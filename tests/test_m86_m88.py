from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dusty.forecast_dataset import (
    ForecastContextValue,
    ForecastMarketBar,
    build_feature_frame,
    build_rolling_examples,
    training_examples_as_of,
)
from dusty.forecasting_v2 import (
    ForecastKey,
    ForecastModelIdentity,
    ForecastTargetKind,
    ProbabilisticForecast,
    QuantilePoint,
)
from dusty.market_clock import (
    MARKET_CLOSED_RETCODE,
    BrokerMarketSchedule,
    MarketClockObservation,
    MarketClockState,
    SessionKind,
    SymbolTradeMode,
    WeeklySession,
    assess_market_clock,
    parse_mt5_session_export,
)


UTC = timezone.utc
MONDAY = datetime(2026, 8, 31, tzinfo=UTC)


def model() -> ForecastModelIdentity:
    return ForecastModelIdentity("kronos", "kronos-small", "1", "a" * 64, "b" * 64)


def forecast(**overrides: object) -> ProbabilisticForecast:
    issued = MONDAY + timedelta(hours=12)
    values = dict(
        model=model(),
        key=ForecastKey("EURUSD", "M15", issued, issued, 4, ForecastTargetKind.RETURN, "trend"),
        origin_value=1.10,
        quantiles=(QuantilePoint(0.1, -0.002), QuantilePoint(0.5, 0.001), QuantilePoint(0.9, 0.004)),
        probability_up=0.61,
        training_cutoff=issued,
        valid_until=issued + timedelta(hours=1),
        context_hash="c" * 64,
    )
    values.update(overrides)
    return ProbabilisticForecast(**values)


class M86ForecastConstitutionTests(unittest.TestCase):
    def test_forecast_identity_binds_symbol_horizon_model_and_context(self):
        item = forecast()
        self.assertEqual(len(item.fingerprint), 64)
        self.assertEqual(item.median, 0.001)
        self.assertEqual(item.quantile(0.1), -0.002)

    def test_future_training_data_is_rejected(self):
        issued = MONDAY + timedelta(hours=12)
        with self.assertRaisesRegex(ValueError, "training data"):
            forecast(training_cutoff=issued + timedelta(seconds=1))

    def test_crossing_quantiles_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "cannot cross"):
            forecast(quantiles=(QuantilePoint(0.1, 0.0), QuantilePoint(0.5, 0.2), QuantilePoint(0.9, 0.1)))


class M87PointInTimeDatasetTests(unittest.TestCase):
    def bars(self) -> tuple[ForecastMarketBar, ...]:
        return tuple(
            ForecastMarketBar(
                "EURUSD",
                "M15",
                MONDAY + timedelta(minutes=15 * index),
                MONDAY + timedelta(minutes=15 * (index + 1)),
                1.0 + index * 0.01,
                1.02 + index * 0.01,
                0.99 + index * 0.01,
                1.01 + index * 0.01,
                2 + index,
                100 + index,
            )
            for index in range(8)
        )

    def test_feature_frame_rejects_future_news_context(self):
        as_of = self.bars()[3].available_at
        future = ForecastContextValue("news_score", 1.0, as_of + timedelta(seconds=1), "official_news")
        with self.assertRaisesRegex(ValueError, "future forecast context"):
            build_feature_frame(self.bars()[:4], as_of=as_of, context=(future,))

    def test_rolling_examples_never_train_before_target_is_known(self):
        examples = build_rolling_examples(self.bars(), lookback=3, horizon_steps=2)
        self.assertTrue(examples)
        first = examples[0]
        self.assertGreater(first.target_known_at, first.features.as_of)
        self.assertNotIn(first, training_examples_as_of(examples, cutoff=first.features.as_of))
        self.assertIn(first, training_examples_as_of(examples, cutoff=first.target_known_at))


def schedule(*, captured_at: datetime = MONDAY) -> BrokerMarketSchedule:
    sessions = (
        WeeklySession(SessionKind.TRADE, 0, 0, 9 * 3600, 12 * 3600),
        WeeklySession(SessionKind.TRADE, 0, 1, 13 * 3600, 17 * 3600),
        WeeklySession(SessionKind.QUOTE, 0, 0, 8 * 3600, 18 * 3600),
    )
    return BrokerMarketSchedule("Broker", "Broker-Demo", "EURUSD", captured_at, 0, sessions)


