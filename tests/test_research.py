from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dusty.experience import TradeSide
from dusty.research import (
    CandidateStatus,
    Clause,
    ExperimentGate,
    FeatureRow,
    RuleOp,
    SQLiteStrategyMemory,
    StrategySpec,
    run_experiment,
    screen,
)


T0 = datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc)


def spec(clauses=None, *, cost_bps=0.0):
    return StrategySpec(
        "trend-long",
        TradeSide.LONG,
        tuple(clauses or (Clause("trend", RuleOp.EQ, "up"),)),
        cost_bps=cost_bps,
    )


def rows():
    return tuple(
        FeatureRow.of(
            T0 + timedelta(minutes=i),
            {"trend": "up" if i % 2 == 0 else "down", "strength": i},
            0.002 if i % 4 == 0 else -0.001,
        )
        for i in range(40)
    )


class StrategyGrammarTests(unittest.TestCase):
    def test_m19_strategy_ir_is_hashable_and_order_independent(self):
        left = spec((Clause("trend", RuleOp.EQ, "up"), Clause("strength", RuleOp.GE, 5)))
        right = spec((Clause("strength", RuleOp.GE, 5), Clause("trend", RuleOp.EQ, "up")))
        self.assertEqual(left.strategy_hash, right.strategy_hash)
        self.assertEqual(len(left.strategy_hash), 64)
        with self.assertRaises(ValueError):
            StrategySpec("empty", TradeSide.LONG, ())

    def test_missing_feature_fails_clause_closed(self):
        clause = Clause("missing", RuleOp.GT, 1)
        self.assertFalse(clause.evaluate({"other": 2}))


class ExperimentTests(unittest.TestCase):
    def test_m20_experiment_is_deterministic_and_cost_aware(self):
        raw = run_experiment(spec(), rows())
        repeated = run_experiment(spec(), rows())
        costly = run_experiment(spec(cost_bps=5), rows())
        self.assertEqual(raw, repeated)
        self.assertGreater(raw.sample_count, 0)
        self.assertLess(costly.mean_return, raw.mean_return)
        self.assertEqual(len(raw.fingerprint), 64)

    def test_research_funnel_rejects_weak_candidates_early(self):
        result = run_experiment(spec(), rows()[:4])
        verdict = screen(result, ExperimentGate(min_samples=20, min_mean_return=0.01))
        self.assertFalse(verdict.passed)
        self.assertIn("insufficient_samples", verdict.reasons)
        self.assertIn("mean_return_failed", verdict.reasons)


class StrategyMemoryTests(unittest.TestCase):
    def test_m21_memory_deduplicates_and_keeps_graveyard_history(self):
        strategy = spec()
        result = run_experiment(strategy, rows())
        with tempfile.TemporaryDirectory() as tmp:
            memory = SQLiteStrategyMemory(Path(tmp) / "research.db")
            self.assertFalse(memory.seen(strategy.strategy_hash))
            memory.remember(strategy, result, CandidateStatus.CHALLENGER)
            self.assertTrue(memory.seen(strategy.strategy_hash))
            memory.remember(
                strategy,
                result,
                CandidateStatus.REJECTED,
                ("parameter_fragility", "weak_out_of_sample"),
            )
            latest = memory.latest(strategy.strategy_hash)
            self.assertIsNotNone(latest)
            self.assertIs(latest.status, CandidateStatus.REJECTED)
            self.assertIn("parameter_fragility", latest.reasons)
            self.assertEqual(len(memory.history(strategy.strategy_hash)), 2)
            self.assertIn(strategy.strategy_hash, memory.graveyard())
            self.assertTrue(memory.integrity_ok())
            memory.close()


if __name__ == "__main__":
    unittest.main()
