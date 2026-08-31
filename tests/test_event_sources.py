from __future__ import annotations

import unittest
from datetime import UTC, date, datetime

from dusty.events import ScheduledEvent, reconstruct_scheduled_event
from dusty.markets import AssetClass, InstrumentType, MarketIdentity, SymbolResearchProfile, same_economic_underlier
from dusty.news import NewsAccess, NewsItem, NewsSource, SourceRole, SymbolNewsRegistry, eligible_news_items


class EventSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.market = MarketIdentity.of(
            raw_symbol="EUR/USD",
            economic_underlier="EURUSD",
            asset_class=AssetClass.FX,
            instrument_type=InstrumentType.CFD,
            venue="demo-broker",
        )
        self.profile = SymbolResearchProfile.of(self.market, currencies=("EUR", "USD"), related_underliers=("GOLD",))
        self.free = NewsSource(
            "fed",
            "Federal Reserve",
            NewsAccess.FREE_PRIMARY,
            SourceRole.PRIMARY_FACT,
            "federal_reserve",
            currencies=("USD",),
        )
        self.paid = NewsSource(
            "paid-wire",
            "Paid Wire",
            NewsAccess.PAID,
            SourceRole.SECONDARY_CONTEXT,
            "paid-wire",
            currencies=("USD",),
        )
        self.registry = SymbolNewsRegistry((self.free, self.paid))
        self.t0 = datetime(2026, 1, 1, 12, tzinfo=UTC)

    def test_underlier_identity_is_not_instrument_identity(self) -> None:
        future = MarketIdentity.of(
            raw_symbol="GCZ26",
            economic_underlier="GOLD",
            asset_class=AssetClass.FUTURE,
            instrument_type=InstrumentType.FUTURE,
            venue="COMEX",
            contract="GCZ26",
            expiry=date(2026, 12, 29),
        )
        cfd = MarketIdentity.of(
            raw_symbol="XAUUSD",
            economic_underlier="GOLD",
            asset_class=AssetClass.METAL,
            instrument_type=InstrumentType.CFD,
        )
        self.assertTrue(same_economic_underlier(future, cfd))
        self.assertNotEqual(future.canonical_symbol, cfd.canonical_symbol)

    def test_free_only_and_symbol_relevance(self) -> None:
        items = (
            NewsItem.of(source_id="fed", external_id="1", published_at=self.t0, known_at=self.t0, headline="Fed update", currencies=("USD",)),
            NewsItem.of(source_id="paid-wire", external_id="2", published_at=self.t0, known_at=self.t0, headline="Paid update", currencies=("USD",)),
            NewsItem.of(source_id="fed", external_id="3", published_at=self.t0, known_at=self.t0, headline="JPY only", currencies=("JPY",)),
        )
        accepted = eligible_news_items(items, self.registry, self.profile, as_of=self.t0)
        self.assertEqual(tuple(item.external_id for item in accepted), ("1",))
        self.assertEqual(tuple(source.source_id for source in self.registry.eligible_sources(self.profile)), ("fed",))

    def test_point_in_time_scheduled_capsule(self) -> None:
        event = ScheduledEvent(
            event_id="us-cpi",
            scheduled_at=self.t0,
            known_at=self.t0,
            currencies=("USD",),
            category="inflation",
            forecast="2.8",
            previous="2.7",
            source_id="forex-factory-calendar",
        )
        prior = NewsItem.of(
            source_id="fed",
            external_id="pre",
            published_at=self.t0,
            known_at=self.t0,
            headline="Inflation context",
            currencies=("USD",),
            event_key="us-cpi",
        )
        future = NewsItem.of(
            source_id="fed",
            external_id="future",
            published_at=self.t0.replace(hour=13),
            known_at=self.t0.replace(hour=13),
            headline="Future reaction",
            currencies=("USD",),
            event_key="us-cpi",
        )
        capsule = reconstruct_scheduled_event(event, (prior, future), self.registry, self.profile, as_of=self.t0, capsule_id="cap-1")
        self.assertEqual(tuple(item.external_id for item in capsule.context_items), ("pre",))
        self.assertIn("fed", capsule.source_ids)

    def test_future_requires_contract(self) -> None:
        with self.assertRaises(ValueError):
            MarketIdentity.of(raw_symbol="GC", economic_underlier="GOLD", asset_class=AssetClass.FUTURE, instrument_type=InstrumentType.FUTURE)


if __name__ == "__main__":
    unittest.main()
