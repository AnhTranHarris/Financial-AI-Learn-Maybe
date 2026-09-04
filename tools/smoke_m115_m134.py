from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from dusty.quant_reviewer import QuantReviewRequest, build_quant_prompt_payload
from dusty.research_brain import (
    ResearchMetrics,
    ResearchSchool,
    evaluate_school,
    human_durable_priors,
)
from dusty.research_runtime import (
    BlackboardItem,
    BlackboardKind,
    ResearchBlackboard,
    ResearchStage,
    SQLiteResearchCycleStore,
    heartbeat,
)
from dusty.source_intake import default_source_policies


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Local M115-M134 research-brain smoke")
    parser.add_argument("--work-root", required=True)
    args = parser.parse_args()

    work_root = Path(args.work_root).resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    db_path = work_root / "m115-m134-cycle.sqlite3"
    if db_path.exists():
        db_path.unlink()

    priors = human_durable_priors()
    if len(priors) != 20 or len({item.prior_id for item in priors}) != 20:
        raise RuntimeError("human_priors_not_exactly_twenty_unique")

    policies = default_source_policies()
    policy_map = {item.source_id: item for item in policies}
    if not policy_map["forexfactory-calendar"].automated_acquisition_allowed:
        raise RuntimeError("calendar_policy_should_allow_structured_acquisition")
    if policy_map["myfxbook"].automated_acquisition_allowed:
        raise RuntimeError("myfxbook_policy_must_remain_manual_review")

    now = datetime.now(timezone.utc)
    item = BlackboardItem(
        BlackboardKind.LESSON,
        "m115-m134-local-smoke",
        _sha("m115-m134-local-smoke"),
    )
    board = ResearchBlackboard("m115-m134-local-smoke", now, (item,))

    store = SQLiteResearchCycleStore(db_path)
    try:
        stages = []
        for _ in range(10):
            beat = heartbeat(store, board, now=now, completed_job_fingerprints=(item.fingerprint,))
            stages.append(beat.checkpoint.stage)
        if stages[0] is not ResearchStage.ACQUIRE or stages[-1] is not ResearchStage.COMPLETE:
            raise RuntimeError("heartbeat_did_not_reach_complete")
        if not store.integrity_ok():
            raise RuntimeError("research_cycle_store_integrity_failed")
    finally:
        store.close()

    reopened = SQLiteResearchCycleStore(db_path)
    try:
        latest = reopened.latest(board.cycle_id)
        history = tuple(reopened.iter_history(board.cycle_id))
        if latest is None or latest.stage is not ResearchStage.COMPLETE or len(history) != 10:
            raise RuntimeError("research_cycle_restart_proof_failed")
        if not reopened.integrity_ok():
            raise RuntimeError("reopened_research_cycle_store_integrity_failed")
    finally:
        reopened.close()

    metrics = ResearchMetrics(
        sample_count=120,
        oos_expectancy=0.001,
        cost_stress_expectancy=0.0005,
        max_drawdown_fraction=0.08,
        walk_forward_efficiency=0.75,
        parameter_stable=True,
        constitution_compliant=True,
        forward_sample_count=30,
        forward_expectancy=0.0004,
        entries_per_hour=0.5,
        resource_seconds=20.0,
    )
    decisions = [evaluate_school(school, metrics) for school in ResearchSchool]
    if not all(item.passed for item in decisions):
        raise RuntimeError("a1_a2_a3_positive_control_failed")

    evidence_hash = _sha("forecast-evidence")
    request = QuantReviewRequest(
        request_id="m115-m134-smoke",
        model_tag="qwen3:1.7b",
        model_digest=_sha("qwen3:1.7b-local-smoke"),
        forecast_fingerprints=(evidence_hash,),
        strategy_fingerprints=(),
        evidence_fingerprints=(),
        scorecard_text="research-only smoke",
        question="Classify supplied research evidence only.",
    )
    prompt = build_quant_prompt_payload(request)
    if "no_trade_authority" not in prompt["constraints"]:
        raise RuntimeError("quant_reviewer_authority_boundary_missing")

    result = {
        "event": "m115_m134_local_smoke_complete",
        "status": "pass",
        "priors": len(priors),
        "source_policies": len(policies),
        "heartbeat_records": 10,
        "final_stage": ResearchStage.COMPLETE.name.lower(),
        "schools": {item.school.value: item.passed for item in decisions},
        "blackboard_fingerprint": board.fingerprint,
        "db_path": str(db_path),
        "authority": {
            "broker_write": False,
            "entry_veto": False,
            "promotion": False,
            "risk_override": False,
        },
    }
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
