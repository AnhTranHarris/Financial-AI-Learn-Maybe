from __future__ import annotations

"""Build a sidecar M166 candidate after removing an unsupported Ollama event hypothesis.

This tool is intentionally broker-free and append-only.  It never modifies the
input Strategy Estate or frozen bar dataset.  It writes a new sidecar Estate,
research identity, provisional research plan, and remediation receipt that can be
fed to the existing M166-M173 provisional runner.
"""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

from dusty.m166_research_identity import M166ResearchIdentity, dataset_fingerprint, parameter_fingerprint
from dusty.m166_variant_remediation import derive_event_hypothesis_variant
from dusty.mt5worker import MT5Bar
from dusty.provisional_research import ProvisionalResearchPlan
from dusty.strategy_estate import load_strategy_estate
from dusty.strategy_library_snapshot import reconstruction_library_bytes

UTC = timezone.utc
PROTOCOL = "dusty-m166-event-hypothesis-variant-builder-v1"


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str) + "\n").encode("utf-8")


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")).hexdigest()


def _sha(value: object, label: str) -> str:
    rendered = str(value or "").strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256")
    return rendered


def _git_sha(value: object, label: str) -> str:
    rendered = str(value or "").strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires full 40-character Git SHA")
    return rendered


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def _read_json(path: Path, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _load_bars(path: Path) -> tuple[MT5Bar, ...]:
    rows: list[MT5Bar] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"dataset line {number} is invalid JSON") from exc
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
    if any(current.at <= previous.at for previous, current in zip(rows, rows[1:])):
        raise ValueError("frozen dataset must be strictly chronological")
    return tuple(rows)


