from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dusty.experience import TradeSide
from dusty.library import KnowledgeKind, KnowledgeRecord, SourceRecord, SQLiteLearningLibrary
from dusty.reproduction import PerformanceClaim, ReproductionStatus, assess_reproduction
from dusty.research import Clause, FeatureRow, RuleOp, SQLiteStrategyMemory, StrategySpec, run_experiment


T0 = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)


def strategy() -> StrategySpec:
    return StrategySpec("stream", TradeSide.LONG, (Clause("trend", RuleOp.EQ, "up"),))


def row_stream(count: int):
    for i in range(count):
        yield FeatureRow.of(
            T0 + timedelta(minutes=i),
            {"trend": "up" if i % 2 == 0 else "down"},
            0.002 if i % 4 == 0 else -0.001,
        )


class ReproductionTests(unittest.TestCase):
    def test_m27_external_claim_must_match_dustys_independent_metrics(self):
        spec = strategy()
        result = run_experiment(spec, row_stream(200))
        matched = assess_reproduction(
            PerformanceClaim(
                strategy_hash=spec.strategy_hash,
                min_samples=50,
                mean_return=result.mean_return,
                hit_rate=result.hit_rate,
            ),
            result,
        )
        diverged = assess_reproduction(
            PerformanceClaim(
                strategy_hash=spec.strategy_hash,
                min_samples=50,
                mean_return=result.mean_return + 0.5,
                mean_return_tolerance=0.01,
            ),
            result,
        )
        self.assertIs(matched.status, ReproductionStatus.MATCHED)
        self.assertIs(diverged.status, ReproductionStatus.DIVERGED)


class KnowledgeRetrievalTests(unittest.TestCase):
    def test_m28_library_retrieves_small_relevant_working_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            library = SQLiteLearningLibrary(Path(tmp) / "library.db")
            library.register_source(
                SourceRecord("s", "research", "https://example.test", T0, "hash", "public")
            )
            records = (
                KnowledgeRecord.of(
                    "k1",
                    KnowledgeKind.METHOD,
                    "ATR can qualify volatility expansion.",
                    ("eurusd", "atr", "breakout"),
                    T0,
                    source_id="s",
                ),
                KnowledgeRecord.of(
                    "k2",
                    KnowledgeKind.FAILURE,
                    "A breakout variant failed after costs.",
                    ("eurusd", "breakout", "costs"),
                    T0 + timedelta(seconds=1),
                    source_id="s",
                ),
                KnowledgeRecord.of(
                    "k3",
                    KnowledgeKind.INDICATOR,
                    "RSI context.",
                    ("rsi", "mean_reversion"),
                    T0 + timedelta(seconds=2),
                    source_id="s",
                ),
            )
            for record in records:
                library.remember_knowledge(record)
            found = library.retrieve_knowledge(("eurusd", "breakout"), limit=2)
            self.assertEqual(len(found), 2)
            self.assertEqual(found[0].record_id, "k2")
            self.assertNotIn("k3", {item.record_id for item in found})
            library.close()


class StreamingResearchTests(unittest.TestCase):
    def test_m29_experiment_consumes_one_shot_stream_and_matches_tuple(self):
        spec = strategy()
        streamed = run_experiment(spec, row_stream(20_000))
        materialized = run_experiment(spec, tuple(row_stream(20_000)))
        self.assertEqual(streamed, materialized)
        self.assertEqual(streamed.sample_count, 10_000)

    def test_m29_strategy_memory_exposes_batched_iterators(self):
        spec = strategy()
        result = run_experiment(spec, row_stream(100))
        with tempfile.TemporaryDirectory() as tmp:
            memory = SQLiteStrategyMemory(Path(tmp) / "research.db")
            from dusty.research import CandidateStatus

            for _ in range(10):
                memory.remember(spec, result, CandidateStatus.REJECTED, ("weak",))
            self.assertEqual(len(tuple(memory.iter_history(batch_size=3))), 10)
            self.assertEqual(tuple(memory.iter_graveyard(batch_size=1)), (spec.strategy_hash,))
            memory.close()


if __name__ == "__main__":
    unittest.main()
