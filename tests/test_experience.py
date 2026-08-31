from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.experience import (
    ActionKind,
    ContextFact,
    MarketPrice,
    SourceGrade,
    SourceKind,
    TradeAction,
    TradeSide,
    behavior_signature,
    context_timeline,
    discover_archetypes,
    evaluate_counterfactuals,
    facts_as_of,
    forex_factory_calendar_fact,
    forex_factory_trade,
    myfxbook_trade,
    reconstruct_episode,
)


T0 = datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc)


def episode(identifier: str, *, entry: float, exit_: float, offset: int = 0):
    start = T0 + timedelta(hours=offset)
    return reconstruct_episode(
        identifier,
        "EURUSD",
        SourceKind.DUSTY,
        f"dusty://{identifier}",
        SourceGrade.BACKTEST,
        (
            TradeAction(start, ActionKind.ENTRY, TradeSide.LONG, entry),
            TradeAction(start + timedelta(minutes=10), ActionKind.EXIT, TradeSide.LONG, exit_),
        ),
        verified=True,
    )


class ExperienceSchemaTests(unittest.TestCase):
    def test_m13_episode_schema_computes_directional_outcome(self):
        long = episode("long", entry=1.1000, exit_=1.1110)
        self.assertAlmostEqual(long.return_fraction, 0.01)
        self.assertEqual(long.duration_minutes, 10)

        short = reconstruct_episode(
            "short",
            "EURUSD",
            SourceKind.DUSTY,
            "dusty://short",
            SourceGrade.BACKTEST,
            (
                TradeAction(T0, ActionKind.ENTRY, TradeSide.SHORT, 1.1000),
                TradeAction(T0 + timedelta(minutes=10), ActionKind.EXIT, TradeSide.SHORT, 1.0890),
            ),
        )
        self.assertAlmostEqual(short.return_fraction, 0.01)

    def test_m14_human_source_firewall_preserves_provenance(self):
        record = {
            "episode_id": "ff-1",
            "symbol": "EURUSD",
            "opened_at": T0.isoformat(),
            "closed_at": (T0 + timedelta(minutes=15)).isoformat(),
            "side": "buy",
            "entry_price": 1.1,
            "exit_price": 1.101,
            "account_type": "demo",
        }
        ff = forex_factory_trade(record, source_ref="https://www.forexfactory.com/trades")
        self.assertIs(ff.source, SourceKind.FOREX_FACTORY_TRADES)
        self.assertIs(ff.grade, SourceGrade.DEMO)
        self.assertFalse(ff.verified)

        record["episode_id"] = "mfx-1"
        record["test_type"] = "backtest"
        record.pop("account_type")
        mfx = myfxbook_trade(record, source_ref="https://www.myfxbook.com/strategies/example")
        self.assertIs(mfx.source, SourceKind.MYFXBOOK)
        self.assertIs(mfx.grade, SourceGrade.BACKTEST)

    def test_m15_point_in_time_context_never_exposes_late_information(self):
        scheduled = T0 + timedelta(minutes=5)
        preknown = forex_factory_calendar_fact(
            event_id="usd-event",
            scheduled_at=scheduled,
            known_at=T0 - timedelta(hours=1),
            currency="USD",
            impact="high",
            forecast="100",
            source_ref="https://www.forexfactory.com/calendar",
        )
        late_actual = ContextFact(
            "event_actual",
            "120",
            known_at=scheduled + timedelta(seconds=1),
            effective_at=scheduled,
            source=SourceKind.FOREX_FACTORY_CALENDAR,
            source_ref="https://www.forexfactory.com/calendar",
            category="calendar",
        )
        self.assertEqual(facts_as_of((preknown, late_actual), T0), (preknown,))

        trade = episode("pit", entry=1.1, exit_=1.101)
        timeline = context_timeline(trade, (preknown, late_actual))
        self.assertIn(preknown, timeline[0].facts)
        self.assertNotIn(late_actual, timeline[0].facts)
        self.assertIn(late_actual, timeline[-1].facts)

    def test_m16_reconstruction_rejects_invalid_trade_story(self):
        with self.assertRaises(ValueError):
            reconstruct_episode(
                "bad",
                "EURUSD",
                SourceKind.OTHER,
                "source://bad",
                SourceGrade.UNKNOWN,
                (
                    TradeAction(T0 + timedelta(minutes=5), ActionKind.ENTRY, TradeSide.LONG, 1.1),
                    TradeAction(T0, ActionKind.EXIT, TradeSide.LONG, 1.2),
                ),
            )

    def test_m17_behavioral_archetypes_are_outcome_free_signatures(self):
        winners_and_losers = (
            episode("a", entry=1.1, exit_=1.11),
            episode("b", entry=1.1, exit_=1.09, offset=1),
        )
        self.assertEqual(behavior_signature(winners_and_losers[0]), behavior_signature(winners_and_losers[1]))
        found = discover_archetypes(winners_and_losers)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].count, 2)
        self.assertEqual(found[0].win_rate, 0.5)

    def test_m18_counterfactuals_use_only_observed_prices(self):
        trade = episode("cf", entry=1.1, exit_=1.11)
        prices = (
            MarketPrice(T0 - timedelta(minutes=1), 1.099),
            MarketPrice(T0, 1.1),
            MarketPrice(T0 + timedelta(minutes=5), 1.105),
            MarketPrice(T0 + timedelta(minutes=10), 1.11),
            MarketPrice(T0 + timedelta(minutes=11), 1.108),
        )
        cases = evaluate_counterfactuals(trade, prices)
        labels = {case.label for case in cases}
        self.assertIn("actual_path", labels)
        self.assertIn("enter_one_observation_earlier", labels)
        self.assertIn("enter_one_observation_later", labels)
        self.assertIn("exit_one_observation_earlier", labels)
        self.assertIn("exit_one_observation_later", labels)
        self.assertIn("no_trade", labels)
        self.assertEqual(next(case.return_fraction for case in cases if case.label == "no_trade"), 0.0)


if __name__ == "__main__":
    unittest.main()
