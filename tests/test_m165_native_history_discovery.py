from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest

from dusty.m165_native_history_discovery import discover_native_history


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 2, 10, tzinfo=UTC)


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    COPY_TICKS_ALL = -1

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._deals = (
            SimpleNamespace(symbol="EURUSD", type=0, time_msc=1_788_831_000_000, time=1_788_831_000,
                order=11, price=1.1002, volume=.01, commission=-.03, fee=0.0, swap=0.0, reason=3),
            SimpleNamespace(symbol="EURUSD", type=1, time_msc=1_788_917_400_000, time=1_788_917_400,
                order=12, price=1.1010, volume=.01, commission=-.03, fee=0.0, swap=-.01, reason=3),
        )
        self._orders = (
            SimpleNamespace(ticket=11, symbol="EURUSD", price_open=1.1000),
            SimpleNamespace(ticket=12, symbol="EURUSD", price_open=1.1012),
        )

    def terminal_info(self):
        self.calls.append("terminal_info")
        return SimpleNamespace(connected=True)

    def account_info(self):
        self.calls.append("account_info")
        return SimpleNamespace(trade_mode=0)

    def history_deals_get(self, start, end, group=None):
        self.calls.append("history_deals_get")
        return self._deals

    def history_orders_get(self, start, end, group=None):
        self.calls.append("history_orders_get")
        return self._orders

    def copy_ticks_range(self, symbol, start, end, flags):
        self.calls.append("copy_ticks_range")
        return (SimpleNamespace(bid=1.1, ask=1.1001),)


class M165NativeHistoryDiscoveryTests(unittest.TestCase):
    def test_discovers_required_history_fields_without_write_surface(self) -> None:
        mt5 = FakeMT5()
        report = discover_native_history(mt5, symbols=("EURUSD",), captured_at=NOW)
        row = report.symbols[0]
        self.assertTrue(report.read_only)
        self.assertTrue(report.demo_account)
        self.assertTrue(report.connected)
        self.assertEqual(row.execution_deals, 2)
        self.assertEqual(row.buy_deals, 1)
        self.assertEqual(row.sell_deals, 1)
        self.assertTrue(row.both_sides)
        self.assertEqual(row.matching_orders, 2)
        self.assertEqual(row.complete_cost_fields, 2)
        self.assertEqual(row.requested_fill_pairs, 2)
        self.assertEqual(row.historical_tick_windows_available, 2)
        self.assertFalse(any("send" in call or "trade" in call for call in mt5.calls))
        self.assertFalse(report.payload["authority"]["broker_write"])

    def test_live_account_is_reported_not_silently_accepted(self) -> None:
        mt5 = FakeMT5()
        mt5.account_info = lambda: SimpleNamespace(trade_mode=2)
        report = discover_native_history(mt5, symbols=("EURUSD",), captured_at=NOW)
        self.assertFalse(report.demo_account)

    def test_unavailable_or_unbounded_history_fails_closed(self) -> None:
        mt5 = FakeMT5()
        mt5.history_deals_get = lambda *args, **kwargs: None
        with self.assertRaisesRegex(RuntimeError, "history unavailable"):
            discover_native_history(mt5, symbols=("EURUSD",), captured_at=NOW)

        mt5 = FakeMT5()
        mt5._deals = mt5._deals * 6
        with self.assertRaisesRegex(RuntimeError, "bound exceeded"):
            discover_native_history(mt5, symbols=("EURUSD",), captured_at=NOW, max_rows=10)

    def test_timestamp_and_policy_bounds_fail_closed(self) -> None:
        mt5 = FakeMT5()
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            discover_native_history(mt5, symbols=("EURUSD",), captured_at=datetime(2026, 9, 8))
        with self.assertRaisesRegex(ValueError, "lookback_days"):
            discover_native_history(mt5, symbols=("EURUSD",), captured_at=NOW, lookback_days=0)


if __name__ == "__main__":
    unittest.main()
