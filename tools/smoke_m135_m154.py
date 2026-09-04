from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from dusty.research_organism import ResearchOrganism, SQLiteResearchOrganismStore, StageWork
from dusty.research_runtime import BlackboardItem, BlackboardKind, ResearchBlackboard, ResearchStage
from dusty.strategy_lab import ConstraintMode, StrategyConstraint, UserStrategyIntent, compile_user_strategy_intent


def digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", required=True)
    args = parser.parse_args()

    root = Path(args.work_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "research-organism.sqlite3"
    if path.exists():
        path.unlink()

    intent = UserStrategyIntent(
        "SMOKE-USR-NAS-0001",
        "Asia to London/NY expansion",
        "NAS Asia entry with later London/NY expansion research.",
        datetime(2026, 9, 4, 11, tzinfo=timezone.utc),
        ("NAS",),
        ("M15",),
        (
            StrategyConstraint("entry.session", "asia", ConstraintMode.LOCKED),
            StrategyConstraint("entry.trigger", "unknown", ConstraintMode.RESEARCHABLE),
            StrategyConstraint("exit.trigger", "high_volume", ConstraintMode.RESEARCHABLE),
            StrategyConstraint("exit.session", "london_or_new_york", ConstraintMode.LOCKED),
        ),
    )
    genome = compile_user_strategy_intent(intent)
    if genome.origin.value != "user" or genome.rule_map().get("entry.session") != "asia":
        raise RuntimeError("user strategy intent did not compile conservatively")

    kinds = {
        ResearchStage.ACQUIRE: BlackboardKind.SOURCE,
        ResearchStage.FORECAST: BlackboardKind.FORECAST,
        ResearchStage.SCORE: BlackboardKind.SCORECARD,
        ResearchStage.INTAKE: BlackboardKind.STRATEGY,
        ResearchStage.SCREEN: BlackboardKind.EXPERIMENT,
        ResearchStage.EXPERIMENT: BlackboardKind.EXPERIMENT,
        ResearchStage.ATTRIBUTE: BlackboardKind.ATTRIBUTION,
        ResearchStage.REMEMBER: BlackboardKind.LESSON,
    }
    handlers = {
        stage: (
            lambda stage=stage: (
                lambda board: StageWork(
                    items=(BlackboardItem(kinds[stage], stage.name.lower(), digest(f"payload:{stage.name}")),),
                    completed_job_fingerprints=(digest(f"job:{stage.name}"),),
                )
            )
        )()
        for stage in kinds
    }

    tick = [datetime(2026, 9, 4, 12, tzinfo=timezone.utc)]

    def clock() -> datetime:
        value = tick[0]
        tick[0] = value + timedelta(seconds=1)
        return value

    board = ResearchBlackboard("smoke-m135-m154", datetime(2026, 9, 4, 11, tzinfo=timezone.utc), ())
    store = SQLiteResearchOrganismStore(path)
    try:
        result = ResearchOrganism(store, clock=clock).run_until_complete(board, handlers)
        if result.checkpoint.stage is not ResearchStage.COMPLETE:
            raise RuntimeError("research organism did not complete")
        if result.broker_write_authority or result.promotion_authority:
            raise RuntimeError("research organism gained operational authority")
        if not store.integrity_ok():
            raise RuntimeError("research organism SQLite integrity failed")
        final_fingerprint = result.board.fingerprint
    finally:
        store.close()

    reopened = SQLiteResearchOrganismStore(path)
    try:
        latest = reopened.cycle_store.latest("smoke-m135-m154")
        if latest is None or latest.stage is not ResearchStage.COMPLETE:
            raise RuntimeError("research organism did not recover COMPLETE checkpoint")
        restored = reopened.load_board(latest.blackboard_fingerprint)
        if restored.fingerprint != final_fingerprint:
            raise RuntimeError("research organism board fingerprint drifted after reopen")
        replay = ResearchOrganism(reopened, clock=clock).run_until_complete(board, handlers)
        if replay.stages_completed:
            raise RuntimeError("completed organism replay should be idempotent")
        if not reopened.integrity_ok():
            raise RuntimeError("reopened research organism SQLite integrity failed")
    finally:
        reopened.close()

    report = {
        "protocol": "dusty-m135-m154-runtime-smoke-v1",
        "status": "pass",
        "user_strategy_fingerprint": genome.fingerprint,
        "final_blackboard_fingerprint": final_fingerprint,
        "stages": [stage.name for stage in ResearchStage],
        "safety": {
            "mt5_orders": False,
            "broker_credentials": False,
            "broker_write": False,
            "entry_veto": False,
            "promotion": False,
            "risk_override": False,
        },
    }
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
