from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.capital_attribution import CapitalAttribution, SQLiteCapitalAttribution, rank_capital_reputations
from dusty.experience import TradeSide
from dusty.portfolio_model import EstimateState, StrategyReturn, build_portfolio_risk_model, derive_fx_factor_exposures


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


class PortfolioModelTests(unittest.TestCase):
    def test_pit_model_ignores_future_and_detects_shared_behavior(self):
        rows = []
        for index in range(20):
            at = NOW + timedelta(hours=index)
            value = (index - 10) / 1000.0
            rows.append(StrategyReturn("a", "EURUSD", at, value))
            rows.append(StrategyReturn("b", "GBPUSD", at, value * 2.0))
        rows.append(StrategyReturn("a", "EURUSD", NOW + timedelta(days=10), 99.0))
        model = build_portfolio_risk_model(rows, as_of=NOW + timedelta(hours=19), min_samples=20)
        self.assertEqual(model.correlations[0].state, EstimateState.MEASURED)
        self.assertAlmostEqual(model.correlations[0].correlation, 1.0)
        self.assertLess(model.volatility_map()["a"], 1.0)

    def test_sparse_history_is_conservative_not_zero_correlation(self):
        rows = (StrategyReturn("a", "EURUSD", NOW, 0.01), StrategyReturn("b", "GBPUSD", NOW, 0.02))
        model = build_portfolio_risk_model(rows, as_of=NOW, min_samples=5)
        self.assertEqual(model.correlations[0].state, EstimateState.INSUFFICIENT)
        self.assertEqual(model.correlations[0].correlation, 1.0)

    def test_duplicate_strategy_timestamp_is_rejected(self):
        rows = (StrategyReturn("a", "EURUSD", NOW, 0.01), StrategyReturn("a", "EURUSD", NOW, 0.02))
        with self.assertRaises(ValueError):
            build_portfolio_risk_model(rows, as_of=NOW, min_samples=2)

    def test_fx_factor_exposures_make_usd_direction_explicit(self):
        eur = dict(derive_fx_factor_exposures("EURUSD", TradeSide.LONG))
        gbp = dict(derive_fx_factor_exposures("GBPUSD", TradeSide.LONG))
        self.assertEqual(eur["CCY:USD"], -1.0)
        self.assertEqual(gbp["CCY:USD"], -1.0)


class CapitalAttributionTests(unittest.TestCase):
    def row(self, identity, strategy, pnl, rules, drawdown):
        return CapitalAttribution(identity, strategy, "EURUSD", NOW, pnl, 100.0, drawdown, rules)

    def test_bad_big_win_cannot_outrank_smaller_good_win(self):
        rows = (self.row("good", "good", 10.0, True, 0.01), self.row("bad", "bad", 100.0, False, 0.00))
        ranked = rank_capital_reputations(rows)
        self.assertEqual(ranked[0].strategy_hash, "good")
        self.assertEqual(ranked[1].governance_pass_rate, 0.0)

    def test_losses_raise_investigation_priority_not_reward(self):
        rows = (self.row("w", "s", 10.0, True, 0.01), self.row("l", "s", -5.0, True, 0.02))
        reputation = rank_capital_reputations(rows)[0]
        self.assertEqual(reputation.investigation_priority, 1)

    def test_attribution_memory_is_append_only_and_integrity_checked(self):
        db = SQLiteCapitalAttribution()
        try:
            row = self.row("one", "s", 5.0, True, 0.01)
            db.append(row)
            self.assertEqual(tuple(db.iter_rows("s")), (row,))
            with self.assertRaises(Exception):
                db.append(row)
            self.assertTrue(db.integrity_ok())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