def _atomic_exact(path: Path, content: bytes) -> None:
    """Create an immutable checkpoint or verify an exact existing checkpoint."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise RuntimeError(f"existing checkpoint differs from deterministic result: {path}")
        return
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _assert_authority_false(value: object, label: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{label} authority missing")
    for key in ("broker_write", "live_write", "custody_write", "promotion", "retry", "risk_override"):
        if value.get(key) is not False:
            raise PermissionError(f"{label} authority {key} must be false")


def _requirement_fingerprint(requirement: dict[str, object]) -> str:
    stored = _sha(requirement.get("requirement_fingerprint"), "requirement fingerprint")
    body = dict(requirement)
    body.pop("requirement_fingerprint", None)
    computed = _digest(body)
    if computed != stored:
        raise ValueError("event requirement fingerprint mismatch")
    return stored


def main() -> int:
    parser = argparse.ArgumentParser(description="Build sidecar event-hypothesis-remediated M166 artifacts")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--parent-identity", required=True)
    parser.add_argument("--parent-plan", required=True)
    parser.add_argument("--event-requirement", required=True)
    parser.add_argument("--strategy-estate", required=True)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = _git_sha(args.expected_head, "expected head")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    dataset_path = Path(args.dataset).resolve()
    identity_path = Path(args.parent_identity).resolve()
    plan_path = Path(args.parent_plan).resolve()
    requirement_path = Path(args.event_requirement).resolve()
    estate_path = Path(args.strategy_estate).resolve()
    for path in (dataset_path, identity_path, plan_path, requirement_path, estate_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    parent_identity = _read_json(identity_path, "parent identity")
    parent_plan = _read_json(plan_path, "parent provisional plan")
    requirement = _read_json(requirement_path, "event requirement")
    _assert_authority_false(parent_identity.get("authority"), "parent identity")
    _assert_authority_false(parent_plan.get("authority"), "parent plan")
    _assert_authority_false(requirement.get("authority"), "event requirement")
    requirement_fp = _requirement_fingerprint(requirement)

    parent_recon_fp = _sha(parent_identity.get("reconstruction_fingerprint"), "parent reconstruction")
    parent_strategy_fp = _sha(parent_identity.get("strategy_fingerprint"), "parent strategy")
    parent_parameter_fp = _sha(parent_identity.get("parameter_fingerprint"), "parent parameters")
    parent_dataset_fp = _sha(parent_identity.get("dataset_fingerprint"), "parent dataset")
    parent_source_commit = _git_sha(parent_identity.get("source_commit"), "parent identity source commit")

    rows = load_strategy_estate(estate_path)
    matches = [row for row in rows if row.fingerprint == parent_recon_fp and row.candidate_spec.strategy_hash == parent_strategy_fp]
    if len(matches) != 1:
        raise RuntimeError("Strategy Estate does not contain exactly one frozen parent reconstruction")
    parent = matches[0]
    if parameter_fingerprint(parent.candidate_spec) != parent_parameter_fp:
        raise RuntimeError("parent Strategy Estate parameter identity drift")

    metadata = parent_identity.get("dataset_metadata")
    if not isinstance(metadata, dict):
        raise ValueError("parent identity dataset_metadata missing")
    symbol = str(metadata.get("symbol", "")).strip().upper()
    timeframe = str(metadata.get("timeframe", "")).strip().upper()
    if not symbol or not timeframe:
        raise ValueError("parent dataset symbol/timeframe missing")
    expected_file_sha = _sha(parent_identity.get("dataset_file_sha256"), "parent dataset file")
    actual_file_sha = _sha256_file(dataset_path)
    if actual_file_sha != expected_file_sha:
        raise RuntimeError("frozen dataset file SHA-256 differs from parent identity")
    bars = _load_bars(dataset_path)
    actual_dataset_fp = dataset_fingerprint(symbol=symbol, timeframe=timeframe, bars=bars)
    if actual_dataset_fp != parent_dataset_fp:
        raise RuntimeError("frozen dataset fingerprint differs from parent identity")

    parent_plan_body = parent_plan.get("plan")
    if not isinstance(parent_plan_body, dict):
        raise ValueError("parent provisional plan body missing")
    for key, expected_value in (
        ("strategy_fingerprint", parent_strategy_fp),
        ("dataset_fingerprint", parent_dataset_fp),
        ("parameter_fingerprint", parent_parameter_fp),
    ):
        if parent_plan_body.get(key) != expected_value:
            raise RuntimeError(f"parent provisional plan {key} drift")
    lane_id = str(parent_identity.get("lane_id", "")).strip().lower()
    if not lane_id or str(parent_plan_body.get("lane_id", "")).strip().lower() != lane_id:
        raise RuntimeError("parent lane identity drift")

    variant = derive_event_hypothesis_variant(
        parent,
        parent_parameter_fingerprint=parent_parameter_fp,
        requirement=requirement,
    )
    variant_reconstruction = variant.reconstruction
    variant_strategy_fp = variant_reconstruction.candidate_spec.strategy_hash
    variant_parameter_fp = parameter_fingerprint(variant_reconstruction.candidate_spec)
    if variant_parameter_fp == parent_parameter_fp:
        raise RuntimeError("event remediation failed to change parameter identity")

    ids = {row.candidate_spec.strategy_id for row in rows}
    if variant_reconstruction.candidate_spec.strategy_id in ids:
        raise RuntimeError("variant strategy_id collides with parent Strategy Estate")
    sidecar_rows = tuple(rows) + (variant_reconstruction,)
    sidecar_bytes = reconstruction_library_bytes(sidecar_rows)

    new_identity = M166ResearchIdentity(
        lane_id,
        variant_strategy_fp,
        parent_dataset_fp,
        variant_parameter_fp,
        dict(metadata),
    )
    identity_payload = new_identity.payload | {
        "builder_protocol": PROTOCOL,
        "source_commit": expected,
        "parent_source_commit": parent_source_commit,
        "parent_reconstruction_fingerprint": parent_recon_fp,
        "parent_strategy_fingerprint": parent_strategy_fp,
        "parent_parameter_fingerprint": parent_parameter_fp,
        "reconstruction_fingerprint": variant_reconstruction.fingerprint,
        "remediation_fingerprint": variant.fingerprint,
        "event_requirement_fingerprint": requirement_fp,
        "dataset_file": str(dataset_path),
        "dataset_file_sha256": actual_file_sha,
        "requested_start_utc": parent_identity.get("requested_start_utc"),
        "requested_end_utc_exclusive": parent_identity.get("requested_end_utc_exclusive"),
        "lookback_days": parent_identity.get("lookback_days"),
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "research_execution": False,
            "promotion": False,
            "retry": False,
            "risk_override": False,
        },
    }

    calibration_fp = _sha(parent_plan_body.get("current_calibration_fingerprint"), "parent calibration")
    observation_count = int(parent_plan_body.get("current_observation_count", 0) or 0)
    distinct_days = int(parent_plan_body.get("current_distinct_days", 0) or 0)
    if observation_count < 1 or distinct_days < 1:
        raise PermissionError("variant provisional plan requires genuine parent M165 observations")
    provisional = ProvisionalResearchPlan(
        lane_id,
        variant_strategy_fp,
        parent_dataset_fp,
        variant_parameter_fp,
        calibration_fp,
        observation_count,
        distinct_days,
    )
    plan_payload = {
        "protocol": PROTOCOL,
        "status": "provisional_research_ready",
        "source_commit": expected,
        "parent_plan_fingerprint": parent_plan.get("plan_fingerprint"),
        "parent_strategy_fingerprint": parent_strategy_fp,
        "variant_reconstruction_fingerprint": variant_reconstruction.fingerprint,
        "remediation_fingerprint": variant.fingerprint,
        "current_m165_status": parent_plan.get("current_m165_status"),
        "current_m165_calibration_fingerprint": calibration_fp,
        "plan_fingerprint": provisional.fingerprint,
        "plan": provisional.payload,
        "production_semantics": {
            "m165_30_observations_3_days_still_required": True,
            "m166_production_admission_granted": False,
            "m174_production_certification_granted": False,
            "m185_eligible": False,
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

    root = Path(args.output_root).resolve()
    sidecar_path = root / "strategy-estate-eventless.json"
    variant_identity_path = root / "m166-research-identity-eventless.json"
    variant_plan_path = root / "m166-m174-provisional-research-plan-eventless.json"
    receipt_path = root / "m166-event-hypothesis-remediation.json"

    receipt_body = variant.payload | {
        "builder_protocol": PROTOCOL,
        "source_commit": expected,
        "parent_source_commit": parent_source_commit,
        "parent_identity_file_sha256": _sha256_file(identity_path),
        "parent_plan_file_sha256": _sha256_file(plan_path),
        "event_requirement_file_sha256": _sha256_file(requirement_path),
        "parent_strategy_estate_file_sha256": _sha256_file(estate_path),
        "frozen_dataset_file_sha256": actual_file_sha,
        "frozen_dataset_fingerprint": parent_dataset_fp,
        "variant_parameter_fingerprint": variant_parameter_fp,
        "sidecar_strategy_estate_sha256": sha256(sidecar_bytes).hexdigest(),
        "variant_identity_fingerprint": new_identity.fingerprint,
        "variant_plan_fingerprint": provisional.fingerprint,
        "sidecar_paths": {
            "strategy_estate": str(sidecar_path),
            "identity": str(variant_identity_path),
            "plan": str(variant_plan_path),
        },
    }
    receipt_payload = receipt_body | {"receipt_fingerprint": _digest(receipt_body)}

    _atomic_exact(sidecar_path, sidecar_bytes)
    _atomic_exact(variant_identity_path, _canonical(identity_payload))
    _atomic_exact(variant_plan_path, _canonical(plan_payload))
    _atomic_exact(receipt_path, _canonical(receipt_payload))

    if estate_path.read_bytes() == sidecar_path.read_bytes():
        raise RuntimeError("sidecar Strategy Estate unexpectedly equals parent Estate")
    # Recheck input bytes after all outputs: the parent artifacts are evidence and
    # must remain unchanged by this builder.
    if _sha256_file(dataset_path) != actual_file_sha:
        raise RuntimeError("frozen dataset changed during variant build")
    if _sha256_file(estate_path) != receipt_payload["parent_strategy_estate_file_sha256"]:
        raise RuntimeError("parent Strategy Estate changed during variant build")

    summary = {
        "protocol": PROTOCOL,
        "source_commit": expected,
        "parent_strategy_fingerprint": parent_strategy_fp,
        "variant_strategy_fingerprint": variant_strategy_fp,
        "parent_parameter_fingerprint": parent_parameter_fp,
        "variant_parameter_fingerprint": variant_parameter_fp,
        "parent_reconstruction_fingerprint": parent_recon_fp,
        "variant_reconstruction_fingerprint": variant_reconstruction.fingerprint,
        "dataset_fingerprint_unchanged": parent_dataset_fp,
        "event_exclusion_minutes": {"parent": parent.candidate_spec.event_exclusion_minutes, "variant": 0},
        "m165_checkpoint": {"observations": observation_count, "days": distinct_days, "calibration_fingerprint": calibration_fp},
        "outputs": {
            "strategy_estate": str(sidecar_path),
            "identity": str(variant_identity_path),
            "plan": str(variant_plan_path),
            "receipt": str(receipt_path),
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
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


broker_write_authority = False
live_write_authority = False
custody_write_authority = False
promotion_authority = False
retry_authority = False
risk_override_authority = False
