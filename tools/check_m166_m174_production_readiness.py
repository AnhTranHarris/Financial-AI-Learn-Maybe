from __future__ import annotations

"""Read-only readiness manifest for the M166-M174 production qualification chain.

This tool does not run research, mutate custody, promote a strategy, or touch a broker.
It proves the local prerequisites that must exist before the deterministic M166-M174
qualification stages may be started, and reports exact blockers without fabricating
missing downstream evidence.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess

from dusty.m165_observation_custody import M165ObservationCustody


PROTOCOL = "dusty-m166-m174-production-readiness-v1"
REQUIRED_STAGES = (
    "m166_walk_forward",
    "m167_purged_validation",
    "m168_parameter_stability",
    "m169_regime_torture",
    "m170_cost_torture",
    "m171_forward_decay",
    "m172_tail_risk",
    "m173_strategy_dependency",
    "m174_robustness",
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def _sha256_file(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _manifest_for_lane(plan: dict[str, object], lane_id: str) -> dict[str, object] | None:
    manifests = plan.get("manifests", [])
    if not isinstance(manifests, list):
        return None
    for row in manifests:
        if isinstance(row, dict) and str(row.get("lane_id", "")) == lane_id:
            return row
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M166-M174 production readiness probe")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--lane-id", required=True)
    parser.add_argument("--strategy-estate")
    parser.add_argument("--output")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = args.expected_head.strip().lower()
    plan_path = Path(args.qualification_plan).resolve()
    database = Path(args.database).resolve()
    estate_path = Path(args.strategy_estate).resolve() if args.strategy_estate else Path.home() / "AppData" / "Local" / "DustyDragon" / "strategy-estate" / "reconstructions.json"
    lane_id = args.lane_id.strip()

    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")
    if not lane_id:
        raise ValueError("lane id required")
    if not plan_path.is_file():
        raise FileNotFoundError(plan_path)
    if not database.is_file():
        raise FileNotFoundError(database)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise ValueError("qualification plan must be a JSON object")
    manifest = _manifest_for_lane(plan, lane_id)

    with M165ObservationCustody(database) as store:
        custody = store.summary_payload()

    calibration = custody.get("calibration") or {}
    if not isinstance(calibration, dict):
        calibration = {}
    observation_count = int(custody.get("observation_count", 0) or 0)
    distinct_days = int(custody.get("distinct_days", 0) or 0)
    sides = sorted(str(value) for value in custody.get("sides", []))
    calibration_status = str(calibration.get("status", ""))

    blockers: list[str] = []
    if manifest is None:
        blockers.append("qualification_lane_missing")
    if not estate_path.is_file():
        blockers.append("strategy_estate_missing")
    if calibration_status != "calibrated":
        blockers.append("m165_not_calibrated")
    if observation_count < 30:
        blockers.append("m165_observation_count_below_30")
    if distinct_days < 3:
        blockers.append("m165_distinct_days_below_3")
    if set(sides) != {"buy", "sell"}:
        blockers.append("m165_both_sides_required")

    ready = not blockers
    result = {
        "protocol": PROTOCOL,
        "source_commit": expected,
        "status": "ready" if ready else "blocked",
        "ready": ready,
        "blockers": blockers,
        "lane_id": lane_id,
        "qualification_plan": {
            "path": str(plan_path),
            "sha256": _sha256_file(plan_path),
            "plan_fingerprint": str(plan.get("plan_fingerprint", "")),
            "source_commit": str(plan.get("source_commit", "")),
            "manifest_found": manifest is not None,
            "manifest_fingerprint": str((manifest or {}).get("manifest_fingerprint", "")),
            "strategy_hash": str((manifest or {}).get("strategy_hash", "")),
            "symbol": str((manifest or {}).get("symbol", "")),
        },
        "strategy_estate": {
            "path": str(estate_path),
            "present": estate_path.is_file(),
            "sha256": _sha256_file(estate_path) if estate_path.is_file() else None,
        },
        "m165": {
            "observation_count": observation_count,
            "distinct_days": distinct_days,
            "sides": sides,
            "calibration_status": calibration_status,
            "broker_profile_fingerprint": str(custody.get("broker_profile_fingerprint", "")),
        },
        "required_stages": list(REQUIRED_STAGES),
        "identity_contract": {
            "m166": "CALIBRATED M165 >=30 observations >=3 days + exact symbol + walk-forward plan identity",
            "m167": "one unique purged split per M166 fold + exact dataset provenance",
            "m168": "center parameter identity must equal M166 plan parameter fingerprint",
            "m169": "regime evidence strategy provenance must equal M166 strategy",
            "m170": "cost torture must use admitted M165 calibration and pass",
            "m171": "forward-decay strategy identity must equal M166 strategy",
            "m172": "tail-risk strategy provenance must equal M166 strategy",
            "m173": "dependency matrix must contain production strategy",
            "m174": "SERIOUS_CHALLENGER, zero blockers, exact evidence-fingerprint set",
        },
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "research_execution": False,
            "retry": False,
            "promotion": False,
            "risk_override": False,
        },
    }

    rendered = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
