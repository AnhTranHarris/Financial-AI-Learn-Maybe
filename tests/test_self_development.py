from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.adaptive import (
    AcquisitionPolicy,
    CurriculumCycleMetrics,
    RegimeObservation,
    decide_acquisition,
    summarize_regimes,
)
from dusty.curriculum import TradingConcept, make_method_insight
from dusty.development import DevelopmentStatus, evaluate_hypotheses
from dusty.experience import TradeSide
from dusty.hypothesis import HypothesisSeed, compose_hypotheses
from dusty.reasoning_bridge import insights_to_snapshot
from dusty.research import Clause, ExperimentGate, ExperimentResult, RuleOp, StrategySpec


T0 = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)


def spec(name: str, feature: str, value: float) -> StrategySpec:
    return StrategySpec(name, TradeSide.LONG, (Clause(feature, RuleOp.GT, value),), horizon_steps=1, cost_bps=2.0)


class ReasoningBridgeTests(unittest.TestCase):
    def test_m39_bridge_is_point_in_time_bounded_and_symbol_conditioned(self):
        exact = make_method_insight(insight_id="exact", target_symbol="EURUSD", statement="exact lesson", concepts=(TradingConcept.TREND,), features=("trend",), source_ids=("ff:1",), known_at=T0)
        related = make_method_insight(insight_id="related", target_symbol="GBPUSD", statement="related lesson", concepts=(TradingConcept.TREND,), features=("trend",), source_ids=("ff:2",), known_at=T0)
        future = make_method_insight(insight_id="future", target_symbol="EURUSD", statement="future leak", concepts=(TradingConcept.TREND,), features=("trend",), source_ids=("ff:3",), known_at=T0 + timedelta(hours=1))
        snapshot = insights_to_snapshot((related, future, exact), snapshot_id="p1", target_symbol="EURUSD", at=T0, related_symbols=("GBPUSD",), limit=2)
        self.assertEqual(len(snapshot.items), 2)
        self.assertEqual(snapshot.items[0].key, "curriculum:exact")
        self.assertNotIn("curriculum:future", {item.key for item in snapshot.items})

    def test_m39_transfer_requires_explicit_opt_in(self):
        other = make_method_insight(insight_id="gold", target_symbol="XAUUSD", statement="cross asset", concepts=(TradingConcept.VOLATILITY,), features=("atr",), source_ids=("tv:1",), known_at=T0)
        blocked = insights_to_snapshot((other,), snapshot_id="b", target_symbol="EURUSD", at=T0)
        allowed = insights_to_snapshot((other,), snapshot_id="a", target_symbol="EURUSD", at=T0, include_transfer=True)
        self.assertEqual(len(blocked.items), 0)
        self.assertEqual(len(allowed.items), 1)


class HypothesisTests(unittest.TestCase):
    def test_m40_composer_is_bounded_and_preserves_ancestry(self):
        a = spec("a", "trend", 1.0)
        b = spec("b", "atr", 2.0)
        c = spec("c", "session", 1.0)
        seeds = (HypothesisSeed(a, ("qp:1",), "trend"), HypothesisSeed(b, ("tv:1",), "vol"), HypothesisSeed(c, ("ff:1",), "session"))
        insight = make_method_insight(insight_id="i1", target_symbol="EURUSD", statement="ATR used with trend continuation.", concepts=(TradingConcept.TREND, TradingConcept.VOLATILITY), features=("trend", "atr"), source_ids=("qp:1", "tv:1"), known_at=T0)
        drafts = compose_hypotheses(seeds, (insight,), target_symbol="EURUSD", max_candidates=2, max_clauses=3)
        self.assertEqual(len(drafts), 2)
        self.assertTrue(all(len(item.parent_hashes) == 2 for item in drafts))
        self.assertTrue(any("i1" in item.insight_ids for item in drafts))
        self.assertTrue(all("mt5_reconciliation_failure" in item.falsifiers for item in drafts))

    def test_m40_does_not_mix_directions_or_horizons(self):
        long = HypothesisSeed(spec("a", "trend", 1), ("a",), "f1")
        short_spec = StrategySpec("s", TradeSide.SHORT, (Clause("atr", RuleOp.GT, 1),))
        short = HypothesisSeed(short_spec, ("b",), "f2")
        self.assertEqual(compose_hypotheses((long, short), (), target_symbol="EURUSD"), ())


class DevelopmentTests(unittest.TestCase):
    def test_m41_tournament_turns_results_into_compact_lessons(self):
        seeds = (HypothesisSeed(spec("a", "trend", 1), ("a",), "f1"), HypothesisSeed(spec("b", "atr", 2), ("b",), "f2"))
        draft = compose_hypotheses(seeds, (), target_symbol="EURUSD")[0]
        good = ExperimentResult(draft.spec.strategy_hash, 100, 0.01, 1.0, 0.6, -0.02, "x")
        summary = evaluate_hypotheses((draft,), {draft.spec.strategy_hash: good}, ExperimentGate(min_samples=20, min_mean_return=0.0, min_hit_rate=0.5))
        self.assertEqual(summary.tested, 1)
        self.assertEqual(summary.promising, 1)
        self.assertIs(summary.lessons[0].status, DevelopmentStatus.PROMISING)


class AdaptiveCurriculumTests(unittest.TestCase):
    def test_m42_acquisition_requires_gap_and_consumed_backlog(self):
        policy = AcquisitionPolicy(max_batch=20, max_unclassified_backlog=5, max_untested_backlog=5)
        bootstrap = decide_acquisition(CurriculumCycleMetrics(0, 0, 0, 0), knowledge_gap="bootstrap EURUSD", requested_count=100, policy=policy)
        self.assertEqual(bootstrap.approved_count, 20)
        blocked = decide_acquisition(CurriculumCycleMetrics(20, 10, 1, 0), knowledge_gap="need more breakout examples", requested_count=20, policy=policy)
        self.assertEqual(blocked.approved_count, 0)
        self.assertEqual(blocked.reason, "classification_backlog")
        no_gap = decide_acquisition(CurriculumCycleMetrics(20, 20, 20, 5), knowledge_gap="", requested_count=20, policy=policy)
        self.assertEqual(no_gap.approved_count, 0)

    def test_m42_regime_summary_is_bounded_and_contextual(self):
        rows = [
            RegimeObservation("h1", ("london", "high_vol"), 0.01),
            RegimeObservation("h1", ("high_vol", "london"), -0.002),
            RegimeObservation("h1", ("asia", "low_vol"), -0.01),
        ]
        stats = summarize_regimes(rows)
        self.assertEqual(len(stats), 2)
        london = next(item for item in stats if "london" in item.tags)
        self.assertEqual(london.sample_count, 2)
        with self.assertRaises(ValueError):
            summarize_regimes((RegimeObservation("h1", ("a",), 0.0), RegimeObservation("h1", ("b",), 0.0)), max_regimes=1)


if __name__ == "__main__":
    unittest.main()
