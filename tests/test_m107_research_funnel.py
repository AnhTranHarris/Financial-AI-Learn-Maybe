from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from dusty.research_challengers import ChallengerPlan, MutationKind, ResearchMutation
from dusty.research_funnel import (
    FunnelLaboratoryPolicy,
    FunnelPolicy,
    FunnelScreenPolicy,
    UnifiedResearchFunnel,
    compact_funnel_report,
    decode_acquisition,
)
from dusty.research_evaluation import FixedEvaluationPlan
from dusty.reviewed_strategies import reviewed_research_packages
from test_connected_research import END, START, selection
from test_fixed_evaluation import FullWindowReader


class UnifiedResearchFunnelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw, cls.economics = FullWindowReader().read(selection(), START, END)
        cls.parent = reviewed_research_packages()[0]
        cls.plan = FixedEvaluationPlan(START, END.replace(day=29), END)
        cls.challengers = ChallengerPlan((
            ResearchMutation(MutationKind.RSI_PERIOD, 10),
            ResearchMutation(MutationKind.EXIT_HORIZON_MINUTES, 180),
        ), max_candidates=2)

    def policy(self, **screen_changes):
        screen = replace(FunnelScreenPolicy(), **screen_changes)
        return FunnelPolicy(
            FunnelLaboratoryPolicy(growth_starting_equity=100_000, commission_per_lot=1),
            screen=screen,
            additional_round_trip_slippage_points=10,
        )

    def request(self):
        return {
            "code_commit": "a" * 40,
            "symbol": "EURUSD",
            "timeframe": "M15",
            "binding": "fixture",
        }

    def test_cycle_runs_all_stages_and_second_identical_run_reuses_acquisition(self) -> None:
        calls = 0

        def acquire():
            nonlocal calls
            calls += 1
            return self.raw, self.economics

        with tempfile.TemporaryDirectory() as temporary:
            funnel = UnifiedResearchFunnel(Path(temporary))
            first = funnel.run(
                self.request(),
                parent=self.parent,
                challenger_plan=self.challengers,
                evaluation_plan=self.plan,
                policy=self.policy(minimum_closed_trades=1, maximum_marked_drawdown=1.0,
                                   require_positive_net_pnl=False),
                acquire=acquire,
            )
            second = funnel.run(
                self.request(),
                parent=self.parent,
                challenger_plan=self.challengers,
                evaluation_plan=self.plan,
                policy=self.policy(minimum_closed_trades=1, maximum_marked_drawdown=1.0,
                                   require_positive_net_pnl=False),
                acquire=acquire,
            )

        self.assertEqual(calls, 1)
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(
            tuple(name for name, _ in first.outputs),
            ("acquisition", "features", "challengers", "cheap-screen", "diagnostics", "fidelity-queue"),
        )
        self.assertEqual(len(first.output_map()["challengers"]["candidates"]), 3)

    def test_acquisition_can_be_recovered_without_a_second_reader_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = UnifiedResearchFunnel(Path(temporary)).run(
                self.request(),
                parent=self.parent,
                challenger_plan=self.challengers,
                evaluation_plan=self.plan,
                policy=self.policy(),
                acquire=lambda: (self.raw, self.economics),
            )
            raw, economics = decode_acquisition(result)
        self.assertEqual(raw, self.raw)
        self.assertEqual(economics, self.economics)

    def test_every_candidate_gets_configured_and_stress_segments_and_cost_attribution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = UnifiedResearchFunnel(Path(temporary)).run(
                self.request(),
                parent=self.parent,
                challenger_plan=self.challengers,
                evaluation_plan=self.plan,
                policy=self.policy(),
                acquire=lambda: (self.raw, self.economics),
            )
        outputs = result.output_map()
        cases = outputs["cheap-screen"]["cases"]
        self.assertEqual(len(cases), 3)
        for case in cases:
            self.assertEqual(set(case["scenarios"]), {"configured", "stress"})
            for scenario in case["scenarios"].values():
                self.assertEqual(set(scenario["segments"]), {"development", "holdout"})
        attribution = outputs["diagnostics"]["matched_exposure_cost_attribution"]
        self.assertEqual(len(attribution), 6)
        for row in attribution:
            self.assertLessEqual(row["additional_cost_effect_same_exposure"], 1e-12)
            self.assertAlmostEqual(
                row["original_net_pnl"]
                + row["additional_cost_effect_same_exposure"]
                + row["exposure_or_sequence_effect"],
                row["actual_stressed_net_pnl"],
                places=7,
            )

    def test_native_budget_fails_closed_instead_of_ranking_survivors(self) -> None:
        permissive = self.policy(
            minimum_closed_trades=1,
            maximum_marked_drawdown=1.0,
            require_positive_net_pnl=False,
            require_development_pass=False,
            require_stress_pass=False,
            max_native_candidates=1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            result = UnifiedResearchFunnel(Path(temporary)).run(
                self.request(),
                parent=self.parent,
                challenger_plan=self.challengers,
                evaluation_plan=self.plan,
                policy=permissive,
                acquire=lambda: (self.raw, self.economics),
            )
        queue = result.output_map()["fidelity-queue"]
        self.assertEqual(queue["status"], "BUDGET_BLOCKED_TOO_MANY_SURVIVORS")
        self.assertEqual(queue["proposals"], [])
        self.assertFalse(queue["ranking_performed"])

    def test_compact_report_excludes_raw_bars_and_never_selects_a_winner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = UnifiedResearchFunnel(Path(temporary)).run(
                self.request(),
                parent=self.parent,
                challenger_plan=self.challengers,
                evaluation_plan=self.plan,
                policy=self.policy(),
                acquire=lambda: (self.raw, self.economics),
            )
        report = compact_funnel_report(result)
        self.assertNotIn("acquisition", report)
        self.assertNotIn("features", report)
        self.assertIsNone(report["selected_winner"])
        self.assertFalse(report["promotion_eligible"])

    def test_request_identity_changes_when_screen_policy_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            funnel = UnifiedResearchFunnel(Path(temporary))
            a = funnel.run(
                self.request(), parent=self.parent, challenger_plan=self.challengers,
                evaluation_plan=self.plan, policy=self.policy(minimum_closed_trades=5),
                acquire=lambda: (self.raw, self.economics),
            )
            b = funnel.run(
                self.request(), parent=self.parent, challenger_plan=self.challengers,
                evaluation_plan=self.plan, policy=self.policy(minimum_closed_trades=6),
                acquire=lambda: (self.raw, self.economics),
            )
        self.assertNotEqual(a.cycle_fingerprint, b.cycle_fingerprint)


if __name__ == "__main__":
    unittest.main()
