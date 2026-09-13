from __future__ import annotations

from datetime import datetime, timezone
import unittest

from dusty.experience import TradeSide
from dusty.m166_semantic_remediation import build_semantic_child
from dusty.reconstruction_semantics import ClauseActivation, ReconstructionSemanticAssessment
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExitPlan, GroupMode, RuleGroup, StrategySpecV2
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)

NOW = datetime(2026, 9, 12, tzinfo=timezone.utc)
H = lambda ch: ch * 64


def parent() -> StrategyReconstruction:
    spec = StrategySpecV2(
        strategy_id="parent",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((
            Clause("return_1", RuleOp.GT, 0.01),
            Clause("rsi", RuleOp.LT, 30.0),
            Clause("sma", RuleOp.GT, 200.0),
        ), GroupMode.ALL),),
        exit_plan=ExitPlan("pct:0.01", "pct:0.01", "off", "off", 4),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
    )
    return StrategyReconstruction(
        H("a"), "dusty-research", "http://localhost/dusty/research-seed", H("b"), H("c"),
        "Momentum pullback", ("EURUSD",), "M15", spec,
        (
            ReconstructionRule("hypothesis.entry.0.0", "return_1 gt 0.01", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("hypothesis.entry.0.1", "rsi lt 30.0", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("hypothesis.entry.0.2", "sma gt 200.0", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("research_family", "Momentum pullback", ReconstructionRuleBasis.SOURCE_DECLARED),
        ),
        ("entry_logic",), ReconstructionActor.DUSTY_RESEARCH, H("d"), NOW,
    )


def assessment() -> ReconstructionSemanticAssessment:
    return ReconstructionSemanticAssessment(
        100, 80, 0,
        (
            ClauseActivation(0, 0, "return_1", "gt", 0.01, 80, 1, -0.01, 0.012),
            ClauseActivation(0, 1, "rsi", "lt", 30.0, 80, 7, 10.0, 90.0),
            ClauseActivation(0, 2, "sma", "gt", 200.0, 80, 0, 1.01, 1.18),
        ),
        "entry logic never activates; dead clauses=g0.c2:sma:gt:200.0",
        "dead_reconstruction",
    )


class SemanticRemediationTests(unittest.TestCase):
    def test_removes_only_empirically_dead_hypothesis_clause(self) -> None:
        base = parent()
        child, rules, receipt = build_semantic_child(base, assessment(), audit_fingerprint=H("e"))
        clauses = child.entry_groups[0].clauses
        self.assertEqual(tuple((c.feature, c.op.value, c.value) for c in clauses), (
            ("return_1", "gt", 0.01), ("rsi", "lt", 30.0),
        ))
        self.assertNotEqual(child.strategy_hash, base.candidate_spec.strategy_hash)
        self.assertEqual(receipt.removed_clause_coordinates, ((0, 2),))
        self.assertTrue(any(r.name == "remediation.semantic_dead_clause.0.2" for r in rules))
        self.assertFalse(any(r.name == "hypothesis.entry.0.2" for r in rules))

    def test_refuses_non_dead_assessment(self) -> None:
        row = assessment()
        not_dead = ReconstructionSemanticAssessment(
            row.total_rows, row.session_eligible_rows, 1, row.clauses,
            "activates", "activatable",
        )
        with self.assertRaisesRegex(ValueError, "dead_reconstruction"):
            build_semantic_child(parent(), not_dead, audit_fingerprint=H("e"))

    def test_refuses_dead_clause_without_hypothesis_provenance(self) -> None:
        base = parent()
        bad = StrategyReconstruction(
            base.proposal_fingerprint, base.source_id, base.source_url, base.source_content_sha256,
            base.source_family_fingerprint, base.title, base.symbols, base.timeframe, base.candidate_spec,
            tuple(r for r in base.rules if r.name != "hypothesis.entry.0.2"),
            base.unresolved_source_rules, base.actor, base.actor_fingerprint, base.created_at,
        )
        with self.assertRaisesRegex(ValueError, "provenance"):
            build_semantic_child(bad, assessment(), audit_fingerprint=H("e"))


if __name__ == "__main__":
    unittest.main()
