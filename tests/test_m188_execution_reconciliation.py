from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from dusty.artifact_vault import ResearchArtifactVault
from dusty.core import AnalystState, GuardianState, PatienceState, SkepticState
from dusty.demo_execution import DemoExecutionResult
from dusty.demo_execution_bridge import DemoBridgeAdmission, DemoBridgeExecutionReceipt
from dusty.execution_lifecycle import ExecutionState
from dusty.execution_reconciliation import (
    BrokerDealEvidence,
    BrokerExecutionEvidence,
    BrokerOrderEvidence,
    BrokerPositionEvidence,
    RECONCILIATION_CONTENT_TYPE,
    ReconciliationStatus,
    persist_reconciliation,
    reconcile_execution,
)
from dusty.experience import TradeSide
from dusty.shadow_execution import ShadowExecutionIntent, ShadowMarketQuote
from dusty.strategy_v3 import OrderStyle


UTC = timezone.utc
NOW = datetime(2026, 9, 6, 1, 0, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def shadow(*, side: TradeSide = TradeSide.LONG, volume: float = 0.10) -> ShadowExecutionIntent:
    quote = ShadowMarketQuote("EURUSD", NOW, 1.0999, 1.1001, fp("quote-source"))
    return ShadowExecutionIntent(
        fp("champion"), fp("deployment"), "eurusd:m15:breakout", fp("intent"),
        "DD-ABC123", fp("strategy"), fp("session"), fp("cognition"),
        AnalystState.LONG if side is TradeSide.LONG else AnalystState.SHORT,
        SkepticState.CLEAR, PatienceState.READY, GuardianState.NORMAL,
        "EURUSD", side, OrderStyle.MARKET, volume, 1.1000, 1.0950 if side is TradeSide.LONG else 1.1050,
        1.1100 if side is TradeSide.LONG else 1.0900, None, 0.0025, 50.0, 0.001,
        NOW + timedelta(milliseconds=100), NOW + timedelta(minutes=3), None,
        quote, fp("capture-policy"),
    )


def receipt(row: ShadowExecutionIntent, *, state=ExecutionState.ACCEPTED, retcode=10009, order=700, deal=0):
    admission = DemoBridgeAdmission(
        fp("permit"), row.champion_fingerprint, row.fingerprint, fp("shadow-artifact"),
        row.intent_hash, row.session_fingerprint, NOW + timedelta(seconds=1),
    )
    return DemoBridgeExecutionReceipt(
        admission, fp("admission-artifact"),
        DemoExecutionResult(row.intent_hash, state, retcode, order, deal, "synthetic"),
    )


def deal(row: ShadowExecutionIntent, ticket: int, *, volume=0.05, price=1.1002, seconds=2, order=700):
    return BrokerDealEvidence(
        ticket, order, 900, "EURUSD", row.side, NOW + timedelta(seconds=seconds),
        volume, price, -0.35, 0.0, 0.0, fp(f"deal-source-{ticket}"),
    )


def evidence(row: ShadowExecutionIntent, *, deals=(), orders=(), positions=(), complete=True):
    return BrokerExecutionEvidence(
        row.intent_hash, row.session_fingerprint, row.symbol, NOW + timedelta(seconds=5),
        complete, fp("broker-snapshot"), tuple(deals), tuple(orders), tuple(positions),
    )


class M188ExecutionReconciliationTests(unittest.TestCase):
    def test_multiple_deals_are_sorted_aggregated_and_history_beats_intermediate_partial_state(self) -> None:
        row = shadow()
        first = deal(row, 2, volume=0.04, price=1.1002, seconds=2)
        second = deal(row, 1, volume=0.06, price=1.1003, seconds=3)
        result = reconcile_execution(
            row,
            receipt(row, state=ExecutionState.PARTIAL, retcode=10010, order=700, deal=2),
            evidence(row, deals=(second, first), positions=(BrokerPositionEvidence(900, "EURUSD", row.side, 0.10, 1.10026, fp("pos")),)),
        )
        self.assertEqual(result.status, ReconciliationStatus.FILLED)
        self.assertAlmostEqual(result.filled_volume, 0.10)
        self.assertAlmostEqual(result.fill_fraction, 1.0)
        self.assertAlmostEqual(result.weighted_average_fill_price, 1.10026)
        self.assertEqual(result.deal_tickets, (2, 1))
        self.assertGreater(result.adverse_slippage_price, 0)
        self.assertAlmostEqual(result.commission, -0.70)

    def test_partial_fill_with_active_remainder_is_partial(self) -> None:
        row = shadow()
        order = BrokerOrderEvidence(700, "EURUSD", 0.10, 0.06, 1, 2, 900, fp("order"))
        result = reconcile_execution(row, receipt(row, state=ExecutionState.PARTIAL, retcode=10010), evidence(row, deals=(deal(row, 1, volume=0.04),), orders=(order,)))
        self.assertEqual(result.status, ReconciliationStatus.PARTIAL)
        self.assertAlmostEqual(result.fill_fraction, 0.4)
        self.assertIn("active_remainder", result.reasons[0])

    def test_active_order_without_deal_is_pending(self) -> None:
        row = shadow()
        order = BrokerOrderEvidence(700, "EURUSD", 0.10, 0.10, 1, 2, 0, fp("order"))
        result = reconcile_execution(row, receipt(row, state=ExecutionState.ACCEPTED, retcode=10008), evidence(row, orders=(order,)))
        self.assertEqual(result.status, ReconciliationStatus.PENDING)

    def test_explicit_rejection_without_fills_is_rejected(self) -> None:
        row = shadow()
        result = reconcile_execution(row, receipt(row, state=ExecutionState.REJECTED, retcode=10006, order=0), evidence(row))
        self.assertEqual(result.status, ReconciliationStatus.REJECTED)
        self.assertIn("10006", result.reasons[0])

    def test_timeout_or_accepted_without_history_never_manufactures_rejection(self) -> None:
        row = shadow()
        timeout = reconcile_execution(row, receipt(row, state=ExecutionState.REJECTED, retcode=10012, order=0), evidence(row))
        accepted = reconcile_execution(row, receipt(row, state=ExecutionState.ACCEPTED, retcode=10009), evidence(row))
        self.assertEqual(timeout.status, ReconciliationStatus.INCOMPLETE)
        self.assertEqual(accepted.status, ReconciliationStatus.INCOMPLETE)

    def test_incomplete_history_remains_incomplete(self) -> None:
        row = shadow()
        result = reconcile_execution(row, receipt(row), evidence(row, complete=False))
        self.assertEqual(result.status, ReconciliationStatus.INCOMPLETE)
        self.assertIn("declared_incomplete", result.reasons[0])

    def test_overfill_ticket_and_side_corruption_fail_as_inconsistent(self) -> None:
        row = shadow()
        overfill = reconcile_execution(row, receipt(row), evidence(row, deals=(deal(row, 1, volume=0.11),)))
        self.assertEqual(overfill.status, ReconciliationStatus.INCONSISTENT)
        self.assertIn("broker_history_overfill", overfill.reasons)

        wrong_ticket = reconcile_execution(row, receipt(row, order=700), evidence(row, deals=(deal(row, 2, order=701),)))
        self.assertEqual(wrong_ticket.status, ReconciliationStatus.INCONSISTENT)
        self.assertIn("broker_deal_order_ticket_mismatch", wrong_ticket.reasons)

        wrong_side_deal = BrokerDealEvidence(3, 700, 900, "EURUSD", TradeSide.SHORT, NOW + timedelta(seconds=2), 0.05, 1.1002, -0.35, 0, 0, fp("wrong-side"))
        wrong_side = reconcile_execution(row, receipt(row), evidence(row, deals=(wrong_side_deal,)))
        self.assertEqual(wrong_side.status, ReconciliationStatus.INCONSISTENT)
        self.assertIn("broker_deal_side_mismatch", wrong_side.reasons)

    def test_broker_evidence_rejects_duplicate_future_or_mixed_symbol_rows(self) -> None:
        row = shadow()
        duplicate = deal(row, 1)
        with self.assertRaisesRegex(ValueError, "duplicate broker deal"):
            evidence(row, deals=(duplicate, duplicate))
        future = BrokerDealEvidence(2, 700, 900, "EURUSD", row.side, NOW + timedelta(seconds=10), 0.05, 1.1002, 0, 0, 0, fp("future"))
        with self.assertRaisesRegex(ValueError, "future deal"):
            evidence(row, deals=(future,))
        other = BrokerOrderEvidence(700, "GBPUSD", 0.1, 0.1, 1, 2, 0, fp("other"))
        with self.assertRaisesRegex(ValueError, "mix symbols"):
            evidence(row, orders=(other,))

    def test_session_or_intent_identity_drift_fails_closed(self) -> None:
        row = shadow()
        good = evidence(row)
        with self.assertRaisesRegex(ValueError, "session identity drift"):
            reconcile_execution(row, receipt(row), BrokerExecutionEvidence(good.intent_hash, fp("other-session"), good.symbol, good.observed_at, True, good.source_fingerprint))
        with self.assertRaisesRegex(ValueError, "does not belong"):
            reconcile_execution(row, receipt(row), BrokerExecutionEvidence(fp("other-intent"), row.session_fingerprint, good.symbol, good.observed_at, True, good.source_fingerprint))

    def test_persistence_is_content_addressed_and_grants_no_authority(self) -> None:
        row = shadow()
        result = reconcile_execution(row, receipt(row), evidence(row, deals=(deal(row, 1, volume=0.10),)))
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.retry_authority)
        self.assertFalse(result.position_mutation_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)
        with tempfile.TemporaryDirectory() as folder:
            vault = ResearchArtifactVault(Path(folder) / "vault")
            try:
                record = persist_reconciliation(vault, result, producer_fingerprint=fp("m188"))
                self.assertEqual(record.content_type, RECONCILIATION_CONTENT_TYPE)
                self.assertEqual(record.subject_fingerprint, row.intent_hash)
                self.assertEqual(vault.read_bytes(record.record_fingerprint), __import__("json").dumps(result.payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8"))
            finally:
                vault.close()


if __name__ == "__main__":
    unittest.main()
