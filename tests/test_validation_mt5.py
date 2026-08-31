from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.experience import TradeSide
from dusty.mt5lab import MT5TestRequest, MT5TickMode, fidelity_at_least, next_tick_mode
from dusty.research import Clause, FeatureRow, RuleOp, StrategySpec, run_experiment
from dusty.validation import (
    RobustnessGate,
    TournamentEntry,
    ValidationFold,
    evaluate_walk_forward,
    mutate_numeric_clause,
    rank_tournament,
)


T0 = datetime(2026, 8, 31, 6, 0, tzinfo=timezone.utc)


def make_spec(value: float, name: str = "s") -> StrategySpec:
    return StrategySpec(name, TradeSide.LONG, (Clause("strength", RuleOp.GE, value),))


def result(spec: StrategySpec, return_value: float):
    rows = (
        FeatureRow.of(T0 + timedelta(minutes=i), {"strength": 10.0}, return_value)
        for i in range(50)
    )
    return run_experiment(spec, rows)


class RefinementTournamentTests(unittest.TestCase):
    def test_m30_numeric_refinement_is_bounded_and_deduplicated(self):
        parent = make_spec(5.0)
        variants = mutate_numeric_clause(parent, 0, (5.0, 6.0, 7.0, 8.0, 9.0), max_variants=3)
        self.assertEqual(len(variants), 3)
        self.assertNotIn(parent.strategy_hash, {item.strategy_hash for item in variants})

    def test_m30_tournament_is_deterministic(self):
        weak = make_spec(4.0, "weak")
        strong = make_spec(5.0, "strong")
        outcome = rank_tournament(
            (
                TournamentEntry(weak, result(weak, 0.001)),
                TournamentEntry(strong, result(strong, 0.003)),
            )
        )
        self.assertEqual(outcome.champion_hash, strong.strategy_hash)


class WalkForwardTests(unittest.TestCase):
    def test_m31_walk_forward_rejects_one_bad_fold(self):
        spec = make_spec(5.0)
        folds = (
            ValidationFold("train-a", result(spec, 0.002)),
            ValidationFold("forward-b", result(spec, 0.001)),
            ValidationFold("forward-c", result(spec, -0.003)),
        )
        assessment = evaluate_walk_forward(
            folds,
            RobustnessGate(min_folds=3, min_fold_mean_return=0.0, max_failed_folds=0),
        )
        self.assertFalse(assessment.passed)
        self.assertIn("too_many_failed_folds", assessment.reasons)


class MT5ContractTests(unittest.TestCase):
    def test_m32_mt5_contract_is_tester_only_and_escalates_fidelity(self):
        request = MT5TestRequest(
            request_id="r1",
            terminal_path=r"C:\\MT5-A\\terminal64.exe",
            strategy_hash="abc",
            symbol="EURUSD",
            timeframe="M5",
            start=T0,
            end=T0 + timedelta(days=30),
            tick_mode=MT5TickMode.OPEN_PRICES,
        )
        self.assertFalse(request.broker_write_authorized)
        self.assertIs(next_tick_mode(MT5TickMode.OPEN_PRICES), MT5TickMode.ONE_MINUTE_OHLC)
        self.assertTrue(fidelity_at_least(MT5TickMode.REAL_TICKS, MT5TickMode.EVERY_TICK))
        self.assertFalse(fidelity_at_least(MT5TickMode.ONE_MINUTE_OHLC, MT5TickMode.REAL_TICKS))


if __name__ == "__main__":
    unittest.main()
