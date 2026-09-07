from __future__ import annotations

from datetime import datetime, timezone
import unittest

from dusty.estate_a1_refinement import materialize_plan_challengers, plan_a1_refinement
from dusty.experience import TradeSide
from dusty.multitimeframe_a1_campaign import A1CampaignAssessment, A1CampaignStatus
from dusty.research import Clause, RuleOp
from dusty.strategy_ecosystem_certification import (
    StrategyEcosystemCertificationStatus,
    certify_strategy_ecosystem,
)
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 7, 20, 0, tzinfo=UTC)
ESTATE = "e" * 64


def reconstruction() -> StrategyReconstruction:
    spec = StrategySpecV2(
        strategy_id="m19615-test",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((Clause("rsi", RuleOp.GT, 50.0),)),),
        exit_plan=ExitPlan(stop_rule="atr:2", target_rule="rr:2", trailing_rule="off", max_hold_steps=4),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
    )
    rules = (
        ReconstructionRule("source.market", "London expansion", ReconstructionRuleBasis.SOURCE_DECLARED),
        ReconstructionRule("hypothesis.entry.0.0", "rsi gt 50.0", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
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
        "M196.15 test",
        ("EURUSD",),
        "M15",
        spec,
        rules,
        (),
        ReconstructionActor.OLLAMA,
        "d" * 64,
        NOW,
    )


def campaign(row: StrategyReconstruction, status: A1CampaignStatus, *, windows: int = 5) -> A1CampaignAssessment:
    promising = windows if status is A1CampaignStatus.PROMISING else 0
    insufficient = windows if status is A1CampaignStatus.INSUFFICIENT else 0
    rejected = windows if status is A1CampaignStatus.REJECTED else 0
    return A1CampaignAssessment(
        status,
        "1" * 64,
        row.candidate_spec.strategy_hash,
        "2" * 64,
        "3" * 64,
        windows,
        promising,
        rejected,
        insufficient,
        1.0 if status is A1CampaignStatus.PROMISING else 0.0,
        1.0 if status is A1CampaignStatus.PROMISING else 0.0,
        50 if status is A1CampaignStatus.PROMISING else 0,
        10.0 if status is A1CampaignStatus.PROMISING else 0.0,
        2.0 if status is A1CampaignStatus.PROMISING else 0.0,
        1.0 if status is A1CampaignStatus.PROMISING else 0.0,
        0.01,
        () if status is A1CampaignStatus.PROMISING else ("insufficient_total_trades",),
        ("4" * 64,),
    )


class M19615StrategyEcosystemCertificationTests(unittest.TestCase):
    def test_nonpromising_parent_and_retested_child_can_close_m196_without_strategy_qualification(self):
        parent = reconstruction()
        parent_campaign = campaign(parent, A1CampaignStatus.INSUFFICIENT)
        plan = plan_a1_refinement(parent, parent_campaign, maximum_challengers=1)
        child = materialize_plan_challengers(parent, plan, created_at=NOW)[0]
        child_campaign = campaign(child, A1CampaignStatus.INSUFFICIENT)
        result = certify_strategy_ecosystem(
            parent,
            parent_campaign,
            plan,
            child=child,
            child_campaign=child_campaign,
            estate_sha256_before=ESTATE,
            estate_sha256_after=ESTATE,
        )
        self.assertIs(result.status, StrategyEcosystemCertificationStatus.READY_FOR_M197)
        self.assertTrue(result.engineering_handoff_to_m197)
        self.assertFalse(result.strategy_a1_qualified)
        self.assertEqual(result.blockers, ())

    def test_promising_parent_can_handoff_without_unnecessary_mutation(self):
        parent = reconstruction()
        parent_campaign = campaign(parent, A1CampaignStatus.PROMISING)
        plan = plan_a1_refinement(parent, parent_campaign)
        result = certify_strategy_ecosystem(
            parent,
            parent_campaign,
            plan,
            estate_sha256_before=ESTATE,
            estate_sha256_after=ESTATE,
        )
        self.assertTrue(result.engineering_handoff_to_m197)
        self.assertTrue(result.strategy_a1_qualified)
        self.assertEqual(result.parent_strategy_hash, result.final_strategy_hash)

    def test_estate_mutation_blocks_engineering_handoff(self):
        parent = reconstruction()
        parent_campaign = campaign(parent, A1CampaignStatus.PROMISING)
        plan = plan_a1_refinement(parent, parent_campaign)
        result = certify_strategy_ecosystem(
            parent,
            parent_campaign,
            plan,
            estate_sha256_before=ESTATE,
            estate_sha256_after="f" * 64,
        )
        self.assertIs(result.status, StrategyEcosystemCertificationStatus.BLOCKED)
        self.assertIn("strategy_estate_mutated", result.blockers)

    def test_missing_child_retest_blocks_failed_parent_handoff(self):
        parent = reconstruction()
        parent_campaign = campaign(parent, A1CampaignStatus.INSUFFICIENT)
        plan = plan_a1_refinement(parent, parent_campaign, maximum_challengers=1)
        result = certify_strategy_ecosystem(
            parent,
            parent_campaign,
            plan,
            estate_sha256_before=ESTATE,
            estate_sha256_after=ESTATE,
        )
        self.assertFalse(result.engineering_handoff_to_m197)
        self.assertIn("failed_parent_missing_child_retest", result.blockers)

    def test_insufficient_chronological_windows_block_handoff(self):
        parent = reconstruction()
        parent_campaign = campaign(parent, A1CampaignStatus.PROMISING, windows=4)
        plan = plan_a1_refinement(parent, parent_campaign)
        result = certify_strategy_ecosystem(
            parent,
            parent_campaign,
            plan,
            estate_sha256_before=ESTATE,
            estate_sha256_after=ESTATE,
        )
        self.assertIn("parent_chronological_window_count_failed", result.blockers)

    def test_certification_never_grants_operational_authority(self):
        parent = reconstruction()
        parent_campaign = campaign(parent, A1CampaignStatus.PROMISING)
        plan = plan_a1_refinement(parent, parent_campaign)
        result = certify_strategy_ecosystem(
            parent,
            parent_campaign,
            plan,
            estate_sha256_before=ESTATE,
            estate_sha256_after=ESTATE,
        )
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
