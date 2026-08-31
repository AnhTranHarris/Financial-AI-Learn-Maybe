from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from dusty.event_reasoning import scenarios_to_snapshot
from dusty.event_research import (
    LiquidityState,
    MarketReaction,
    StrategyEventObservation,
    TradingSession,
    assess_session_repricing,
    summarize_reaction_windows,
    summarize_strategy_event_interactions,
)
from dusty.markets import AssetClass, InstrumentType, MarketIdentity, SymbolResearchProfile
from dusty.news import NewsAccess, NewsItem, NewsSource, SourceRole, SymbolNewsRegistry
from dusty.scenario import ScenarioState, TransmissionChannel, cluster_unscheduled_news, make_scenario


class ScenarioResearchTests(unittest.TestCase):
    def setUp(self) -> None:
        market = MarketIdentity.of(raw_symbol="WTI", economic_underlier="WTI", asset_class=AssetClass.ENERGY, instrument_type=InstrumentType.CFD)
        self.profile = SymbolResearchProfile.of(market, currencies=("USD",), related_underliers=("BRENT",))
        self.registry = SymbolNewsRegistry(
            (
                NewsSource("a", "Primary A", NewsAccess.FREE_PRIMARY, SourceRole.PRIMARY_FACT, "group-a", underliers=("WTI",)),
                NewsSource("b", "Context B", NewsAccess.FREE_PUBLIC_API, SourceRole.SECONDARY_CONTEXT, "group-b", underliers=("WTI",)),
                NewsSource("c", "Syndicated A", NewsAccess.FREE_PUBLIC_FEED, SourceRole.SECONDARY_CONTEXT, "group-a", underliers=("WTI",)),
            )
        )
        self.t0 = datetime(2026, 1, 1, 1, tzinfo=UTC)

    def test_cluster_deduplicates_headlines_and_counts_independence(self) -> None:
        items = (
            NewsItem.of(source_id="a", external_id="1", published_at=self.t0, known_at=self.t0, headline="Shipping route disrupted", underliers=("WTI",), event_key="shipping-risk"),
            NewsItem.of(source_id="c", external_id="2", published_at=self.t0, known_at=self.t0, headline="Shipping route disrupted", underliers=("WTI",), event_key="shipping-risk"),
            NewsItem.of(source_id="b", external_id="3", published_at=self.t0, known_at=self.t0, headline="Oil market watches shipping risk", underliers=("WTI",), event_key="shipping-risk"),
        )
        clusters = cluster_unscheduled_news(items, self.registry, self.profile, as_of=self.t0)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].duplicate_count, 1)
        self.assertEqual(clusters[0].publisher_groups, ("group-a", "group-b"))
        self.assertEqual(clusters[0].corroboration.value, "corroborated")

    def test_scenario_requires_falsification_and_never_authorizes_broker(self) -> None:
        item = NewsItem.of(source_id="a", external_id="1", published_at=self.t0, known_at=self.t0, headline="Shipping risk", underliers=("WTI",), event_key="shipping-risk")
        cluster = cluster_unscheduled_news((item,), self.registry, self.profile, as_of=self.t0)[0]
        scenario = make_scenario(
            cluster,
            scenario_id="s1",
            state=ScenarioState.SUPPLY_DISRUPTION,
            premise="Shipping disruption could increase supply risk premium.",
            transmission=(TransmissionChannel.SHIPPING,),
            confirmations=("Brent and WTI confirm after liquidity returns",),
            invalidations=("Oil rejects the move despite higher liquidity",),
        )
        self.assertFalse(scenario.broker_write_authorized)
        snapshot = scenarios_to_snapshot((scenario,), snapshot_id="snap", target_symbol="WTI", at=self.t0)
        self.assertEqual(len(snapshot.items), 1)
        self.assertFalse(snapshot.items[0].value["broker_write_authorized"])

    def test_low_to_high_liquidity_repricing_is_research_not_signal(self) -> None:
        rows = (
            MarketReaction("shipping-risk", "WTI", 0, 0.002, 3.0, 10.0, TradingSession.ASIA, LiquidityState.LOW),
            MarketReaction("shipping-risk", "WTI", 180, 0.006, 1.5, 40.0, TradingSession.LONDON, LiquidityState.HIGH),
        )
        assessment = assess_session_repricing(rows)
        self.assertTrue(assessment.sufficient)
        self.assertTrue(assessment.continuation)
        windows = summarize_reaction_windows(rows, windows=(5, 240))
        self.assertEqual(tuple(item.sample_count for item in windows), (1, 2))

    def test_strategy_event_interactions_are_bounded(self) -> None:
        observations = (
            StrategyEventObservation("hash", "geopolitical", ScenarioState.ESCALATION, TradingSession.LONDON, 0.01),
            StrategyEventObservation("hash", "geopolitical", ScenarioState.ESCALATION, TradingSession.LONDON, -0.002),
        )
        stats = summarize_strategy_event_interactions(observations)
        self.assertEqual(stats[0].sample_count, 2)
        self.assertAlmostEqual(stats[0].mean_return, 0.004)
        with self.assertRaises(ValueError):
            summarize_strategy_event_interactions(
                observations + (StrategyEventObservation("other", "geopolitical", ScenarioState.ESCALATION, TradingSession.LONDON, 0.01),)
            )

    def test_future_scenario_does_not_leak(self) -> None:
        item = NewsItem.of(source_id="a", external_id="1", published_at=self.t0, known_at=self.t0, headline="Risk", underliers=("WTI",), event_key="risk")
        cluster = cluster_unscheduled_news((item,), self.registry, self.profile, as_of=self.t0)[0]
        scenario = make_scenario(
            cluster,
            scenario_id="future",
            state=ScenarioState.CONTINUATION,
            premise="Risk continues.",
            transmission=(TransmissionChannel.SHIPPING,),
            confirmations=("market confirms",),
            invalidations=("market rejects",),
        )
        snapshot = scenarios_to_snapshot((scenario,), snapshot_id="past", target_symbol="WTI", at=self.t0 - timedelta(seconds=1))
        self.assertEqual(len(snapshot.items), 0)


if __name__ == "__main__":
    unittest.main()