class M88MarketClockTests(unittest.TestCase):
    def test_scheduled_closure_is_normal_and_research_continues(self):
        at = MONDAY + timedelta(hours=7)
        result = assess_market_clock(schedule(), MarketClockObservation(at, None, SymbolTradeMode.FULL, last_trade_retcode=MARKET_CLOSED_RETCODE))
        self.assertEqual(result.state, MarketClockState.SCHEDULED_CLOSED)
        self.assertTrue(result.normal_condition)
        self.assertFalse(result.new_entries_authorized)
        self.assertTrue(result.research_authorized)

    def test_session_break_is_not_a_system_fault(self):
        at = MONDAY + timedelta(hours=12, minutes=30)
        result = assess_market_clock(schedule(), MarketClockObservation(at, at - timedelta(minutes=40), SymbolTradeMode.FULL))
        self.assertEqual(result.state, MarketClockState.SESSION_BREAK)
        self.assertTrue(result.normal_condition)

    def test_open_market_requires_fresh_native_tick(self):
        at = MONDAY + timedelta(hours=10)
        stale = assess_market_clock(schedule(), MarketClockObservation(at, at - timedelta(minutes=10), SymbolTradeMode.FULL))
        fresh = assess_market_clock(schedule(), MarketClockObservation(at, at - timedelta(seconds=5), SymbolTradeMode.FULL))
        self.assertEqual(stale.state, MarketClockState.UNEXPECTED_STALE_MARKET)
        self.assertFalse(stale.normal_condition)
        self.assertEqual(fresh.state, MarketClockState.OPEN)
        self.assertTrue(fresh.new_entries_authorized)

    def test_market_closed_retcode_during_scheduled_open_is_anomaly(self):
        at = MONDAY + timedelta(hours=10)
        result = assess_market_clock(
            schedule(),
            MarketClockObservation(at, at - timedelta(seconds=5), SymbolTradeMode.FULL, last_trade_retcode=MARKET_CLOSED_RETCODE),
        )
        self.assertEqual(result.state, MarketClockState.HALTED)
        self.assertFalse(result.normal_condition)

    def test_broker_holiday_closes_only_that_schedule_date(self):
        base = schedule()
        holiday = BrokerMarketSchedule(
            base.broker,
            base.server,
            base.symbol,
            base.captured_at,
            base.server_utc_offset_seconds,
            base.sessions,
            (date(2026, 8, 31),),
        )
        at = MONDAY + timedelta(hours=10)
        result = assess_market_clock(holiday, MarketClockObservation(at, None, SymbolTradeMode.FULL))
        self.assertEqual(result.state, MarketClockState.SCHEDULED_CLOSED)

    def test_future_schedule_snapshot_cannot_leak_into_reasoning(self):
        at = MONDAY + timedelta(hours=10)
        result = assess_market_clock(
            schedule(captured_at=at + timedelta(seconds=1)),
            MarketClockObservation(at, at - timedelta(seconds=5), SymbolTradeMode.FULL),
        )
        self.assertEqual(result.state, MarketClockState.UNKNOWN)
        self.assertIn("market_schedule_not_yet_known", result.reasons)

    def test_mt5_session_export_preserves_broker_schedule_identity(self):
        captured = int(MONDAY.timestamp())
        tick = int((MONDAY + timedelta(hours=9)).timestamp())
        text = (
            "schema,terminal_build,broker,server,symbol,captured_epoch,utc_offset_seconds,kind,weekday,session_index,from_seconds,to_seconds,trade_mode,last_tick_epoch\n"
            f"dusty-session-v1,5000,Broker,Broker-Demo,EURUSD,{captured},0,trade,0,0,32400,61200,4,{tick}\n"
            f"dusty-session-v1,5000,Broker,Broker-Demo,EURUSD,{captured},0,quote,0,0,28800,64800,4,{tick}\n"
        )
        result = parse_mt5_session_export(text)
        self.assertEqual(result.terminal_build, 5000)
        self.assertEqual(result.schedule.symbol, "EURUSD")
        self.assertEqual(result.trade_mode, SymbolTradeMode.FULL)

    def test_long_only_trade_mode_preserves_directional_permission(self):
        at = MONDAY + timedelta(hours=10)
        result = assess_market_clock(
            schedule(),
            MarketClockObservation(at, at - timedelta(seconds=5), SymbolTradeMode.LONG_ONLY),
        )
        self.assertEqual(result.state, MarketClockState.TRADE_RESTRICTED)
        self.assertTrue(result.long_entries_authorized)
        self.assertFalse(result.short_entries_authorized)

    def test_mt5_session_probe_is_read_only(self):
        source = Path("mt5/DustySessionProbe.mq5").read_text(encoding="utf-8")
        self.assertIn("SymbolInfoSessionTrade", source)
        self.assertIn("SymbolInfoSessionQuote", source)
        self.assertIn("(day+6)%7", source)
        self.assertNotIn("OrderSend", source)


if __name__ == "__main__":
    unittest.main()
