from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dusty.demo_execution import DemoMT5ExecutionAdapter
from dusty.demo_session import AccountMode, DemoSession, SessionFault, SessionIdentity
from dusty.execution_lifecycle import BrokerExecutionSnapshot, ExecutionState, PositionSnapshot, SQLiteExecutionLedger, assess_position_protection, close_by_position_request
from dusty.experience import TradeSide
from dusty.order_intent import MT5PreflightAdapter, OrderIntent
from dusty.position_actions import (
    MT5PositionActionPreflightAdapter,
    PositionActionIntent,
    PositionActionKind,
)
from dusty.strategy_v3 import OrderStyle


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


def identity(*, mode=AccountMode.DEMO, login=123, allowed=True, fingerprint="spec-a"):
    return SessionIdentity("terminal.exe", "5000", "Broker-Demo", login, mode, "USD", 100, allowed, allowed, 2, fingerprint, NOW)


def intent(session):
    return OrderIntent("s" * 64, session.identity.fingerprint, "EURUSD", TradeSide.LONG, 0.1, 1.1000, 1.0950, 1.1100, 0.0025, 60.0, True, 1.0, True, True, NOW, NOW + timedelta(minutes=2), 1)


class FakeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    ORDER_TYPE_BUY_STOP_LIMIT = 6
    ORDER_TYPE_SELL_STOP_LIMIT = 7
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    TRADE_ACTION_SLTP = 6
    TRADE_ACTION_REMOVE = 7
    ORDER_TIME_GTC = 0
    ORDER_TIME_SPECIFIED = 2
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(self, *, send_result=True):
        self.send_calls = 0
        self.send_result = send_result
        self.initialized = False

    def initialize(self, path):
        self.initialized = True
        return True

    def shutdown(self):
        self.initialized = False

    def symbol_info_tick(self, symbol):
        self.assert_connected()
        return SimpleNamespace(ask=1.1001, bid=1.0999)

    def order_calc_profit(self, order_type, symbol, volume, entry, stop):
        self.assert_connected()
        return -50.0

    def order_calc_margin(self, order_type, symbol, volume, entry):
        self.assert_connected()
        return 100.0

    def order_check(self, request):
        self.assert_connected()
        return SimpleNamespace(retcode=0)

    def order_send(self, request):
        self.assert_connected()
        self.send_calls += 1
        if not self.send_result:
            return None
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=10, deal=20, comment="done")

    def assert_connected(self):
        if not self.initialized:
            raise AssertionError("MT5 call requires initialized connection")


class SessionTests(unittest.TestCase):
    def test_demo_to_live_drift_latches_permanently(self):
        session = DemoSession(identity())
        failed = session.verify(identity(mode=AccountMode.REAL))
        self.assertFalse(failed.valid)
        self.assertIn(SessionFault.MODE_DRIFT, failed.faults)
        self.assertFalse(session.verify(identity()).valid)
        self.assertFalse(session.broker_write_authorized)

    def test_permission_loss_latches(self):
        session = DemoSession(identity())
        session.verify(identity(allowed=False))
        self.assertIn(SessionFault.PERMISSION_LOSS, session.faults)


