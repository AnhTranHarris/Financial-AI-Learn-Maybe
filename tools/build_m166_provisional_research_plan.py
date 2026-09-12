from __future__ import annotations

"""Build an immutable provisional M166-M174 research checkpoint.

The checkpoint permits research computation only.  It does not satisfy M166
production admission and cannot certify M174 or feed M185 until final M165
calibration is independently admitted.
"""

import argparse
import json
from pathlib import Path
import subprocess

from dusty.broker_calibration import calibrate_broker_economics
from dusty.m165_observation_custody import M165ObservationCustody
from dusty.provisional_research import ProvisionalResearchPlan


PROTOCOL = "dusty-m166-m174-provisional-research-plan-builder-v1"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def _sha(value: object, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _qualification_manifest(plan: dict[str, object], lane_id: str) -> dict[str, object]:
    rows = plan.get("manifests")
    if not isinstance(rows, list):
        raise ValueError("qualification plan lacks manifests")
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("lane_id", "")).lower() == lane_id.lower()]
    if len(matches) != 1:
        raise ValueError("qualification plan must contain exactly one requested lane")
    return matches[0]


def build_payload(
    *,
    expected_head: str,
    lane_id: str,
    qualification: dict[str, object],
    discovery: dict[str, object],
    custody_summary: dict[str, object],
    calibration_fingerprint: str,
) -> dict[str, object]:
    lane = str(lane_id).strip().lower()
    manifest = _qualification_manifest(qualification, lane)
    strategy = _sha(manifest.get("strategy_hash"), "qualification strategy")

    if discovery.get("status") != "unique_candidate" or int(discovery.get("identity_set_count", 0) or 0) != 1:
        raise PermissionError("provisional planning requires exactly one discovered research identity set")
    identity_sets = discovery.get("identity_sets")
    if not isinstance(identity_sets, list) or len(identity_sets) != 1 or not isinstance(identity_sets[0], dict):
        raise ValueError("discovery identity set malformed")
    identity = identity_sets[0]
    if str(discovery.get("lane_id", "")).lower() != lane:
        raise ValueError("discovery lane identity drift")
    if _sha(discovery.get("qualification_strategy_hash"), "discovery strategy") != strategy:
        raise ValueError("discovery strategy does not match qualification strategy")
    discovered_strategy = _sha(identity.get("strategy_fingerprint"), "research strategy")
    if discovered_strategy != strategy:
        raise ValueError("research strategy does not match qualification strategy")

    calibration = custody_summary.get("calibration")
    if not isinstance(calibration, dict):
        raise ValueError("custody summary lacks calibration")
    calibration_fp = _sha(calibration_fingerprint, "current M165 calibration")
    observation_count = int(custody_summary.get("observation_count", 0) or 0)
    distinct_days = int(custody_summary.get("distinct_days", 0) or 0)
    if observation_count < 1 or distinct_days < 1:
        raise PermissionError("provisional research requires genuine M165 broker observations")

    plan = ProvisionalResearchPlan(
        lane_id=lane,
        strategy_fingerprint=discovered_strategy,
        dataset_fingerprint=_sha(identity.get("dataset_fingerprint"), "research dataset"),
        parameter_fingerprint=_sha(identity.get("parameter_fingerprint"), "research parameters"),
        current_calibration_fingerprint=calibration_fp,
        current_observation_count=observation_count,
        current_distinct_days=distinct_days,
    )
    return {
        "protocol": PROTOCOL,
        "status": "provisional_research_ready",
        "source_commit": expected_head,
        "qualification_manifest_fingerprint": str(manifest.get("manifest_fingerprint", "")),
        "qualification_strategy_hash": strategy,
        "current_m165_status": str(calibration.get("status", "")),
        "current_m165_calibration_fingerprint": calibration_fp,
        "plan_fingerprint": plan.fingerprint,
        "plan": plan.payload,
        "research_identity_sources": identity.get("sources", []),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build provisional M166-M174 research plan")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--discovery", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--lane-id", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = str(args.expected_head).strip().lower()
    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    qualification_path = Path(args.qualification_plan).resolve()
    discovery_path = Path(args.discovery).resolve()
    database = Path(args.database).resolve()
    for path in (qualification_path, discovery_path, database):
        if not path.is_file():
            raise FileNotFoundError(path)

    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
    with M165ObservationCustody(database) as store:
        rows = store.load_all()
        custody = store.summary_payload()
    if not rows:
        raise PermissionError("provisional research requires genuine M165 custody rows")
    calibration = calibrate_broker_economics(
        rows,
        broker_profile_fingerprint=rows[0].broker_profile_fingerprint,
        symbol=rows[0].symbol,
    )

    payload = build_payload(
        expected_head=expected,
        lane_id=args.lane_id,
        qualification=qualification,
        discovery=discovery,
        custody_summary=custody,
        calibration_fingerprint=calibration.fingerprint,
    )
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
