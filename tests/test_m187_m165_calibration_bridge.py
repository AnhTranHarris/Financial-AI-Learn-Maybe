from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from dusty.artifact_vault import ResearchArtifactVault
from dusty.demo_execution import DemoExecutionResult
from dusty.demo_session import AccountMode, DemoSession, SessionIdentity
from dusty.execution_lifecycle import ExecutionState
from dusty.experience import TradeSide
from dusty.m187_calibration_bridge import (
    M187_CALIBRATION_ADMISSION_CONTENT_TYPE,
    M187CalibrationExecutionBridge,
    M187CalibrationPermit,
)
from dusty.order_intent import BrokerPreflight, OrderIntent
from dusty.position_actions import (
    PositionActionIntent,
    PositionActionKind,
    PositionActionPreflight,
)
from dusty.strategy_v3 import OrderStyle


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 3, 0, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class SpyAdapter:
    def __init__(self, before_send=None) -> None:
        self.calls = 0
        self.before_send = before_send

    @property
    def live_write_authorized(self) -> bool:
        return False

    def send(self, preflight, *, at: datetime) -> DemoExecutionResult:
        if self.before_send is not None:
            self.before_send(preflight, at)
        self.calls += 1
        return DemoExecutionResult(
            preflight.intent.intent_hash,
            ExecutionState.FILLED if isinstance(preflight, BrokerPreflight) else preflight.success_state,
            10009,
            700 + self.calls,
            800 + self.calls,
            "done",
        )


