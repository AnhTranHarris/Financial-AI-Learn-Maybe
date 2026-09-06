from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import inspect
from pathlib import Path
import tempfile
import threading
import unittest

import dusty.portfolio_risk_governor as governor_module
from dusty.experience import TradeSide
from dusty.order_intent import BrokerPreflight, OrderIntent
from dusty.portfolio_risk_governor import (
    PortfolioCapitalSnapshot,
    PortfolioRiskGovernorPolicy,
    RiskReservationDecision,
    RiskReservationState,
    SQLitePortfolioRiskGovernor,
    TerminalRiskReleaseEvidence,
)
from dusty.risk import RiskState


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 23, 30, tzinfo=UTC)
SOURCE_COMMIT = "a" * 40
SESSION = sha256(b"session").hexdigest()
ACCOUNT = sha256(b"account").hexdigest()


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M195PortfolioRiskGovernorTests(unittest.TestCase):
    def intent(
        self,
        name: str,
        symbol: str,
        allowed_loss: float,
        *,
        session: str = SESSION,
        expires_at: datetime | None = None,
    ) -> OrderIntent:
        return OrderIntent(
            strategy_hash=fp(f"strategy-{name}"),
            session_fingerprint=session,
            symbol=symbol,
            side=TradeSide.LONG,
            volume=0.10,
            reference_price=1.1000,
            stop_price=1.0900,
            target_price=1.1200,
            approved_risk_fraction=max(0.000001, min(1.0, allowed_loss / 10_000.0)),
            allowed_loss=allowed_loss,
            pm_approved=True,
            growth_multiplier=1.0,
            risk_approved=True,
            guardian_approved=True,
            created_at=NOW,
            expires_at=expires_at or (NOW + timedelta(minutes=5)),
            filling_mode=0,
        )

    def preflight(
        self,
        intent: OrderIntent,
        *,
        loss_at_stop: float | None = None,
        required_margin: float = 50.0,
        passed: bool = True,
        reasons: tuple[str, ...] = (),
    ) -> BrokerPreflight:
        return BrokerPreflight(
            intent,
            passed,
            intent.allowed_loss * 0.8 if loss_at_stop is None else loss_at_stop,
            required_margin,
            1.1001,
            (("symbol", intent.symbol), ("volume", intent.volume)),
            reasons,
        )

    def snapshot(
        self,
        *,
        equity: float = 10_000.0,
        balance: float | None = None,
        session: str = SESSION,
        account: str = ACCOUNT,
        captured_at: datetime = NOW,
        margin_used: float = 0.0,
        free_margin: float = 10_000.0,
        complete: bool = True,
        unexplained_positions: int = 0,
        unexplained_orders: int = 0,
        source_commit: str = SOURCE_COMMIT,
    ) -> PortfolioCapitalSnapshot:
        reference = max(10_000.0, equity)
        return PortfolioCapitalSnapshot(
            account_fingerprint=account,
            session_fingerprint=session,
            source_commit=source_commit,
            captured_at=captured_at,
            equity=equity,
            balance=equity if balance is None else balance,
            high_water_mark=reference,
            day_start_equity=reference,
            week_start_equity=reference,
            margin_used=margin_used,
            free_margin=free_margin,
            broker_positions_fingerprint=fp("broker-positions"),
            broker_orders_fingerprint=fp("broker-orders"),
            complete_exposure_data=complete,
            unexplained_position_count=unexplained_positions,
            unexplained_order_count=unexplained_orders,
        )

    def reserve(
        self,
        governor: SQLitePortfolioRiskGovernor,
        intent: OrderIntent,
        *,
        snapshot: PortfolioCapitalSnapshot | None = None,
        preflight: BrokerPreflight | None = None,
        now: datetime = NOW,
    ):
        return governor.reserve(
            intent,
            preflight or self.preflight(intent),
            snapshot or self.snapshot(),
            now=now,
            evidence_fingerprints=(fp(f"evidence-{intent.intent_hash}"),),
        )

    def governor(self, path: str | Path = ":memory:") -> SQLitePortfolioRiskGovernor:
        return SQLitePortfolioRiskGovernor(path, source_commit=SOURCE_COMMIT)

    def test_multiple_strategies_share_one_hard_portfolio_heat_budget(self) -> None:
        governor = self.governor()
        try:
            first = self.reserve(governor, self.intent("a", "EURUSD", 90.0))
            second = self.reserve(governor, self.intent("b", "GBPUSD", 90.0))
            third = self.reserve(governor, self.intent("c", "USDJPY", 30.0))
            self.assertEqual(first.decision, RiskReservationDecision.APPROVED)
            self.assertEqual(second.decision, RiskReservationDecision.APPROVED)
            self.assertAlmostEqual(second.post_trade_portfolio_heat, 0.018)
            self.assertEqual(third.decision, RiskReservationDecision.DENIED)
            self.assertIn("portfolio_heat_ceiling", third.reasons)
            self.assertAlmostEqual(governor.active_reserved_loss(ACCOUNT), 180.0)
        finally:
            governor.close()

    def test_same_symbol_static_constitution_remains_authoritative(self) -> None:
        governor = self.governor()
        try:
            self.assertEqual(
                self.reserve(governor, self.intent("a", "EURUSD", 60.0)).decision,
                RiskReservationDecision.APPROVED,
            )
            result = self.reserve(governor, self.intent("b", "EURUSD", 50.0))
            self.assertEqual(result.decision, RiskReservationDecision.DENIED)
            self.assertIn("same_symbol_heat_ceiling", result.reasons)
        finally:
            governor.close()

    def test_charged_risk_is_full_planned_allowed_loss_not_lower_preflight_move(self) -> None:
        governor = self.governor()
        try:
            intent = self.intent("planned", "EURUSD", 90.0)
            result = self.reserve(governor, intent, preflight=self.preflight(intent, loss_at_stop=25.0))
            self.assertEqual(result.decision, RiskReservationDecision.APPROVED)
            self.assertEqual(result.reserved_loss, 90.0)
            self.assertEqual(governor.active_reserved_loss(ACCOUNT), 90.0)
        finally:
            governor.close()

    def test_unknown_or_incomplete_broker_exposure_fails_closed_not_zero(self) -> None:
        cases = (
            (self.snapshot(complete=False), "incomplete_broker_exposure_data"),
            (self.snapshot(unexplained_positions=1), "unexplained_broker_positions"),
            (self.snapshot(unexplained_orders=1), "unexplained_broker_orders"),
        )
        for snapshot, reason in cases:
            with self.subTest(reason=reason):
                governor = self.governor()
                try:
                    result = self.reserve(governor, self.intent(reason, "EURUSD", 50.0), snapshot=snapshot)
                    self.assertEqual(result.decision, RiskReservationDecision.DENIED)
                    self.assertIn(reason, result.reasons)
                    self.assertEqual(governor.active_reserved_loss(ACCOUNT), 0.0)
                finally:
                    governor.close()

    def test_stale_future_wrong_source_and_wrong_session_snapshots_fail_closed(self) -> None:
        cases = (
            (self.snapshot(captured_at=NOW - timedelta(minutes=2)), NOW, "capital_snapshot_stale"),
            (self.snapshot(captured_at=NOW + timedelta(seconds=1)), NOW, "capital_snapshot_from_future"),
            (self.snapshot(source_commit="b" * 40), NOW, "capital_snapshot_source_commit_mismatch"),
        )
        for snapshot, now, reason in cases:
            with self.subTest(reason=reason):
                governor = self.governor()
                try:
                    result = self.reserve(governor, self.intent(reason, "EURUSD", 50.0), snapshot=snapshot, now=now)
                    self.assertEqual(result.decision, RiskReservationDecision.DENIED)
                    self.assertIn(reason, result.reasons)
                finally:
                    governor.close()

        governor = self.governor()
        try:
            intent = self.intent("session", "EURUSD", 50.0, session=fp("other-session"))
            result = self.reserve(governor, intent, snapshot=self.snapshot())
            self.assertEqual(result.decision, RiskReservationDecision.DENIED)
            self.assertIn("intent_session_mismatch", result.reasons)
        finally:
            governor.close()

    def test_zero_equity_is_deterministic_denial_not_numeric_exception(self) -> None:
        governor = self.governor()
        try:
            result = self.reserve(governor, self.intent("zero", "EURUSD", 1.0), snapshot=self.snapshot(equity=0.0))
            self.assertEqual(result.decision, RiskReservationDecision.DENIED)
            self.assertEqual(result.risk_state, RiskState.FAILED)
            self.assertIn("account_state:failed", result.reasons)
        finally:
            governor.close()

    def test_broker_margin_can_tighten_but_never_expand_internal_heat_budget(self) -> None:
        governor = self.governor()
        try:
            self.reserve(governor, self.intent("a", "EURUSD", 90.0))
            self.reserve(governor, self.intent("b", "GBPUSD", 90.0))
            result = self.reserve(
                governor,
                self.intent("c", "USDJPY", 30.0),
                snapshot=self.snapshot(free_margin=1_000_000.0),
            )
            self.assertEqual(result.decision, RiskReservationDecision.DENIED)
            self.assertIn("portfolio_heat_ceiling", result.reasons)
        finally:
            governor.close()

        governor = self.governor()
        try:
            intent = self.intent("margin", "EURUSD", 50.0)
            result = self.reserve(
                governor,
                intent,
                snapshot=self.snapshot(free_margin=20.0),
                preflight=self.preflight(intent, required_margin=50.0),
            )
            self.assertEqual(result.decision, RiskReservationDecision.DENIED)
            self.assertIn("broker_free_margin_insufficient", result.reasons)
        finally:
            governor.close()

    def test_existing_active_intent_is_idempotent_and_never_double_charged(self) -> None:
        governor = self.governor()
        try:
            intent = self.intent("same", "EURUSD", 80.0)
            first = self.reserve(governor, intent)
            second = self.reserve(governor, intent)
            self.assertEqual(first.decision, RiskReservationDecision.APPROVED)
            self.assertEqual(second.decision, RiskReservationDecision.EXISTING)
            self.assertEqual(first.record_fingerprint, second.record_fingerprint)
            self.assertEqual(governor.active_reserved_loss(ACCOUNT), 80.0)
        finally:
            governor.close()

    def test_released_intent_cannot_be_resurrected(self) -> None:
        governor = self.governor()
        try:
            intent = self.intent("released", "EURUSD", 60.0)
            self.reserve(governor, intent)
            governor.release_pre_send(
                intent.intent_hash,
                now=NOW + timedelta(seconds=1),
                no_send_evidence_fingerprints=(fp("no-send"),),
            )
            self.assertEqual(governor.state(intent.intent_hash), RiskReservationState.RELEASED)
            self.assertEqual(governor.active_reserved_loss(ACCOUNT), 0.0)
            with self.assertRaisesRegex(ValueError, "cannot be resurrected"):
                self.reserve(governor, intent)
        finally:
            governor.close()

    def test_commit_survives_restart_and_only_terminal_broker_evidence_releases_heat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "risk.sqlite3"
            intent = self.intent("restart", "EURUSD", 75.0)
            governor = self.governor(path)
            self.reserve(governor, intent)
            governor.commit(
                intent.intent_hash,
                now=NOW + timedelta(seconds=1),
                evidence_fingerprints=(fp("send-admission"),),
            )
            governor.close()

            reopened = self.governor(path)
            try:
                self.assertEqual(reopened.state(intent.intent_hash), RiskReservationState.COMMITTED)
                self.assertEqual(reopened.active_reserved_loss(ACCOUNT), 75.0)
                incomplete = TerminalRiskReleaseEvidence(
                    intent.intent_hash,
                    ACCOUNT,
                    NOW + timedelta(seconds=2),
                    fp("m188"),
                    fp("positions-after"),
                    fp("orders-after"),
                    False,
                    True,
                    (fp("broker-history"),),
                )
                with self.assertRaisesRegex(ValueError, "no remaining position"):
                    reopened.release_terminal(incomplete)
                self.assertEqual(reopened.active_reserved_loss(ACCOUNT), 75.0)

                complete = replace(incomplete, position_absent_or_closed=True)
                receipt = reopened.release_terminal(complete)
                self.assertEqual(receipt.state, RiskReservationState.RELEASED)
                self.assertEqual(reopened.active_reserved_loss(ACCOUNT), 0.0)
                self.assertTrue(reopened.integrity_check()[0])
            finally:
                reopened.close()

    def test_pre_send_release_cannot_race_or_release_committed_exposure(self) -> None:
        governor = self.governor()
        try:
            intent = self.intent("committed", "EURUSD", 60.0)
            self.reserve(governor, intent)
            governor.commit(
                intent.intent_hash,
                now=NOW + timedelta(seconds=1),
                evidence_fingerprints=(fp("send"),),
            )
            with self.assertRaisesRegex(ValueError, "requires reserved state"):
                governor.release_pre_send(
                    intent.intent_hash,
                    now=NOW + timedelta(seconds=2),
                    no_send_evidence_fingerprints=(fp("forged-no-send"),),
                )
            self.assertEqual(governor.state(intent.intent_hash), RiskReservationState.COMMITTED)
            self.assertEqual(governor.active_reserved_loss(ACCOUNT), 60.0)
        finally:
            governor.close()

    def test_expiry_never_automatically_refunds_reserved_risk(self) -> None:
        governor = self.governor()
        try:
            intent = self.intent("expiry", "EURUSD", 60.0, expires_at=NOW + timedelta(seconds=1))
            self.reserve(governor, intent)
            self.assertEqual(governor.active_reserved_loss(ACCOUNT), 60.0)
            # No clock-based cleanup exists: explicit no-send or terminal evidence is required.
            self.assertEqual(governor.state(intent.intent_hash), RiskReservationState.RESERVED)
        finally:
            governor.close()

    def test_current_equity_revalues_heat_and_a_drawdown_tightens_remaining_capacity(self) -> None:
        governor = self.governor()
        try:
            self.reserve(governor, self.intent("base", "EURUSD", 90.0))
            lower_equity = self.snapshot(equity=5_000.0, free_margin=5_000.0)
            result = self.reserve(governor, self.intent("next", "GBPUSD", 20.0), snapshot=lower_equity)
            self.assertEqual(result.decision, RiskReservationDecision.DENIED)
            self.assertIn("portfolio_heat_ceiling", result.reasons)
            self.assertAlmostEqual(result.post_trade_portfolio_heat, 0.022)
        finally:
            governor.close()

    def test_higher_equity_can_create_room_but_never_rewrites_old_reservation(self) -> None:
        governor = self.governor()
        try:
            first = self.intent("base", "EURUSD", 90.0)
            self.reserve(governor, first)
            larger = self.snapshot(equity=20_000.0, free_margin=20_000.0)
            second = self.reserve(governor, self.intent("next", "GBPUSD", 100.0), snapshot=larger)
            self.assertEqual(second.decision, RiskReservationDecision.APPROVED)
            self.assertEqual(governor.active_reserved_loss(ACCOUNT), 190.0)
        finally:
            governor.close()

    def test_two_independent_connections_cannot_race_past_shared_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "risk.sqlite3"
            seed_governor = self.governor(path)
            try:
                self.reserve(seed_governor, self.intent("seed", "AUDUSD", 100.0))
            finally:
                seed_governor.close()

            barrier = threading.Barrier(2)
            lock = threading.Lock()
            decisions: list[RiskReservationDecision] = []
            failures: list[BaseException] = []

            def worker(name: str, symbol: str) -> None:
                local = self.governor(path)
                try:
                    intent = self.intent(name, symbol, 60.0)
                    barrier.wait(timeout=5)
                    result = self.reserve(local, intent)
                    with lock:
                        decisions.append(result.decision)
                except BaseException as exc:  # pragma: no cover - assertion reports exact worker failure
                    with lock:
                        failures.append(exc)
                finally:
                    local.close()

            threads = (
                threading.Thread(target=worker, args=("race-a", "NZDUSD")),
                threading.Thread(target=worker, args=("race-b", "USDJPY")),
            )
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)
            self.assertFalse(failures, failures)
            self.assertEqual(sorted(decision.value for decision in decisions), ["approved", "denied"])

            audit = self.governor(path)
            try:
                self.assertEqual(audit.active_reserved_loss(ACCOUNT), 160.0)
                self.assertTrue(audit.integrity_check()[0])
            finally:
                audit.close()

    def test_ledger_fingerprint_corruption_blocks_new_risk(self) -> None:
        governor = self.governor()
        try:
            self.reserve(governor, self.intent("first", "EURUSD", 60.0))
            governor._db.execute(
                "UPDATE portfolio_risk_records SET reserved_loss=61 WHERE intent_hash=(SELECT intent_hash FROM portfolio_risk_records LIMIT 1)"
            )
            ok, errors = governor.integrity_check()
            self.assertFalse(ok)
            self.assertTrue(any("record_fingerprint" in error for error in errors))
            with self.assertRaisesRegex(RuntimeError, "ledger integrity failure"):
                self.reserve(governor, self.intent("second", "GBPUSD", 60.0))
        finally:
            governor.close()

    def test_preflight_and_governance_are_revalidated_at_master_risk_boundary(self) -> None:
        governor = self.governor()
        try:
            intent = self.intent("preflight", "EURUSD", 50.0)
            failed = self.reserve(
                governor,
                intent,
                preflight=self.preflight(intent, passed=False, reasons=("order_check_failed",)),
            )
            self.assertEqual(failed.decision, RiskReservationDecision.DENIED)
            self.assertIn("broker_preflight_not_passed", failed.reasons)

            other = self.intent("other", "EURUSD", 50.0)
            mismatch = self.reserve(governor, intent, preflight=self.preflight(other))
            self.assertEqual(mismatch.decision, RiskReservationDecision.DENIED)
            self.assertIn("broker_preflight_intent_mismatch", mismatch.reasons)
        finally:
            governor.close()

    def test_m195_has_no_broker_live_risk_guardian_or_promotion_authority(self) -> None:
        governor = self.governor()
        try:
            result = self.reserve(governor, self.intent("authority", "EURUSD", 50.0))
            self.assertFalse(result.broker_write_authority)
            self.assertFalse(result.live_write_authority)
            self.assertFalse(result.risk_override_authority)
            self.assertFalse(result.guardian_override_authority)
            self.assertFalse(result.promotion_authority)
            self.assertFalse(governor.broker_write_authorized)
            self.assertFalse(governor.live_write_authorized)
            self.assertFalse(governor.risk_override_authorized)
            self.assertFalse(governor.guardian_override_authorized)
        finally:
            governor.close()

    def test_m195_does_not_import_mt5_or_m196_correlation_model_or_send_orders(self) -> None:
        source = inspect.getsource(governor_module)
        self.assertNotIn("MetaTrader5", source)
        self.assertNotIn("order_send(", source)
        self.assertNotIn("portfolio_model", source)
        self.assertNotIn("allocate_portfolio", source)


if __name__ == "__main__":
    unittest.main()
