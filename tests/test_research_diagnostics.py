from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from dusty.experience import TradeSide
from dusty.forecasting import Forecast
from dusty.research_diagnostics import (
    MatchedExposureAttribution,
    MatchedExposureTrade,
    audit_forecast_veto,
    decompose_stressed_result,
    matched_exposure_cost_attribution,
)
from dusty.runtime import RuntimeTrade


class ForecastVetoDiagnosticTests(unittest.TestCase):
    def test_reproduces_the_audited_bearish_veto_shape_without_claiming_selection_skill(self) -> None:
        start = datetime(2026, 8, 1, tzinfo=UTC)
        forecasts = []
        for index in range(90):
            at = start + timedelta(minutes=15 * index)
            # First 45 baseline entries conflict, entry 46 is neutral. Across all issued
            # forecasts: 77/90 bearish = 85.555...%, displayed as 85.6%.
            if index < 45 or 46 <= index < 78:
                point = 99.0
            elif index == 45:
                point = 100.0
            else:
                point = 101.0
            forecasts.append(Forecast("ridge", at, 16, 100.0, point))

        trades = []
        pnl = {}
        for index in range(46):
            at = start + timedelta(minutes=15 * index)
            trades.append(RuntimeTrade("seed", at, at + timedelta(hours=1), TradeSide.LONG,
                                       100.0, 101.0, 98.0, None, "test"))
            pnl[at] = 1.0 if index < 19 else (-1.0 if index < 45 else 0.0)

        diagnostic = audit_forecast_veto(
            forecasts,
            trades,
            pnl,
            provider="ridge",
            horizon_steps=16,
            direction_threshold=0.005,
        )

        self.assertEqual(diagnostic.issued_forecasts, 90)
        self.assertEqual(diagnostic.bearish_forecasts, 77)
        self.assertAlmostEqual(diagnostic.bearish_fraction, 77 / 90)
        self.assertEqual(diagnostic.baseline_entries, 46)
        self.assertEqual(diagnostic.conflicting_entries, 45)
        self.assertEqual(diagnostic.conflicting_winners, 19)
        self.assertEqual(diagnostic.conflicting_losers, 26)
        self.assertEqual(diagnostic.neutral_entries, 1)
        self.assertEqual(diagnostic.favorable_entries, 0)
        self.assertEqual(diagnostic.missing_entry_forecasts, 0)

    def test_duplicate_forecast_identity_fails_closed(self) -> None:
        at = datetime(2026, 8, 1, tzinfo=UTC)
        forecast = Forecast("ridge", at, 16, 100.0, 99.0)
        trade = RuntimeTrade("seed", at, at + timedelta(hours=1), TradeSide.LONG,
                             100.0, 101.0, 98.0, None, "test")
        with self.assertRaisesRegex(ValueError, "duplicate_forecast"):
            audit_forecast_veto(
                (forecast, forecast),
                (trade,),
                {at: -1.0},
                provider="ridge",
                horizon_steps=16,
                direction_threshold=0.0,
            )


class MatchedExposureDiagnosticTests(unittest.TestCase):
    def test_fixed_exposure_cost_stress_cannot_create_a_profit_improvement(self) -> None:
        rows = (
            MatchedExposureTrade("a", 2.0, -100.0, 3.0, 5.0),
            MatchedExposureTrade("b", 1.0, 50.0, 3.0, 5.0),
        )
        result = matched_exposure_cost_attribution(rows)
        self.assertEqual(result.trade_count, 2)
        self.assertAlmostEqual(result.original_net_pnl, -59.0)
        self.assertAlmostEqual(result.stressed_net_pnl_same_exposure, -65.0)
        self.assertAlmostEqual(result.additional_cost_effect, -6.0)

    def test_decomposition_reproduces_audited_67_54_improvement(self) -> None:
        attribution = MatchedExposureAttribution(
            trade_count=15,
            original_net_pnl=-1522.27,
            stressed_net_pnl_same_exposure=-1568.27,
            additional_cost_effect=-46.00,
        )
        result = decompose_stressed_result(attribution, -1454.73)

        self.assertAlmostEqual(result.additional_cost_effect_same_exposure, -46.00)
        self.assertAlmostEqual(result.exposure_or_sequence_effect, 113.54)
        self.assertAlmostEqual(result.actual_stressed_net_pnl, -1454.73)
        self.assertAlmostEqual(result.total_change, 67.54)
        self.assertAlmostEqual(
            result.original_net_pnl
            + result.additional_cost_effect_same_exposure
            + result.exposure_or_sequence_effect,
            result.actual_stressed_net_pnl,
        )


if __name__ == "__main__":
    unittest.main()
