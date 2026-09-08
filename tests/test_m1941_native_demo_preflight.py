from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timezone
import unittest

from dusty.demo_session import AccountMode
from dusty.m194_native_demo_preflight import (
    NativeDemoPreflightStatus,
    NativeDemoTerminalSnapshot,
    assess_native_demo_preflight,
    capture_native_demo_snapshot,
)


COMMIT = "a" * 40
TERMINAL = r"C:\Program Files\Coinexx MT5 Terminal\terminal64.exe"
NOW = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)


def snapshot(**changes) -> NativeDemoTerminalSnapshot:
    row = dict(
        terminal_path=TERMINAL,
        terminal_build="6182",
        connected=True,
        terminal_trade_allowed=True,
        tradeapi_disabled=False,
        server="Coinexx-Demo",
        login=871471,
        account_mode=AccountMode.DEMO,
        account_trade_allowed=True,
        account_expert_allowed=True,
        account_currency="USD",
        leverage=500.0,
        symbol="EURUSD",
        symbol_spec_fingerprint="b" * 64,
        captured_at=NOW,
    )
    row.update(changes)
    return NativeDemoTerminalSnapshot(**row)


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self) -> None:
        self.initialized = False
        self.shutdown_called = False
        terminal_type = namedtuple("Terminal", "build connected trade_allowed tradeapi_disabled")
        account_type = namedtuple("Account", "server login trade_mode trade_allowed trade_expert currency leverage")
        spec_type = namedtuple(
            "Spec",
            "name digits point trade_mode trade_contract_size volume_min volume_max volume_step trade_tick_size trade_tick_value currency_base currency_profit currency_margin ignored",
        )
        self._terminal = terminal_type(6182, True, False, False)
        self._account = account_type("Coinexx-Demo", 871471, 0, True, True, "USD", 500.0)
        self._spec = spec_type("EURUSD", 5, 0.00001, 4, 100000.0, 0.01, 100.0, 0.01, 0.00001, 1.0, "EUR", "USD", "EUR", "ignored")

    def initialize(self, path):
        self.initialized = path == TERMINAL
        return self.initialized

    def terminal_info(self):
        return self._terminal

    def account_info(self):
        return self._account

    def symbol_info(self, symbol):
        return self._spec if symbol == "EURUSD" else None

    def shutdown(self):
        self.shutdown_called = True
        return True

    def order_send(self, request):  # pragma: no cover - must never be called
        raise AssertionError("M194.1 preflight must never call order_send")


class M1941NativeDemoPreflightTests(unittest.TestCase):
    def test_ready_requires_both_terminal_and_account_permissions(self):
        result = assess_native_demo_preflight(snapshot(), source_commit=COMMIT, expected_terminal_path=TERMINAL)
        self.assertIs(result.status, NativeDemoPreflightStatus.READY)
        self.assertEqual(result.blockers, ())
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)

    def test_terminal_trading_disabled_blocks_even_when_account_allows(self):
        result = assess_native_demo_preflight(
            snapshot(terminal_trade_allowed=False),
            source_commit=COMMIT,
            expected_terminal_path=TERMINAL,
        )
        self.assertIs(result.status, NativeDemoPreflightStatus.BLOCKED)
        self.assertIn("terminal_trading_disabled", result.blockers)

    def test_real_account_is_hard_preflight_blocker(self):
        result = assess_native_demo_preflight(
            snapshot(account_mode=AccountMode.REAL),
            source_commit=COMMIT,
            expected_terminal_path=TERMINAL,
        )
        self.assertIs(result.status, NativeDemoPreflightStatus.BLOCKED)
        self.assertIn("account_mode_not_demo:real", result.blockers)

    def test_external_python_disable_blocks(self):
        result = assess_native_demo_preflight(
            snapshot(tradeapi_disabled=True),
            source_commit=COMMIT,
            expected_terminal_path=TERMINAL,
        )
        self.assertIn("external_python_trading_disabled", result.blockers)

    def test_path_drift_blocks(self):
        result = assess_native_demo_preflight(
            snapshot(terminal_path=r"C:\Other\terminal64.exe"),
            source_commit=COMMIT,
            expected_terminal_path=TERMINAL,
        )
        self.assertIn("terminal_path_mismatch", result.blockers)

    def test_capture_is_read_only_and_preserves_terminal_permission(self):
        module = FakeMT5()
        row = capture_native_demo_snapshot(module, terminal_path=TERMINAL, symbol="eurusd", captured_at=NOW)
        self.assertTrue(module.initialized)
        self.assertTrue(module.shutdown_called)
        self.assertEqual(row.account_mode, AccountMode.DEMO)
        self.assertFalse(row.terminal_trade_allowed)
        self.assertTrue(row.account_trade_allowed)
        self.assertTrue(row.account_expert_allowed)
        self.assertEqual(len(row.symbol_spec_fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
