from __future__ import annotations

import unittest

from dusty.investment_lab import LaboratoryConfig
from dusty.research_comparison import run_research_comparison
from dusty.research_evaluation import FixedEvaluationPlan
from test_connected_research import END, START
from test_research_comparison import setup_data


class ComparisonAttributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bars, cls.economics = setup_data()
        cls.config = LaboratoryConfig(
            growth_starting_equity=100000,
            commission_per_lot=1,
            expected_slippage_price=.00002,
        )
        cls.plan = FixedEvaluationPlan(START, END.replace(day=29), END)
        cls.report = run_research_comparison(
            cls.bars,
            symbol="EURUSD",
            economics=cls.economics,
            config=cls.config,
            plan=cls.plan,
        )

    def test_every_candidate_segment_gets_one_matched_exposure_decomposition(self) -> None:
        rows = self.report["matched_exposure_cost_attribution"]
        self.assertEqual(len(rows), 10)
        self.assertEqual(
            {(row["candidate_id"], row["segment"]) for row in rows},
            {(candidate["id"], segment)
             for candidate in self.report["contract"]["candidates"]
             for segment in ("development", "holdout")},
        )

    def test_components_reconcile_actual_stress_without_calling_resizing_cost_drag(self) -> None:
        expected_extra_per_lot = (
            10 * self.economics.point_size / self.economics.tick_size * self.economics.tick_value
        )
        for row in self.report["matched_exposure_cost_attribution"]:
            with self.subTest(candidate=row["candidate_id"], segment=row["segment"]):
                self.assertAlmostEqual(row["additional_cost_per_lot"], expected_extra_per_lot)
                self.assertLessEqual(row["additional_cost_effect_same_exposure"], 1e-12)
                self.assertLessEqual(row["stressed_net_pnl_same_exposure"], row["original_net_pnl"] + 1e-12)
                self.assertAlmostEqual(
                    row["original_net_pnl"]
                    + row["additional_cost_effect_same_exposure"]
                    + row["exposure_or_sequence_effect"],
                    row["actual_stressed_net_pnl"],
                    places=7,
                )
                self.assertAlmostEqual(
                    row["actual_stressed_net_pnl"] - row["original_net_pnl"],
                    row["actual_total_change"],
                    places=7,
                )

    def test_no_trade_control_remains_zero_under_both_components(self) -> None:
        rows = [row for row in self.report["matched_exposure_cost_attribution"]
                if row["candidate_id"] == "no-trade"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row["original_approved_volume_lots"], 0)
            self.assertEqual(row["original_net_pnl"], 0)
            self.assertEqual(row["additional_cost_effect_same_exposure"], 0)
            self.assertEqual(row["exposure_or_sequence_effect"], 0)
            self.assertEqual(row["actual_stressed_net_pnl"], 0)


if __name__ == "__main__":
    unittest.main()