class M187M165CalibrationBridgeTests(unittest.TestCase):
    def identity(self, *, mode: AccountMode = AccountMode.DEMO) -> SessionIdentity:
        return SessionIdentity(
            "C:\\Program Files\\Coinexx MT5 Terminal\\terminal64.exe",
            "6182",
            "Coinexx-Demo",
            1001,
            mode,
            "USD",
            500.0,
            True,
            True,
            2,
            fp("symbol-spec"),
            NOW - timedelta(minutes=5),
        )

    def entry(self, session: DemoSession, *, volume: float = 0.01) -> OrderIntent:
        return OrderIntent(
            fp("qualification-strategy"),
            session.identity.fingerprint,
            "EURUSD",
            TradeSide.LONG,
            volume,
            1.1000,
            1.0950,
            None,
            0.001,
            5.0,
            True,
            1.0,
            True,
            True,
            NOW,
            NOW + timedelta(minutes=2),
            0,
            order_style=OrderStyle.MARKET,
        )

    def entry_preflight(self, intent: OrderIntent, *, passed: bool = True) -> BrokerPreflight:
        request = (
            ("action", 1),
            ("comment", intent.client_tag),
            ("price", 1.1001),
            ("sl", intent.stop_price),
            ("symbol", intent.symbol),
            ("type", 0),
            ("type_filling", intent.filling_mode),
            ("type_time", 0),
            ("volume", intent.volume),
        )
        return BrokerPreflight(
            intent,
            passed,
            4.9,
            2.0,
            1.1001,
            request if passed else (),
            () if passed else ("failed",),
        )

    def close(self, session: DemoSession, *, volume: float = 0.01) -> PositionActionIntent:
        return PositionActionIntent(
            fp("qualification-strategy"),
            session.identity.fingerprint,
            PositionActionKind.FULL_CLOSE,
            "EURUSD",
            TradeSide.LONG,
            9001,
            0,
            volume,
            volume,
            1.0950,
            0.0,
            0.0,
            True,
            True,
            True,
            NOW,
            NOW + timedelta(minutes=2),
            0,
        )

    def close_preflight(self, intent: PositionActionIntent) -> PositionActionPreflight:
        return PositionActionPreflight(
            intent,
            True,
            (
                ("action", 1),
                ("position", intent.position_ticket),
                ("symbol", intent.symbol),
                ("volume", intent.action_volume),
            ),
            (),
            ExecutionState.CLOSED,
        )

    def permit(self, session: DemoSession, intent, *, action: str, sequence: int = 1) -> M187CalibrationPermit:
        return M187CalibrationPermit(
            fp("qualification-plan"),
            fp("qualification-manifest"),
            fp("qualification-strategy"),
            fp("symbol-spec"),
            session.identity.fingerprint,
            "EURUSD",
            intent.intent_hash,
            action,
            0.01,
            5.0,
            sequence,
            NOW,
            NOW + timedelta(minutes=5),
        )

    def fixture(self, *, mode: AccountMode = AccountMode.DEMO, spy=None):
        temp = tempfile.TemporaryDirectory()
        vault = ResearchArtifactVault(Path(temp.name) / "vault")
        session = DemoSession(self.identity(mode=mode))
        adapter = spy or SpyAdapter()
        bridge = M187CalibrationExecutionBridge(
            vault=vault,
            session=session,
            adapter=adapter,
            producer_fingerprint=fp("m187-calibration-producer"),
        )
        return temp, vault, session, adapter, bridge

    def test_permit_is_short_lived_one_shot_and_non_escalating(self) -> None:
        session = DemoSession(self.identity())
        intent = self.entry(session)
        permit = self.permit(session, intent, action="open")
        self.assertTrue(permit.demo_write_authority)
        self.assertFalse(permit.live_write_authority)
        self.assertFalse(permit.promotion_authority)
        self.assertFalse(permit.strategy_mutation_authority)
        self.assertFalse(permit.risk_override_authority)
        self.assertFalse(permit.guardian_override_authority)
        self.assertFalse(permit.retry_authority)
        self.assertTrue(permit.active_at(NOW + timedelta(minutes=1)))
        with self.assertRaises(ValueError):
            replace(permit, valid_until=NOW + timedelta(minutes=16))
        with self.assertRaises(ValueError):
            replace(permit, purpose="live_trading")

    def test_bridge_rejects_live_session_and_has_no_ambient_authority(self) -> None:
        temp = tempfile.TemporaryDirectory()
        vault = ResearchArtifactVault(Path(temp.name) / "vault")
        try:
            with self.assertRaises(ValueError):
                M187CalibrationExecutionBridge(
                    vault=vault,
                    session=DemoSession(self.identity(mode=AccountMode.REAL)),
                    adapter=SpyAdapter(),
                    producer_fingerprint=fp("producer"),
                )
        finally:
            vault.close()
            temp.cleanup()

        temp, vault, _session, _adapter, bridge = self.fixture()
        try:
            self.assertFalse(bridge.demo_write_authorized)
            self.assertFalse(bridge.live_write_authorized)
            self.assertEqual(bridge.order_send_owner, "DemoMT5ExecutionAdapter")
            self.assertFalse(hasattr(bridge, "order_send"))
        finally:
            vault.close()
            temp.cleanup()

    def test_exact_entry_admission_persists_before_single_delegate(self) -> None:
        holder = {}

        def before_send(preflight, _at):
            rows = tuple(
                row
                for row in holder["vault"].list_subject(preflight.intent.intent_hash)
                if row.content_type == M187_CALIBRATION_ADMISSION_CONTENT_TYPE
            )
            if len(rows) != 1:
                raise AssertionError("calibration admission must be durable before send")

        spy = SpyAdapter(before_send=before_send)
        temp, vault, session, _adapter, bridge = self.fixture(spy=spy)
        holder["vault"] = vault
        try:
            intent = self.entry(session)
            permit = self.permit(session, intent, action="open")
            receipt = bridge.execute(
                preflight=self.entry_preflight(intent),
                permit=permit,
                current_symbol_spec_fingerprint=fp("symbol-spec"),
                at=NOW + timedelta(seconds=1),
            )
            self.assertEqual(spy.calls, 1)
            self.assertEqual(receipt.admission.intent_hash, intent.intent_hash)
            self.assertFalse(receipt.live_write_authority)
            self.assertFalse(receipt.promotion_authority)
            self.assertFalse(receipt.retry_authority)
        finally:
            vault.close()
            temp.cleanup()

    def test_drift_or_failed_preflight_never_delegates(self) -> None:
        temp, vault, session, spy, bridge = self.fixture()
        try:
            intent = self.entry(session)
            permit = self.permit(session, intent, action="open")
            with self.assertRaises(PermissionError):
                bridge.execute(
                    preflight=self.entry_preflight(replace(intent, volume=0.02)),
                    permit=permit,
                    current_symbol_spec_fingerprint=fp("symbol-spec"),
                    at=NOW + timedelta(seconds=1),
                )
            with self.assertRaises(PermissionError):
                bridge.execute(
                    preflight=self.entry_preflight(intent),
                    permit=permit,
                    current_symbol_spec_fingerprint=fp("different-spec"),
                    at=NOW + timedelta(seconds=1),
                )
            with self.assertRaises(PermissionError):
                bridge.execute(
                    preflight=self.entry_preflight(intent, passed=False),
                    permit=permit,
                    current_symbol_spec_fingerprint=fp("symbol-spec"),
                    at=NOW + timedelta(seconds=1),
                )
            self.assertEqual(spy.calls, 0)
        finally:
            vault.close()
            temp.cleanup()

    def test_full_close_requires_exact_full_position_volume(self) -> None:
        temp, vault, session, spy, bridge = self.fixture()
        try:
            intent = self.close(session)
            permit = self.permit(session, intent, action="full_close", sequence=2)
            bridge.execute(
                preflight=self.close_preflight(intent),
                permit=permit,
                current_symbol_spec_fingerprint=fp("symbol-spec"),
                at=NOW + timedelta(seconds=1),
            )
            self.assertEqual(spy.calls, 1)

            partial = PositionActionIntent(
                fp("qualification-strategy"),
                session.identity.fingerprint,
                PositionActionKind.PARTIAL_CLOSE,
                "EURUSD",
                TradeSide.LONG,
                9002,
                0,
                0.01,
                0.005,
                1.0950,
                0.0,
                0.0,
                True,
                True,
                True,
                NOW,
                NOW + timedelta(minutes=2),
                0,
            )
            wrong_permit = self.permit(session, partial, action="full_close", sequence=3)
            with self.assertRaises(PermissionError):
                bridge.execute(
                    preflight=self.close_preflight(partial),
                    permit=wrong_permit,
                    current_symbol_spec_fingerprint=fp("symbol-spec"),
                    at=NOW + timedelta(seconds=1),
                )
            self.assertEqual(spy.calls, 1)
        finally:
            vault.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
