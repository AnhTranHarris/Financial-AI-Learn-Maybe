from __future__ import annotations

from datetime import datetime, timezone
import unittest

from dusty.controlled_evolution import EvolutionAction
from dusty.estate_a1_refinement import (
    compile_reconstruction_genome,
    materialize_plan_challengers,
    plan_a1_refinement,
    reconstruction_genome,
)
from dusty.experience import TradeSide
from dusty.multitimeframe_a1_campaign import A1CampaignAssessment, A1CampaignStatus
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_lab import ConstraintMode
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 7, 19, 0, tzinfo=UTC)


def reconstruction(*, op: RuleOp = RuleOp.GT) -> StrategyReconstruction:
    spec = StrategySpecV2(
        strategy_id="estate-refinement-test",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((Clause("rsi", op, 50.0),)),),
        exit_plan=ExitPlan(
            stop_rule="atr:2",
            target_rule="rr:2",
            trailing_rule="off",
            max_hold_steps=4,
        ),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
    )
    rules = (
        ReconstructionRule("source.market", "London expansion", ReconstructionRuleBasis.SOURCE_DECLARED),
        ReconstructionRule("hypothesis.entry.0.0", f"rsi {op.value} 50.0", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.direction", "long", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.stop", "atr:2", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.target", "rr:2", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.trailing", "off", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.horizon_minutes", "60", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
    )
    return StrategyReconstruction(
        "a" * 64,
        "test-source",
        "https://example.com/strategy",
        "b" * 64,
        "c" * 64,
        "Estate refinement test",
        ("EURUSD",),
        "M15",
        spec,
        rules,
        (),
        ReconstructionActor.OLLAMA,
        "d" * 64,
        NOW,
    )


def campaign(
    row: StrategyReconstruction,
    status: A1CampaignStatus,
    blockers: tuple[str, ...],
    *,
    trades: int = 0,
    net: float = 0.0,
) -> A1CampaignAssessment:
    return A1CampaignAssessment(
        status=status,
        plan_fingerprint="1" * 64,
        strategy_hash=row.candidate_spec.strategy_hash,
        a1_policy_fingerprint="2" * 64,
        campaign_policy_fingerprint="3" * 64,
        window_count=5,
        promising_window_count=0 if status is not A1CampaignStatus.PROMISING else 5,
        rejected_window_count=5 if status is A1CampaignStatus.REJECTED else 0,
        insufficient_window_count=5 if status is A1CampaignStatus.INSUFFICIENT else 0,
        promising_window_fraction=0.0 if status is not A1CampaignStatus.PROMISING else 1.0,
        positive_window_fraction=0.0 if net <= 0 else 1.0,
        total_trades=trades,
        total_net_pnl=net,
        median_window_net_pnl=net / 5,
        worst_window_net_pnl=net / 5,
        worst_drawdown_fraction=0.01,
        blockers=blockers,
        evidence_fingerprints=("4" * 64,),
    )


class M19614EstateA1RefinementTests(unittest.TestCase):
    def test_genome_adapter_locks_source_and_marks_hypothesis_researchable(self):
        row = reconstruction()
        genome = reconstruction_genome(row)
        constraints = {item.key: item for item in genome.constraints}
        self.assertIs(constraints["source.market"].mode, ConstraintMode.LOCKED)
        self.assertIs(constraints["hypothesis.entry.0.0"].mode, ConstraintMode.RESEARCHABLE)
        self.assertIs(constraints["risk.martingale"].mode, ConstraintMode.FORBIDDEN)

    def test_typed_reconstruction_provenance_mismatch_fails_closed(self):
        row = reconstruction()
        bad_rules = tuple(
            ReconstructionRule(item.name, "rsi gt 99.0", item.basis)
            if item.name == "hypothesis.entry.0.0"
            else item
            for item in row.rules
        )
        bad = StrategyReconstruction(
            row.proposal_fingerprint,
            row.source_id,
            row.source_url,
            row.source_content_sha256,
            row.source_family_fingerprint,
            row.title,
            row.symbols,
            row.timeframe,
            row.candidate_spec,
            bad_rules,
            row.unresolved_source_rules,
            row.actor,
            row.actor_fingerprint,
            row.created_at,
        )
        with self.assertRaises(ValueError):
            reconstruction_genome(bad)

    def test_compiler_reuses_m157_and_standard_feature_registry(self):
        row = reconstruction()
        genome, compiled = compile_reconstruction_genome(row)
        self.assertEqual(compiled.source_genome_fingerprint, genome.fingerprint)
        self.assertTrue(compiled.manifest_ready)
        self.assertFalse(compiled.broker_write_authority)
        self.assertFalse(compiled.promotion_authority)

    def test_insufficient_no_trade_campaign_creates_bounded_m158_challengers(self):
        row = reconstruction()
        result = campaign(
            row,
            A1CampaignStatus.INSUFFICIENT,
            ("insufficient_total_trades", "underlying_window_insufficient"),
        )
        plan = plan_a1_refinement(row, result, maximum_challengers=2)
        self.assertIs(plan.evolution.action, EvolutionAction.CREATE_CHALLENGER)
        self.assertEqual(len(plan.evolution.challengers), 2)
        self.assertTrue(all(len(item.instructions) == 1 for item in plan.evolution.challengers))
        values = [item.instructions[0].new_value for item in plan.evolution.challengers]
        self.assertTrue(all(value.startswith("rsi gt ") for value in values))
        self.assertTrue(all(float(value.split()[-1]) < 50.0 for value in values))

    def test_negative_edge_campaign_tightens_entry_threshold(self):
        row = reconstruction()
        result = campaign(
            row,
            A1CampaignStatus.REJECTED,
            ("aggregate_net_pnl_failed",),
            trades=40,
            net=-10.0,
        )
        plan = plan_a1_refinement(row, result, maximum_challengers=1)
        self.assertIs(plan.evolution.action, EvolutionAction.CREATE_CHALLENGER)
        value = plan.evolution.challengers[0].instructions[0].new_value
        self.assertGreater(float(value.split()[-1]), 50.0)

    def test_promising_campaign_advances_without_mutation(self):
        row = reconstruction()
        result = campaign(row, A1CampaignStatus.PROMISING, (), trades=50, net=10.0)
        plan = plan_a1_refinement(row, result)
        self.assertIs(plan.evolution.action, EvolutionAction.ADVANCE)
        self.assertEqual(plan.evolution.challengers, ())
        self.assertEqual(materialize_plan_challengers(row, plan, created_at=NOW), ())

    def test_unsupported_equality_threshold_stops_research_instead_of_guessing(self):
        row = reconstruction(op=RuleOp.EQ)
        result = campaign(row, A1CampaignStatus.INSUFFICIENT, ("insufficient_total_trades",))
        plan = plan_a1_refinement(row, result)
        self.assertIs(plan.evolution.action, EvolutionAction.STOP_RESEARCH)
        self.assertEqual(plan.evolution.challengers, ())

    def test_materialized_child_preserves_source_declared_rules_and_changes_strategy_hash(self):
        row = reconstruction()
        result = campaign(row, A1CampaignStatus.INSUFFICIENT, ("insufficient_total_trades",))
        plan = plan_a1_refinement(row, result, maximum_challengers=1)
        children = materialize_plan_challengers(row, plan, created_at=NOW)
        self.assertEqual(len(children), 1)
        child = children[0]
        self.assertNotEqual(child.candidate_spec.strategy_hash, row.candidate_spec.strategy_hash)
        parent_source = next(item for item in row.rules if item.basis is ReconstructionRuleBasis.SOURCE_DECLARED)
        child_source = next(item for item in child.rules if item.basis is ReconstructionRuleBasis.SOURCE_DECLARED)
        self.assertEqual(parent_source, child_source)
        self.assertIs(child.actor, ReconstructionActor.DUSTY_RESEARCH)

    def test_refinement_plan_has_no_operational_authority(self):
        row = reconstruction()
        result = campaign(row, A1CampaignStatus.INSUFFICIENT, ("insufficient_total_trades",))
        plan = plan_a1_refinement(row, result)
        self.assertFalse(plan.broker_write_authority)
        self.assertFalse(plan.live_write_authority)
        self.assertFalse(plan.promotion_authority)
        self.assertFalse(plan.risk_override_authority)
        self.assertFalse(plan.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
