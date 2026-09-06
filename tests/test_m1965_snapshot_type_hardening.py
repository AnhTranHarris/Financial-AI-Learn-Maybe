from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp
from dusty.source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_library_snapshot import load_reconstruction_library, write_reconstruction_library
from dusty.trading_skills import ReconstructionActor, ReconstructionRule, ReconstructionRuleBasis, reconstruct_strategy


NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64


def reconstruction():
    proposal = StrategyProposal(
        "external:strict-json",
        SourceSnapshot("myfxbook", "https://www.myfxbook.com/strategies/strict/1", NOW - timedelta(days=1), H("a"), SourceAccess.MANUAL_REVIEW, False),
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.PARTIAL,
        "Strict JSON test",
        symbols=("EURUSD",),
        timeframes=("M15",),
        declared_rules=(("concept", "trend"),),
        unresolved=("entry_logic",),
    )
    spec = StrategySpecV2(
        "strict-json-v1",
        TradeSide.LONG,
        (RuleGroup((Clause("rsi", RuleOp.GE, 55.0),)),),
        ExitPlan("atr:2", "rr:2", max_hold_steps=8),
        15,
        120,
    )
    return reconstruct_strategy(
        proposal,
        candidate_spec=spec,
        symbols=("EURUSD",),
        timeframe="M15",
        rules=(
            ReconstructionRule("concept", "trend", ReconstructionRuleBasis.SOURCE_DECLARED),
            ReconstructionRule("entry_logic", "RSI >= 55", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ),
        actor=ReconstructionActor.DUSTY_RESEARCH,
        actor_fingerprint=H("b"),
        created_at=NOW,
    )


def rewrite(path: Path, payload: object, *, allow_nan: bool = False) -> str:
    content = (json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=allow_nan) + "\n").encode("utf-8")
    path.write_bytes(content)
    return sha256(content).hexdigest()


class M1965SnapshotTypeHardeningTests(unittest.TestCase):
    def test_string_integer_is_not_coerced(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            write_reconstruction_library(path, (reconstruction(),))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["reconstructions"][0]["candidate_spec"]["decision_timeframe_minutes"] = "15"
            digest = rewrite(path, payload)
            with self.assertRaisesRegex(ValueError, "decision_timeframe_minutes must be an integer"):
                load_reconstruction_library(path, digest)

    def test_number_is_not_coerced_to_string(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            write_reconstruction_library(path, (reconstruction(),))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["reconstructions"][0]["candidate_spec"]["strategy_id"] = 123
            digest = rewrite(path, payload)
            with self.assertRaisesRegex(ValueError, "strategy_id must be a string"):
                load_reconstruction_library(path, digest)

    def test_nonfinite_json_is_rejected_before_dataclass_construction(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            write_reconstruction_library(path, (reconstruction(),))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["reconstructions"][0]["candidate_spec"]["cost_bps"] = float("nan")
            digest = rewrite(path, payload, allow_nan=True)
            with self.assertRaisesRegex(ValueError, "strict UTF-8 JSON"):
                load_reconstruction_library(path, digest)

    def test_boolean_is_not_accepted_as_numeric_cost(self) -> None:
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            write_reconstruction_library(path, (reconstruction(),))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["reconstructions"][0]["candidate_spec"]["cost_bps"] = True
            digest = rewrite(path, payload)
            with self.assertRaisesRegex(ValueError, "cost_bps must be numeric"):
                load_reconstruction_library(path, digest)


if __name__ == "__main__":
    unittest.main()
