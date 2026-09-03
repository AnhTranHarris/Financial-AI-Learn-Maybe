from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from dusty.local_research import ResearchSettings
from dusty.local_research_funnel import run_local_research_funnel
from dusty.research_challengers import ChallengerPlan, MutationKind, ResearchMutation
from dusty.research_funnel import FunnelScreenPolicy
from test_connected_research import END, START, selection
from test_fixed_evaluation import FullWindowReader


class CountingReader:
    def __init__(self, bars, economics) -> None:
        self.bars = bars
        self.economics = economics
        self.calls = 0
        self.cost_observation = {"status": "FIXTURE", "execution_deals": 0}

    def read(self, _selection, _start, _end):
        self.calls += 1
        return self.bars, self.economics


class LocalResearchFunnelAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.selection = selection()
        cls.bars, cls.economics = FullWindowReader().read(cls.selection, START, END)
        cls.history_days = (END - START).days
        cls.settings = ResearchSettings(
            history_days=cls.history_days,
            commission_per_lot=1.0,
            slippage_points=2.0,
            spread_floor_points=1.0,
            fixed_end=END,
            holdout_days=1,
            cost_source="deterministic fixture assumptions",
        )
        cls.challengers = ChallengerPlan((
            ResearchMutation(MutationKind.RSI_PERIOD, 10),
            ResearchMutation(MutationKind.EXIT_HORIZON_MINUTES, 180),
        ), max_candidates=2)
        cls.screen = FunnelScreenPolicy(
            minimum_closed_trades=1,
            maximum_marked_drawdown=1.0,
            require_positive_net_pnl=False,
            require_development_pass=False,
            require_stress_pass=False,
            max_native_candidates=8,
        )

    def test_adapter_reuses_one_history_acquisition_across_identical_runs(self) -> None:
        reader = CountingReader(self.bars, self.economics)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = run_local_research_funnel(
                self.selection,
                self.settings,
                root,
                START,
                END,
                reader=reader,
                challenger_plan=self.challengers,
                screen_policy=self.screen,
            )
            second = run_local_research_funnel(
                self.selection,
                self.settings,
                root,
                START,
                END,
                reader=reader,
                challenger_plan=self.challengers,
                screen_policy=self.screen,
            )

        self.assertEqual(reader.calls, 1)
        self.assertFalse(first.cycle.cache_hit)
        self.assertTrue(second.cycle.cache_hit)
        self.assertEqual(second.bars, self.bars)
        self.assertEqual(second.economics, self.economics)
        self.assertFalse(second.report["promotion_eligible"])
        self.assertIsNone(second.report["selected_winner"])
        self.assertFalse(second.report["local_adapter"]["broker_write_authorized"])
        self.assertFalse(second.report["local_adapter"]["native_tester_launched"])
        self.assertFalse(second.report["local_adapter"]["legacy_desktop_routing_changed"])

    def test_adapter_requires_frozen_holdout_and_cost_provenance(self) -> None:
        no_holdout = ResearchSettings(
            history_days=self.history_days,
            fixed_end=END,
            cost_source="fixture",
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "fixed_holdout"):
                run_local_research_funnel(
                    self.selection,
                    no_holdout,
                    Path(temporary),
                    START,
                    END,
                    reader=CountingReader(self.bars, self.economics),
                    challenger_plan=self.challengers,
                )

        no_cost_note = ResearchSettings(
            history_days=self.history_days,
            fixed_end=END,
            holdout_days=1,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "cost_source"):
                run_local_research_funnel(
                    self.selection,
                    no_cost_note,
                    Path(temporary),
                    START,
                    END,
                    reader=CountingReader(self.bars, self.economics),
                    challenger_plan=self.challengers,
                )

    def test_adapter_refuses_legacy_comparison_mode_to_avoid_duplicate_heavy_matrices(self) -> None:
        comparison = ResearchSettings(
            history_days=self.history_days,
            fixed_end=END,
            holdout_days=1,
            cost_source="fixture",
            comparison=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "alternative_to_legacy_comparison"):
                run_local_research_funnel(
                    self.selection,
                    comparison,
                    Path(temporary),
                    START,
                    END,
                    reader=CountingReader(self.bars, self.economics),
                    challenger_plan=self.challengers,
                )


if __name__ == "__main__":
    unittest.main()
