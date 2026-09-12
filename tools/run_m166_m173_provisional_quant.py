from __future__ import annotations

"""Run the frozen M166-M173 provisional robustness lane without broker I/O."""

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

from dusty.m166_provisional_quant import (
    ProvisionalQuantPolicy,
    build_runtime_bars,
    run_m166,
    run_m167,
    run_m168,
    run_m169,
    run_m171,
    run_m172,
)
from dusty.m166_research_identity import dataset_fingerprint, parameter_fingerprint
from dusty.mt5worker import MT5Bar
from dusty.strategy_estate import load_strategy_estate

UTC = timezone.utc
PROTOCOL = "dusty-m166-m173-provisional-quant-runner-v1"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def _is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if proc.returncode not in {0, 1}:
        raise RuntimeError(proc.stderr.strip() or "git ancestry check failed")
    return proc.returncode == 0


def _atomic_json(path: Path, payload: object) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    h = sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _load_bars(path: Path) -> tuple[MT5Bar, ...]:
    rows: list[MT5Bar] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"dataset line {number} is not an object")
            rows.append(
                MT5Bar(
                    datetime.fromisoformat(str(raw["time"])).astimezone(UTC),
                    float(raw["open"]),
                    float(raw["high"]),
                    float(raw["low"]),
                    float(raw["close"]),
                    int(raw["tick_volume"]),
                    int(raw["spread"]),
                    int(raw["real_volume"]),
                )
            )
    if not rows:
        raise ValueError("frozen dataset is empty")
    return tuple(rows)


def _payload(value: object) -> object:
    if hasattr(value, "payload"):
        return getattr(value, "payload")
    if is_dataclass(value):
        return asdict(value)
    return value


def _json_normalize(value: object) -> object:
    return json.loads(_canonical(value))


