from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dusty.demo_execution import DemoMT5ExecutionAdapter
from dusty.demo_session import AccountMode, DemoSession, SessionFault, SessionIdentity
from dusty.execution_lifecycle import (
    BrokerExecutionSnapshot,
    ExecutionState,
    PositionSnapshot,
    SQLiteExecutionLedger,
    assess_position_protection,
    close_by_position_request,
)
from dusty.experience import TradeSide
from dusty.order_intent import MT5PreflightAdapter, OrderIntent


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


def identity(*, mode=AccountMode.DEMO, login=123, allowed=True, fingerprint="spec-a"):
    return SessionIdentity(
        terminal_path="terminal.exe",
        terminal_build="5000",
        server="Broker-Demo",
        login=login,
        account_mode=mode,
        account_currency="USD",
        leverage=100,
        trade_allowed=allowed,
        expert_trading_allowed=allowed,
        margin_mode=2,
        symbol_spec_fingerprint=fingerprint,
        captured_at=NOW,
    )


def intent(session):
    return OrderIntent(
        strategy_hash="s" * 64,
        session_fingerprint=session.identity.fingerprint,
        symbol="EURUSD",
        side=TradeSide.LONG,
        volume=0.1,
        reference_price=1.1000,
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
    )


class FakeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(self, *, send_result=True):
        self.send_calls = 0
        self.send_result = send_result

    def initialize(self, path):
        return True

    def shutdown(self):
        pass

    def symbol_info_tick(self, symbol):
        return SimpleNamespace(ask=1.1001, bid=1.0999)

    def order_calc_profit(self, order_type, symbol, volume, entry, stop):
        return -50.0

    def order_calc_margin(self, order_type, symbol, volume, entry):
        return 100.0

    def order_check(self, request):
        return SimpleNamespace(retcode=0)

    def order_send(self, request):
        self.send_calls += 1
        if not self.send_result:
            return None
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=10, deal=20, comment="done")


class SessionTests(unittest.TestCase):
    def test_demo_to_live_drift_latches_permanently(self):
        session = DemoSession(identity())
        failed = session.verify(identity(mode=AccountMode.REAL))
        self.assertFalse(failed.valid)
        self.assertIn(SessionFault.MODE_DRIFT, failed.faults)
        recovered_view = session.verify(identity())
        self.assertFalse(recovered_view.valid)
        self.assertFalse(session.broker_write_authorized)

    def test_permission_loss_latches(self):
        session = DemoSession(identity())
        session.verify(identity(allowed=False))
        self.assertIn(SessionFault.PERMISSION_LOSS, session.faults)


class IntentAndExecutionTests(unittest.TestCase):
    def test_preflight_is_read_only_and_uses_broker_loss(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        preflight_adapter = MT5PreflightAdapter(fake, session, lambda: identity())
        result = preflight_adapter.check(intent(session), at=NOW + timedelta(seconds=10))
        self.assertTrue(result.passed)
        self.assertEqual(result.loss_at_stop, 50.0)
        self.assertFalse(hasattr(preflight_adapter, "order_send"))

    def test_governance_failure_never_reaches_order_check(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        base = intent(session)
        denied = OrderIntent(
            base.strategy_hash, base.session_fingerprint, base.symbol, base.side, base.volume,
            base.reference_price, base.stop_price, base.target_price, base.approved_risk_fraction,
            base.allowed_loss, False, base.growth_multiplier, base.risk_approved, base.guardian_approved,
            base.created_at, base.expires_at, base.filling_mode,
        )
        result = MT5PreflightAdapter(fake, session, lambda: identity()).check(denied, at=NOW)
        self.assertFalse(result.passed)
        self.assertEqual(result.reasons, ("governance_not_approved",))

    def test_only_demo_adapter_sends_and_replay_is_blocked_by_unique_ledger(self):
        session = DemoSession(identity())
        fake = FakeMT5()
        preflight = MT5PreflightAdapter(fake, session, lambda: identity()).check(intent(session), at=NOW)
        ledger = SQLiteExecutionLedger()
        try:
            adapter = DemoMT5ExecutionAdapter(fake, session, lambda: identity(), ledger)
            result = adapter.send(preflight, at=NOW + timedelta(seconds=1))
            self.assertEqual(result.state, ExecutionState.FILLED)
            self.assertEqual(fake.send_calls, 1)
            with self.assertRaises(Exception):
                adapter.send(preflight, at=NOW + timedelta(seconds=2))
            self.assertEqual(fake.send_calls, 1)
            self.assertFalse(adapter.live_write_authorized)
        finally:
            ledger.close()

    def test_drift_between_preflight_and_send_causes_zero_send_calls(self):
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
            with self.assertRaises(Exception):
                adapter.send(preflight, at=NOW + timedelta(seconds=2))
            self.assertEqual(fake.send_calls, 1)
            reconciled = ledger.reconcile_unknown(
                preflight.intent.intent_hash,
                (BrokerExecutionSnapshot(preflight.intent.client_tag, order_ticket=10, deal_ticket=20, position_ticket=30),),
                at=NOW + timedelta(seconds=3),
            )
            self.assertEqual(reconciled.state, ExecutionState.FILLED)
        finally:
            ledger.close()


class ProtectionTests(unittest.TestCase):
    def test_missing_stop_is_not_protected(self):
        position = PositionSnapshot(123, "EURUSD", TradeSide.LONG, 0.1, 0.0)
        assessment = assess_position_protection(position)
        self.assertFalse(assessment.protected)

    def test_close_request_targets_actual_position_ticket(self):
        fake = FakeMT5()
        position = PositionSnapshot(123, "EURUSD", TradeSide.LONG, 0.1, 1.09)
        request = close_by_position_request(position, market_price=1.1, filling_mode=1, magic=42, module=fake)
        self.assertEqual(request["position"], 123)
        self.assertEqual(request["type"], fake.ORDER_TYPE_SELL)


if __name__ == "__main__":
    unittest.main()
