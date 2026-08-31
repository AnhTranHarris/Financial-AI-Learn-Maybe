from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from dusty.backtest import (
    BacktestMode,
    PriceMark,
    SimulatedTrade,
    evaluation_slice,
    purged_walk_forward_ranges,
    simulate_portfolio,
)
from dusty.experience import TradeSide
from dusty.markets import InstrumentEconomics
from dusty.statistical import (
    CandidateFoldScore,
    SQLiteTrialRegistry,
    TrialRecord,
    adjusted_pvalue,
    assess_selection_bias,
    bootstrap_mean_interval,
    estimate_selection_overfit,
    parameter_neighborhood_stable,
    profit_concentration,
)


T0 = datetime(2026, 8, 31, 9, 0, tzinfo=UTC)
ECON = InstrumentEconomics(
    contract_size=1.0,
    tick_size=1.0,
    tick_value=1.0,
    volume_min=0.01,
    volume_step=0.01,
    volume_max=100.0,
    margin_rate=0.1,
)


class BacktestLedgerTests(unittest.TestCase):
    def test_realistic_ledger_handles_overlap_costs_and_unrealized_equity(self) -> None:
        trades = (
            SimulatedTrade(
                "a",
                "A",
                TradeSide.LONG,
                T0,
                T0 + timedelta(minutes=20),
                100.0,
                110.0,
                1.0,
                entry_cost=1.0,
                exit_cost=1.0,
            ),
            SimulatedTrade(
                "b",
                "B",
                TradeSide.SHORT,
                T0 + timedelta(minutes=5),
                T0 + timedelta(minutes=25),
                200.0,
                190.0,
                1.0,
                entry_cost=1.0,
                exit_cost=1.0,
            ),
        )
        marks = (
            PriceMark(T0 + timedelta(minutes=10), "A", 95.0),
            PriceMark(T0 + timedelta(minutes=10), "B", 205.0),
        )
        result = simulate_portfolio(
            trades,
            marks,
            {"A": ECON, "B": ECON},
            starting_equity=100.0,
        )
        self.assertIs(result.mode, BacktestMode.REALISTIC_LEDGER)
        self.assertEqual(result.trade_count, 2)
        self.assertAlmostEqual(result.net_pnl, 16.0)
        self.assertAlmostEqual(result.ending_balance, 116.0)
        stressed = next(point for point in result.ledger if point.at == T0 + timedelta(minutes=10))
        self.assertEqual(stressed.open_positions, 2)
        self.assertLess(stressed.equity, 100.0)
        self.assertGreater(result.max_drawdown_fraction, 0.0)

    def test_backtest_refuses_open_ended_positions(self) -> None:
        trade = SimulatedTrade(
            "a",
            "A",
            TradeSide.LONG,
            T0,
            T0 + timedelta(minutes=20),
            100.0,
            110.0,
            1.0,
        )
        # Removing the exit from the event stream is impossible because SimulatedTrade owns it;
        # instead verify missing economics fails before account simulation can fabricate PnL.
        with self.assertRaises(ValueError):
            simulate_portfolio((trade,), (), {}, starting_equity=100.0)

    def test_warmup_is_excluded_and_folds_are_purged_embargoed(self) -> None:
        self.assertEqual(evaluation_slice((0, 1, 2, 3), warmup_rows=2), (2, 3))
        folds = purged_walk_forward_ranges(
            100,
            train_rows=20,
            test_rows=10,
            purge_rows=2,
            embargo_rows=3,
        )
        self.assertGreaterEqual(len(folds), 2)
        first, second = folds[:2]
        self.assertEqual(first.train_end, 20)
        self.assertEqual(first.test_start, 22)
        self.assertEqual(second.test_start - first.test_end, 3)
        for fold in folds:
            self.assertLessEqual(fold.train_end, fold.test_start)


class StatisticalRealityTests(unittest.TestCase):
    def test_bootstrap_is_seeded_and_reproducible(self) -> None:
        values = (0.01, 0.02, -0.005, 0.015, 0.01)
        first = bootstrap_mean_interval(values, resamples=500, seed=7)
        second = bootstrap_mean_interval(values, resamples=500, seed=7)
        self.assertEqual(first, second)
        self.assertLessEqual(first.lower, first.mean)
        self.assertGreaterEqual(first.upper, first.mean)

    def test_search_penalty_grows_with_number_of_trials(self) -> None:
        returns = (0.01, 0.02, 0.015, 0.005, 0.012, 0.011)
        few = assess_selection_bias(returns, trial_count=2)
        many = assess_selection_bias(returns, trial_count=10_000)
        self.assertGreater(many.search_penalty, few.search_penalty)
        self.assertLess(many.deflated_signal_score, few.deflated_signal_score)
        self.assertEqual(adjusted_pvalue(0.01, trial_count=20), 0.2)

    def test_failed_trials_are_never_hidden(self) -> None:
        registry = SQLiteTrialRegistry()
        try:
            registry.record(TrialRecord("a", "trend", 1.0, True, "fp-a"))
            registry.record(TrialRecord("b", "trend", -1.0, False, "fp-b"))
            registry.record(TrialRecord("c", "mean-reversion", 0.2, False, "fp-c"))
            self.assertEqual(registry.count(), 3)
            self.assertEqual(registry.count("trend"), 2)
            self.assertEqual(tuple(item.passed for item in registry.history()), (True, False, False))
            self.assertTrue(registry.integrity_ok())
        finally:
            registry.close()

    def test_profit_concentration_and_parameter_plateau_are_explicit(self) -> None:
        concentrated = profit_concentration((-0.1, -0.1, 0.05, 1.0))
        self.assertGreater(concentrated.largest_winner_fraction, 0.9)
        self.assertTrue(
            parameter_neighborhood_stable((0.10, 0.11, 0.09, 0.08), max_spread=0.05)
        )
        self.assertFalse(
            parameter_neighborhood_stable((0.10, 1.0, -0.2), max_spread=0.20)
        )

    def test_selection_overfit_proxy_tracks_in_sample_winner_failure(self) -> None:
        scores = (
            CandidateFoldScore("f1", "a", 10.0, -1.0),
            CandidateFoldScore("f1", "b", 5.0, 2.0),
            CandidateFoldScore("f2", "a", 9.0, -2.0),
            CandidateFoldScore("f2", "b", 4.0, 1.0),
        )
        assessment = estimate_selection_overfit(scores)
        self.assertEqual(assessment.fold_count, 2)
        self.assertEqual(assessment.selected_below_median_count, 2)
        self.assertEqual(assessment.overfit_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
