from __future__ import annotations

import unittest

from dusty.analysis_certification import (
    AnalysisMilestoneEvidence,
    AnalyticalDependencyEvidence,
    DeskStrategyEvidence,
    FirmMandate,
    REQUIRED_ANALYSIS_MILESTONES,
    RuntimeToolObservation,
    assess_firm_mandate,
    assess_runtime_analysis,
    certify_analysis_phase,
)
from dusty.analytical_tools import TemporalBehavior, ToolLifecycle
from dusty.strategy_v3 import FrozenStrategyDeployment
from dusty.tool_evaluation import PerformanceWindow


COMMIT = "a" * 40


def performance(name: str, *, pnl: float = 200.0, trades: int = 150, violations: int = 0) -> PerformanceWindow:
    return PerformanceWindow(name, trades, pnl, 300.0, -100.0, 0.10, 30.0, 0.25, violations)


def desk(index: int, **kwargs: object) -> DeskStrategyEvidence:
    return DeskStrategyEvidence(
        f"desk-{index}",
        f"generation-{index}",
        f"session-{index}",
        kwargs.pop("performance", performance(f"desk-window-{index}")),
        kwargs.pop("native_execution_parity", True),
        kwargs.pop("analytical_tool_drift", False),
    )


class M84RuntimeTests(unittest.TestCase):
    def test_tool_hash_drift_blocks_entries_but_preserves_position_supervision(self):
        deployment = FrozenStrategyDeployment("a" * 64, "b" * 64, ("c" * 64,), "generation-1")
        result = assess_runtime_analysis(
            deployment,
            strategy_hash="a" * 64,
            graph_hash="b" * 64,
            tools=(RuntimeToolObservation("c" * 64, ToolLifecycle.CERTIFIED_DEPENDENCY, False, True, 1.0),),
            maximum_stale_seconds=60,
        )
        self.assertFalse(result.new_entries_authorized)
        self.assertTrue(result.position_supervision_required)
        self.assertTrue(any(reason.startswith("tool_hash_drift:") for reason in result.reasons))

    def test_stale_or_degraded_tool_blocks_demo_entry(self):
        deployment = FrozenStrategyDeployment("a" * 64, "b" * 64, ("c" * 64,), "generation-1")
        result = assess_runtime_analysis(
            deployment,
            strategy_hash="a" * 64,
            graph_hash="b" * 64,
            tools=(RuntimeToolObservation("c" * 64, ToolLifecycle.DEGRADED, True, True, 120.0),),
            maximum_stale_seconds=60,
        )
        self.assertFalse(result.new_entries_authorized)
        self.assertIn("tool_value_stale:cccccccccccc", result.reasons)


class M85MandateTests(unittest.TestCase):
    def test_six_profitable_rule_compliant_desks_and_independent_windows_pass(self):
        mandate = FirmMandate("firm-v1")
        windows = tuple(performance(f"window-{index}") for index in range(3))
        result = assess_firm_mandate(mandate, windows, tuple(desk(index) for index in range(6)))
        self.assertTrue(result.passed)
        self.assertEqual(result.passing_desks, 6)

    def test_one_profitable_rule_violation_invalidates_generation(self):
        mandate = FirmMandate("firm-v1")
        windows = tuple(performance(f"window-{index}") for index in range(3))
        desks = [desk(index) for index in range(6)]
        desks[2] = desk(2, performance=performance("bad-win", pnl=1000, violations=1))
        result = assess_firm_mandate(mandate, windows, desks)
        self.assertFalse(result.passed)
        self.assertIn("desk_generation_contains_rule_violation", result.reasons)

    def test_positive_total_pnl_cannot_hide_losing_independent_window(self):
        mandate = FirmMandate("firm-v1")
        windows = (performance("one", pnl=1000), performance("two", pnl=1000), performance("three", pnl=-1))
        result = assess_firm_mandate(mandate, windows, tuple(desk(index) for index in range(6)))
        self.assertFalse(result.passed)
        self.assertIn("window:three:expectancy_failed", result.reasons)


class M85CertificationTests(unittest.TestCase):
    def evidence(self):
        return tuple(
            AnalysisMilestoneEvidence(milestone, True, f"artifact-{milestone}", f"data-{milestone}", f"config-{milestone}", f"test-{milestone}", COMMIT)
            for milestone in REQUIRED_ANALYSIS_MILESTONES
        )

    def mandate(self):
        policy = FirmMandate("firm-v1")
        return assess_firm_mandate(
            policy,
            tuple(performance(f"window-{index}") for index in range(3)),
            tuple(desk(index) for index in range(6)),
        )

    def dependency(self, **kwargs: object):
        return AnalyticalDependencyEvidence(
            "b" * 64,
            kwargs.pop("state", ToolLifecycle.CERTIFIED_DEPENDENCY),
            kwargs.pop("temporal_behavior", TemporalBehavior.CAUSAL_COMPLETED_BAR),
            kwargs.pop("native_parity_passed", True),
            kwargs.pop("backtest_demo_parity_passed", True),
            kwargs.pop("artifact_hash_matches", True),
        )

    def test_full_phase_can_certify_live_compatible_package_but_never_live_write(self):
        result = certify_analysis_phase(
            self.evidence(),
            (self.dependency(),),
            self.mandate(),
            current_commit_sha=COMMIT,
            m75_operational_proof_hash="c" * 64,
        )
        self.assertTrue(result.indicator_chart_certified)
        self.assertTrue(result.live_compatible_research_package)
        self.assertFalse(result.live_write_authorized)

    def test_repainting_dependency_blocks_certification_despite_mandate_pass(self):
        result = certify_analysis_phase(
            self.evidence(),
            (self.dependency(temporal_behavior=TemporalBehavior.REPAINTING),),
            self.mandate(),
            current_commit_sha=COMMIT,
            m75_operational_proof_hash="c" * 64,
        )
        self.assertFalse(result.indicator_chart_certified)
        self.assertTrue(any(reason.startswith("dependency_temporal_failure:") for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
