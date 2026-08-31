from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.curriculum import (
    CohortPolicy,
    CurriculumCandidate,
    NoveltyState,
    RelevanceTier,
    SourcePlatform,
    TradingConcept,
    build_symbol_curriculum,
    compress_curriculum,
    make_method_insight,
    novelty_state,
    relevance,
    resolve_symbol,
)
from dusty.experience import SourceGrade


T0 = datetime(2026, 8, 31, tzinfo=timezone.utc)


def candidate(
    n: int,
    platform: SourcePlatform,
    *,
    symbol: str = "EURUSD",
    gain: float = 0.0,
    drawdown: float = 0.1,
    trades: int = 100,
    days: int = 365,
    popularity: int | None = None,
    family: str | None = None,
    strategy: str | None = None,
    known_at: datetime = T0,
    verified: bool = True,
    grade: SourceGrade = SourceGrade.LIVE,
) -> CurriculumCandidate:
    return CurriculumCandidate(
        source_id=f"s{n}",
        external_id=f"e{n}",
        platform=platform,
        symbol=resolve_symbol(symbol, {"EURUSD.A": "EURUSD"}),
        family_hash=family or f"f{n}",
        strategy_hash=strategy or f"h{n}",
        known_at=known_at,
        source_grade=grade,
        gain=gain,
        drawdown=drawdown,
        trade_count=trades,
        history_days=days,
        popularity=popularity,
        verified=verified,
        rules_understood=True,
    )


class SymbolCurriculumTests(unittest.TestCase):
    def test_m36_symbol_identity_requires_alias_for_broker_suffix(self):
        self.assertEqual(resolve_symbol("EUR/USD").canonical, "EURUSD")
        self.assertNotEqual(resolve_symbol("EURUSD.a").canonical, "EURUSD")
        aliased = resolve_symbol("EURUSD.a", {"EURUSD.A": "EURUSD"})
        self.assertEqual(aliased.canonical, "EURUSD")
        self.assertTrue(aliased.alias_verified)

    def test_m36_builds_exact_symbol_top20_without_future_leakage(self):
        rows = [candidate(i, SourcePlatform.FOREX_FACTORY, gain=float(i), known_at=T0) for i in range(30)]
        rows += [
            candidate(100, SourcePlatform.FOREX_FACTORY, symbol="GBPUSD", gain=9999.0),
            candidate(101, SourcePlatform.FOREX_FACTORY, gain=9998.0, known_at=T0 + timedelta(days=1)),
        ]
        snapshot = build_symbol_curriculum(rows, "EURUSD", as_of=T0)
        cohort = snapshot.cohorts[0]
        self.assertEqual(len(cohort.raw_top_gain), 20)
        self.assertEqual(cohort.raw_top_gain[0].gain, 29.0)
        self.assertTrue(all(item.symbol.canonical == "EURUSD" for item in cohort.raw_top_gain))
        self.assertNotIn("e101", {item.external_id for item in cohort.raw_top_gain})

    def test_m36_tradingview_popularity_is_discovery_not_quality_truth(self):
        rows = [
            candidate(1, SourcePlatform.TRADINGVIEW, gain=0.1, popularity=100_000, verified=False, grade=SourceGrade.BACKTEST),
            candidate(2, SourcePlatform.TRADINGVIEW, gain=0.08, popularity=10, verified=True, grade=SourceGrade.FORWARD_TEST, days=800, trades=700),
        ]
        cohort = build_symbol_curriculum(rows, "EURUSD", as_of=T0, policy=CohortPolicy(20, 20)).cohorts[0]
        self.assertEqual(cohort.popularity_top[0].external_id, "e1")
        self.assertEqual(cohort.research_top[0].external_id, "e2")

    def test_m37_relevance_separates_exact_related_and_transfer(self):
        self.assertIs(relevance("EURUSD", "EUR/USD"), RelevanceTier.EXACT)
        self.assertIs(relevance("EURUSD", "GBPUSD", related_symbols=("GBPUSD", "AUDUSD")), RelevanceTier.RELATED)
        self.assertIs(relevance("EURUSD", "XAUUSD"), RelevanceTier.TRANSFER)

    def test_m37_compression_counts_duplicates_and_family_variants(self):
        rows = [
            candidate(1, SourcePlatform.TRADINGVIEW, family="f1", strategy="h1"),
            candidate(2, SourcePlatform.MYFXBOOK, family="f1", strategy="h1"),
            candidate(3, SourcePlatform.FOREX_FACTORY, family="f1", strategy="h2"),
            candidate(4, SourcePlatform.QUANTPEDIA, family="f2", strategy="h3"),
        ]
        compressed = compress_curriculum(rows)
        self.assertEqual(len(compressed.representatives), 2)
        self.assertEqual(compressed.exact_duplicate_count, 1)
        self.assertEqual(compressed.family_variant_count, 1)
        self.assertIs(novelty_state(rows[0], known_strategy_hashes={"h1"}, known_family_hashes={"f1"}), NoveltyState.EXACT_DUPLICATE)
        self.assertIs(novelty_state(rows[2], known_strategy_hashes={"h1"}, known_family_hashes={"f1"}), NoveltyState.MEANINGFUL_VARIANT)

    def test_m38_knowledge_requires_explicit_concepts_and_provenance(self):
        insight = make_method_insight(
            insight_id="i1",
            target_symbol="EUR/USD",
            statement="Volatility expansion was used as a breakout filter.",
            concepts=(TradingConcept.BREAKOUT, TradingConcept.VOLATILITY),
            features=("atr", "range_break"),
            source_ids=("tv:1", "qp:2"),
            known_at=T0,
        )
        self.assertEqual(insight.target_symbol, "EURUSD")
        self.assertEqual(len(insight.source_ids), 2)
        with self.assertRaises(ValueError):
            make_method_insight(
                insight_id="bad",
                target_symbol="EURUSD",
                statement="unsupported",
                concepts=(),
                features=(),
                source_ids=("x",),
                known_at=T0,
            )


if __name__ == "__main__":
    unittest.main()