def _stage(
    *,
    name: str,
    status: str,
    subject: str,
    inputs: dict[str, object],
    evidence: object | None = None,
    reason: str = "",
) -> dict[str, object]:
    evidence_payload = None if evidence is None else _json_normalize(_payload(evidence))
    payload = {
        "protocol": "dusty-provisional-stage-artifact-v1",
        "stage": name,
        "status": status,
        "subject_fingerprint": subject,
        "inputs": _json_normalize(inputs),
        "evidence": evidence_payload,
        "reason": reason,
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "promotion": False,
            "retry": False,
            "risk_override": False,
        },
    }
    return payload | {"artifact_fingerprint": _digest(payload)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--provisional-plan", required=True)
    parser.add_argument("--strategy-estate", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    dataset_path = Path(args.dataset).resolve()
    identity_path = Path(args.identity).resolve()
    plan_path = Path(args.provisional_plan).resolve()
    estate_path = Path(args.strategy_estate).resolve()
    root = Path(args.output_root).resolve()
    expected = args.expected_head.strip().lower()

    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("repository must be clean")
    for path in (dataset_path, identity_path, plan_path, estate_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    identity = _read_json(identity_path)
    plan = _read_json(plan_path)
    identity_source = str(identity.get("source_commit", "")).strip().lower()
    plan_source = str(plan.get("source_commit", "")).strip().lower()
    if identity_source != plan_source:
        raise RuntimeError("identity and provisional plan source commits differ")
    if len(identity_source) != 40 or any(ch not in "0123456789abcdef" for ch in identity_source):
        raise RuntimeError("input evidence source commit is invalid")
    if not _is_ancestor(repo, identity_source, expected):
        raise RuntimeError("frozen research evidence is not from an ancestor of the runner head")
    if plan.get("status") != "provisional_research_ready":
        raise RuntimeError("provisional plan is not research-ready")

    expected_file_sha = str(identity.get("dataset_file_sha256", "")).lower()
    if _sha256_file(dataset_path) != expected_file_sha:
        raise RuntimeError("frozen dataset file SHA-256 mismatch")
    bars = _load_bars(dataset_path)
    dataset_fp = dataset_fingerprint(
        symbol=str(identity["dataset_metadata"]["symbol"]),
        timeframe=str(identity["dataset_metadata"]["timeframe"]),
        bars=bars,
    )
    if dataset_fp != identity.get("dataset_fingerprint"):
        raise RuntimeError("recomputed dataset fingerprint mismatch")

    recon_fp = str(identity.get("reconstruction_fingerprint", ""))
    strategy_fp = str(identity.get("strategy_fingerprint", ""))
    reconstructions = load_strategy_estate(estate_path)
    matches = [row for row in reconstructions if row.fingerprint == recon_fp and row.candidate_spec.strategy_hash == strategy_fp]
    if len(matches) != 1:
        raise RuntimeError("Strategy Estate does not contain exactly one frozen reconstruction")
    spec = matches[0].candidate_spec
    parameter_fp = parameter_fingerprint(spec)
    if parameter_fp != identity.get("parameter_fingerprint"):
        raise RuntimeError("recomputed parameter fingerprint mismatch")

    plan_body = plan.get("plan")
    if not isinstance(plan_body, dict):
        raise RuntimeError("provisional plan body is missing")
    for key, actual in (
        ("strategy_fingerprint", strategy_fp),
        ("dataset_fingerprint", dataset_fp),
        ("parameter_fingerprint", parameter_fp),
    ):
        if plan_body.get(key) != actual:
            raise RuntimeError(f"provisional plan {key} drift")

    policy = ProvisionalQuantPolicy()
    runtime_bars = build_runtime_bars(bars)
    m166_plan, m166_rows, m166_summary, oos_trades = run_m166(
        spec,
        runtime_bars,
        strategy_fingerprint=strategy_fp,
        dataset_fingerprint=dataset_fp,
        parameter_fp=parameter_fp,
        policy=policy,
    )

    m167_rows = run_m167(
        runtime_bars,
        m166_plan.windows,
        dataset_fingerprint=dataset_fp,
        label_horizon_minutes=spec.intended_horizon_minutes,
    )
    m168_assessment, m168_details = run_m168(
        spec,
        runtime_bars,
        m166_plan.windows,
        center_parameter_fingerprint=parameter_fp,
        center_trades=oos_trades,
        policy=policy,
    )
    m169_assessment, m169_details = run_m169(oos_trades, runtime_bars, policy=policy)
    m171 = run_m171(
        oos_trades,
        strategy_fingerprint=strategy_fp,
        period_start=m166_plan.windows[0].test_start,
        period_end=m166_plan.windows[-1].test_end,
    )
    m172 = run_m172(oos_trades)

    common = {
        "runner_source_commit": expected,
        "evidence_source_commit": identity_source,
        "plan_fingerprint": plan.get("plan_fingerprint"),
        "policy_fingerprint": policy.fingerprint,
        "strategy_fingerprint": strategy_fp,
        "dataset_fingerprint": dataset_fp,
        "parameter_fingerprint": parameter_fp,
    }
    artifacts: dict[str, dict[str, object]] = {}
    artifacts["m166_walk_forward"] = _stage(
        name="m166_walk_forward",
        status="completed",
        subject=m166_plan.fingerprint,
        inputs=common,
        evidence={
            "plan_fingerprint": m166_plan.fingerprint,
            "mode": m166_plan.mode.value,
            "windows": [asdict(row) for row in m166_plan.windows],
            "fold_results": [asdict(row) for row in m166_rows],
            "summary": asdict(m166_summary),
            "oos_trade_count": len(oos_trades),
        },
    )
    artifacts["m167_purged_validation"] = _stage(
        name="m167_purged_validation",
        status="completed",
        subject=_digest(m167_rows),
        inputs=common | {"m166_plan_fingerprint": m166_plan.fingerprint},
        evidence={"splits": list(m167_rows)},
    )
    artifacts["m168_parameter_stability"] = _stage(
        name="m168_parameter_stability",
        status=str(getattr(m168_assessment, "status").value),
        subject=str(getattr(m168_assessment, "fingerprint")),
        inputs=common | {"m166_plan_fingerprint": m166_plan.fingerprint},
        evidence={"assessment": asdict(m168_assessment), "neighbors": list(m168_details)},
    )
    artifacts["m169_regime_torture"] = _stage(
        name="m169_regime_torture",
        status=str(getattr(m169_assessment, "status").value),
        subject=str(getattr(m169_assessment, "fingerprint")),
        inputs=common | {"m166_plan_fingerprint": m166_plan.fingerprint},
        evidence={"assessment": asdict(m169_assessment), "regimes": list(m169_details)},
    )
    artifacts["m170_cost_torture"] = _stage(
        name="m170_cost_torture",
        status="pending",
        subject=str(plan.get("current_m165_calibration_fingerprint")),
        inputs=common | {"calibration_fingerprint": plan.get("current_m165_calibration_fingerprint")},
        reason="M170 requires CALIBRATED M165 evidence; current provisional calibration is insufficient",
    )
    artifacts["m171_forward_decay"] = _stage(
        name="m171_forward_decay",
        status="pending" if m171 is None else str(getattr(m171, "status").value),
        subject=strategy_fp if m171 is None else str(getattr(m171, "fingerprint")),
        inputs=common | {"m166_plan_fingerprint": m166_plan.fingerprint},
        evidence=None if m171 is None else asdict(m171),
        reason="historical provisional OOS baseline is non-positive" if m171 is None else str(getattr(m171, "reason")),
    )
    artifacts["m172_tail_risk"] = _stage(
        name="m172_tail_risk",
        status=str(getattr(m172, "status").value),
        subject=str(getattr(m172, "fingerprint")),
        inputs=common | {"m166_plan_fingerprint": m166_plan.fingerprint},
        evidence=asdict(m172),
    )
    artifacts["m173_strategy_dependency"] = _stage(
        name="m173_strategy_dependency",
        status="pending",
        subject=strategy_fp,
        inputs=common,
        reason="M173 requires at least two independent strategies on an exact synchronized return grid",
    )

    root.mkdir(parents=True, exist_ok=True)
    for stage_name, payload in artifacts.items():
        destination = root / f"{stage_name}.json"
        if destination.exists():
            existing = _read_json(destination)
            if existing != payload:
                raise RuntimeError(f"existing {stage_name} checkpoint differs from deterministic result")
        else:
            _atomic_json(destination, payload)

    incomplete_statuses = {"pending", "insufficient", "missing_forward", "insufficient_forward"}
    complete = sum(1 for row in artifacts.values() if row["status"] not in incomplete_statuses)
    manifest_payload = {
        "protocol": PROTOCOL,
        "runner_source_commit": expected,
        "evidence_source_commit": identity_source,
        "lane_id": identity.get("lane_id"),
        "plan_fingerprint": plan.get("plan_fingerprint"),
        "policy": policy.payload,
        "policy_fingerprint": policy.fingerprint,
        "identity": {
            "strategy_fingerprint": strategy_fp,
            "dataset_fingerprint": dataset_fp,
            "parameter_fingerprint": parameter_fp,
        },
        "stage_artifacts": {name: row["artifact_fingerprint"] for name, row in artifacts.items()},
        "stage_status": {name: row["status"] for name, row in artifacts.items()},
        "completed_or_measured_stages": complete,
        "production_semantics": {
            "m166_production_admission_granted": False,
            "m174_production_certification_granted": False,
            "m185_eligible": False,
            "m165_30_observations_3_days_still_required": True,
            "final_calibration_revalidation": ["m170_cost_torture", "m174_robustness"],
        },
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "promotion": False,
            "retry": False,
            "risk_override": False,
        },
    }
    manifest = manifest_payload | {"manifest_fingerprint": _digest(manifest_payload)}
    manifest_path = root / "provisional-quant-manifest.json"
    if manifest_path.exists():
        if _read_json(manifest_path) != manifest:
            raise RuntimeError("existing provisional manifest differs from deterministic result")
    else:
        _atomic_json(manifest_path, manifest)

    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