class IntentAndExecutionTests(unittest.TestCase):
    def test_preflight_is_read_only_and_uses_same_connection_broker_loss(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        def connected_identity():
            fake.assert_connected()
            return identity()
        adapter = MT5PreflightAdapter(fake, session, connected_identity)
        result = adapter.check(intent(session), at=NOW + timedelta(seconds=10))
        self.assertTrue(result.passed)
        self.assertEqual(result.loss_at_stop, 50.0)
        self.assertFalse(hasattr(adapter, "order_send"))

    def test_governance_failure_never_initializes_broker(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        base = intent(session)
        denied = OrderIntent(base.strategy_hash, base.session_fingerprint, base.symbol, base.side, base.volume, base.reference_price, base.stop_price, base.target_price, base.approved_risk_fraction, base.allowed_loss, False, base.growth_multiplier, base.risk_approved, base.guardian_approved, base.created_at, base.expires_at, base.filling_mode)
        result = MT5PreflightAdapter(fake, session, lambda: identity()).check(denied, at=NOW)
        self.assertFalse(result.passed)
        self.assertFalse(fake.initialized)

    def test_governed_pending_long_and_short_use_broker_native_order_types(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        common = dict(
            strategy_hash="s" * 64,
            session_fingerprint=session.identity.fingerprint,
            symbol="EURUSD",
            volume=0.1,
            approved_risk_fraction=0.0025,
            allowed_loss=60.0,
            pm_approved=True,
            growth_multiplier=1.0,
            risk_approved=True,
            guardian_approved=True,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=2),
            filling_mode=1,
            order_style=OrderStyle.LIMIT,
            pending_expiry=NOW + timedelta(hours=1),
        )
        long_intent = OrderIntent(
            side=TradeSide.LONG,
            reference_price=1.0990,
            stop_price=1.0950,
            target_price=1.1100,
            **common,
        )
        short_intent = OrderIntent(
            side=TradeSide.SHORT,
            reference_price=1.1010,
            stop_price=1.1050,
            target_price=1.0900,
            **common,
        )
        adapter = MT5PreflightAdapter(fake, session, lambda: identity())
        long_result = adapter.check(long_intent, at=NOW)
        short_result = adapter.check(short_intent, at=NOW)
        self.assertTrue(long_result.passed)
        self.assertTrue(short_result.passed)
        self.assertEqual(long_result.request_dict()["type"], fake.ORDER_TYPE_BUY_LIMIT)
        self.assertEqual(short_result.request_dict()["type"], fake.ORDER_TYPE_SELL_LIMIT)
        self.assertEqual(long_result.request_dict()["action"], fake.TRADE_ACTION_PENDING)

    def test_stop_limit_trigger_and_limit_geometry_fails_closed(self):
        session = DemoSession(identity())
        common = dict(
            strategy_hash="s" * 64,
            session_fingerprint=session.identity.fingerprint,
            symbol="EURUSD",
            side=TradeSide.LONG,
            volume=0.1,
            reference_price=1.1010,
            stop_price=1.0950,
            target_price=1.1100,
            approved_risk_fraction=0.0025,
            allowed_loss=60.0,
            pm_approved=True,
            growth_multiplier=1.0,
            risk_approved=True,
            guardian_approved=True,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=2),
            filling_mode=1,
            order_style=OrderStyle.STOP_LIMIT,
            pending_expiry=NOW + timedelta(hours=1),
        )
        with self.assertRaises(ValueError):
            OrderIntent(stop_limit_price=1.1020, **common)

        valid = OrderIntent(stop_limit_price=1.1005, **common)
        result = MT5PreflightAdapter(FakeMT5(), session, lambda: identity()).check(valid, at=NOW)
        self.assertTrue(result.passed)
        self.assertEqual(result.request_dict()["stoplimit"], 1.1005)

    def test_only_demo_adapter_sends_and_replay_is_blocked(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        connected = lambda: identity()
        preflight = MT5PreflightAdapter(fake, session, connected).check(intent(session), at=NOW)
        ledger = SQLiteExecutionLedger()
        try:
            adapter = DemoMT5ExecutionAdapter(fake, session, connected, ledger)
            result = adapter.send(preflight, at=NOW + timedelta(seconds=1))
            self.assertEqual(result.state, ExecutionState.FILLED)
            self.assertEqual(fake.send_calls, 1)
            with self.assertRaises(Exception):
                adapter.send(preflight, at=NOW + timedelta(seconds=2))
            self.assertEqual(fake.send_calls, 1)
            self.assertFalse(adapter.live_write_authorized)
        finally:
            ledger.close()

    def test_position_protection_and_partial_close_share_single_demo_send_adapter(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        protection = PositionActionIntent(
            "s" * 64,
            session.identity.fingerprint,
            PositionActionKind.TIGHTEN_STOP,
            "EURUSD",
            TradeSide.LONG,
            42,
            0,
            0.1,
            0.0,
            1.095,
            1.100,
            1.110,
            True,
            True,
            True,
            NOW,
            NOW + timedelta(minutes=1),
            1,
        )
        preflight_adapter = MT5PositionActionPreflightAdapter(fake, session, lambda: identity())
        preflight = preflight_adapter.check(protection, at=NOW)
        self.assertTrue(preflight.passed)
        self.assertFalse(hasattr(preflight_adapter, "order_send"))
        ledger = SQLiteExecutionLedger()
        try:
            result = DemoMT5ExecutionAdapter(fake, session, lambda: identity(), ledger).send(
                preflight, at=NOW + timedelta(seconds=1)
            )
            self.assertEqual(result.state, ExecutionState.PROTECTED)
            self.assertEqual(fake.send_calls, 1)
        finally:
            ledger.close()

    def test_stop_widening_is_rejected_before_broker_connection(self):
        session = DemoSession(identity())
        with self.assertRaises(ValueError):
            PositionActionIntent(
                "s" * 64,
                session.identity.fingerprint,
                PositionActionKind.TIGHTEN_STOP,
                "EURUSD",
                TradeSide.LONG,
                42,
                0,
                0.1,
                0.0,
                1.095,
                1.090,
                1.110,
                True,
                True,
                True,
                NOW,
                NOW + timedelta(minutes=1),
                1,
            )

    def test_drift_on_send_connection_causes_zero_send_calls(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        preflight = MT5PreflightAdapter(fake, session, lambda: identity()).check(intent(session), at=NOW)
        ledger = SQLiteExecutionLedger()
        try:
            adapter = DemoMT5ExecutionAdapter(fake, session, lambda: identity(mode=AccountMode.REAL), ledger)
            with self.assertRaises(PermissionError):
                adapter.send(preflight, at=NOW + timedelta(seconds=1))
            self.assertEqual(fake.send_calls, 0)
        finally:
            ledger.close()

    def test_crash_after_send_reservation_never_auto_retries(self):
        session = DemoSession(identity())
        fake = FakeMT5(send_result=False)
        preflight = MT5PreflightAdapter(fake, session, lambda: identity()).check(intent(session), at=NOW)
        ledger = SQLiteExecutionLedger()
        try:
            adapter = DemoMT5ExecutionAdapter(fake, session, lambda: identity(), ledger)
            with self.assertRaises(RuntimeError):
                adapter.send(preflight, at=NOW + timedelta(seconds=1))
            self.assertEqual(ledger.get(preflight.intent.intent_hash).state, ExecutionState.SENT_UNKNOWN)
            self.assertEqual(fake.send_calls, 1)
            self.assertEqual(ledger.reconcile_unknown(preflight.intent.intent_hash, (), at=NOW + timedelta(seconds=2)).state, ExecutionState.SENT_UNKNOWN)
            with self.assertRaises(Exception):
                adapter.send(preflight, at=NOW + timedelta(seconds=3))
            self.assertEqual(fake.send_calls, 1)
            reconciled = ledger.reconcile_unknown(preflight.intent.intent_hash, (BrokerExecutionSnapshot(preflight.intent.client_tag, order_ticket=10, deal_ticket=20, position_ticket=30),), at=NOW + timedelta(seconds=4))
            self.assertEqual(reconciled.state, ExecutionState.FILLED)
        finally:
            ledger.close()


class ProtectionTests(unittest.TestCase):
    def test_missing_stop_is_not_protected(self):
        self.assertFalse(assess_position_protection(PositionSnapshot(123, "EURUSD", TradeSide.LONG, 0.1, 0.0)).protected)

    def test_close_request_targets_actual_position_ticket(self):
        fake = FakeMT5()
        request = close_by_position_request(PositionSnapshot(123, "EURUSD", TradeSide.LONG, 0.1, 1.09), market_price=1.1, filling_mode=1, magic=42, module=fake)
        self.assertEqual(request["position"], 123)
        self.assertEqual(request["type"], fake.ORDER_TYPE_SELL)


if __name__ == "__main__":
    unittest.main()
