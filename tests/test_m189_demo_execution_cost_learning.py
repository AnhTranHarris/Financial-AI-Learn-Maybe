from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.broker_calibration import BrokerCalibrationPolicy, CalibrationStatus
from dusty.core import AnalystState, GuardianState, PatienceState, SkepticState
from dusty.demo_execution_cost_learning import (
    DemoCostLearningStatus,
    DemoExecutionCostSample,
    learn_demo_execution_costs,
    sample_from_reconciliation,
)
from dusty.execution_reconciliation import ExecutionReconciliation, ReconciliationStatus
from dusty.experience import TradeSide
from dusty.shadow_execution import ShadowExecutionIntent, ShadowMarketQuote
from dusty.strategy_v3 import OrderStyle


UTC = timezone.utc
START = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
BROKER = sha256(b"coinexx-demo-profile").hexdigest()


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def make_shadow(side: TradeSide = TradeSide.LONG) -> ShadowExecutionIntent:
    quote = ShadowMarketQuote("EURUSD", START, 1.1000, 1.1002, fp("quote-source"))
    return ShadowExecutionIntent(
        fp("champion"), fp("deployment"), "eurusd:m15", fp("intent"), "DD-ABC",
        fp("strategy"), fp("session"), fp("cognition"),
        AnalystState.LONG if side is TradeSide.LONG else AnalystState.SHORT,
        SkepticState.CLEAR, PatienceState.READY, GuardianState.NORMAL,
        "EURUSD", side, OrderStyle.MARKET, 0.10, 1.1001,
        1.0950 if side is TradeSide.LONG else 1.1050,
        1.1100 if side is TradeSide.LONG else 1.0900,
        None, 0.0025, 50.0, 0.001, START + timedelta(milliseconds=50),
        START + timedelta(minutes=2), None, quote, fp("capture-policy"),
    )


def reconciliation(row: ShadowExecutionIntent, index: int, *, status=ReconciliationStatus.FILLED, fill_fraction=1.0, fill_price=None, observed_at=None) -> ExecutionReconciliation:
    observed = observed_at or START + timedelta(days=index // 10, seconds=index + 2)
    expected = row.capture_quote.ask if row.side is TradeSide.LONG else row.capture_quote.bid
    price = fill_price if fill_price is not None else expected + (0.00001 * (index % 4) if row.side is TradeSide.LONG else -0.00001 * (index % 4))
    volume = row.volume * fill_fraction
    return ExecutionReconciliation(
        status, row.intent_hash, row.fingerprint, fp(f"receipt-{index}"), fp(f"broker-{index}"),
        observed, expected, volume, fill_fraction, price, abs(price - expected),
        abs(price - expected) / expected, 20.0 + index, 30.0 + index,
        -0.35, 0.0, -0.01, (700 + index,), (800 + index,), (900 + index,),
        ("observed_fill",), (fp(f"evidence-{index}"),),
    )


def sample_pair(index: int, side: TradeSide) -> tuple[DemoExecutionCostSample, ExecutionReconciliation]:
    row = make_shadow(side)
    rec = reconciliation(row, index)
    sample = DemoExecutionCostSample(
        BROKER, rec.fingerprint, row.fingerprint, "EURUSD", side, 0.00001,
        1.1000, 1.1002, rec.expected_price, rec.weighted_average_fill_price,
        rec.filled_volume, rec.fill_fraction, rec.first_fill_latency_ms,
        rec.last_fill_latency_ms, rec.commission, rec.swap, rec.fee,
    )
    return sample, rec


class M189DemoExecutionCostLearningTests(unittest.TestCase):
    def test_only_partial_or_filled_m188_evidence_can_become_sample(self) -> None:
        row = make_shadow()
        filled = reconciliation(row, 1)
        sample = sample_from_reconciliation(row, filled, broker_profile_fingerprint=BROKER, point_size=0.00001)
        self.assertEqual(sample.reconciliation_fingerprint, filled.fingerprint)
        self.assertAlmostEqual(sample.fill_fraction, 1.0)
        pending = reconciliation(row, 2, status=ReconciliationStatus.PENDING, fill_fraction=0.0, fill_price=1.1002)
        with self.assertRaisesRegex(ValueError, "PARTIAL/FILLED"):
            sample_from_reconciliation(row, pending, broker_profile_fingerprint=BROKER, point_size=0.00001)

    def test_no_samples_is_uncalibrated_and_never_invents_metrics(self) -> None:
        learned = learn_demo_execution_costs((), broker_profile_fingerprint=BROKER, symbol="EURUSD")
        self.assertEqual(learned.status, DemoCostLearningStatus.UNCALIBRATED)
        self.assertEqual(learned.calibration.status, CalibrationStatus.UNCALIBRATED)
        self.assertIsNone(learned.fill_fraction_p50)
        self.assertIsNone(learned.first_fill_latency_p95_ms)

    def test_thin_demo_sample_remains_insufficient(self) -> None:
        pairs = tuple(sample_pair(i, TradeSide.LONG if i % 2 == 0 else TradeSide.SHORT) for i in range(6))
        learned = learn_demo_execution_costs(pairs, broker_profile_fingerprint=BROKER, symbol="EURUSD")
        self.assertEqual(learned.status, DemoCostLearningStatus.INSUFFICIENT)
        self.assertEqual(learned.calibration.status, CalibrationStatus.INSUFFICIENT)
        self.assertIsNone(learned.fill_fraction_p05)

    def test_multi_day_both_side_demo_evidence_reuses_m165_and_adds_latency_fill_metrics(self) -> None:
        pairs = tuple(sample_pair(i, TradeSide.LONG if i % 2 == 0 else TradeSide.SHORT) for i in range(30))
        learned = learn_demo_execution_costs(pairs, broker_profile_fingerprint=BROKER, symbol="EURUSD")
        self.assertEqual(learned.status, DemoCostLearningStatus.CALIBRATED)
        self.assertEqual(learned.calibration.status, CalibrationStatus.CALIBRATED)
        self.assertEqual(learned.calibration.observation_count, 30)
        self.assertIsNotNone(learned.calibration.spread_p95_points)
        self.assertIsNotNone(learned.calibration.adverse_slippage_p99_points)
        self.assertGreater(learned.first_fill_latency_p95_ms, learned.first_fill_latency_p50_ms)
        self.assertAlmostEqual(learned.fill_fraction_p50, 1.0)

    def test_partial_fills_reduce_empirical_fill_fraction_without_becoming_strategy_failure(self) -> None:
        pairs = []
        for i in range(30):
            sample, rec = sample_pair(i, TradeSide.LONG if i % 2 == 0 else TradeSide.SHORT)
            if i % 5 == 0:
                sample = DemoExecutionCostSample(
                    sample.broker_profile_fingerprint, sample.reconciliation_fingerprint,
                    sample.shadow_fingerprint, sample.symbol, sample.side, sample.point_size,
                    sample.captured_bid, sample.captured_ask, sample.requested_price,
                    sample.fill_price, sample.filled_volume * 0.5, 0.5,
                    sample.first_fill_latency_ms, sample.last_fill_latency_ms,
                    sample.commission, sample.swap, sample.fee,
                )
                rec = ExecutionReconciliation(
                    ReconciliationStatus.PARTIAL, rec.intent_hash, rec.shadow_fingerprint,
                    rec.m187_receipt_fingerprint, rec.broker_evidence_fingerprint, rec.observed_at,
                    rec.expected_price, rec.filled_volume * 0.5, 0.5,
                    rec.weighted_average_fill_price, rec.adverse_slippage_price,
                    rec.adverse_slippage_fraction, rec.first_fill_latency_ms, rec.last_fill_latency_ms,
                    rec.commission, rec.swap, rec.fee, rec.order_tickets, rec.deal_tickets,
                    rec.position_tickets, rec.reasons, rec.evidence_fingerprints,
                )
                sample = DemoExecutionCostSample(
                    sample.broker_profile_fingerprint, rec.fingerprint, sample.shadow_fingerprint,
                    sample.symbol, sample.side, sample.point_size, sample.captured_bid,
                    sample.captured_ask, sample.requested_price, sample.fill_price,
                    sample.filled_volume, sample.fill_fraction, sample.first_fill_latency_ms,
                    sample.last_fill_latency_ms, sample.commission, sample.swap, sample.fee,
                )
            pairs.append((sample, rec))
        learned = learn_demo_execution_costs(tuple(pairs), broker_profile_fingerprint=BROKER, symbol="EURUSD")
        self.assertEqual(learned.status, DemoCostLearningStatus.CALIBRATED)
        self.assertLess(learned.fill_fraction_p05, 1.0)

    def test_duplicate_or_identity_drift_fails_closed(self) -> None:
        pair = sample_pair(1, TradeSide.LONG)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            learn_demo_execution_costs((pair, pair), broker_profile_fingerprint=BROKER, symbol="EURUSD")
        wrong = DemoExecutionCostSample(
            fp("other-broker"), *pair[0].__getstate__()[1:] if False else (
                pair[0].reconciliation_fingerprint, pair[0].shadow_fingerprint, pair[0].symbol,
                pair[0].side, pair[0].point_size, pair[0].captured_bid, pair[0].captured_ask,
                pair[0].requested_price, pair[0].fill_price, pair[0].filled_volume,
                pair[0].fill_fraction, pair[0].first_fill_latency_ms, pair[0].last_fill_latency_ms,
                pair[0].commission, pair[0].swap, pair[0].fee,
            )
        )
        with self.assertRaisesRegex(ValueError, "mix broker"):
            learn_demo_execution_costs(((wrong, pair[1]),), broker_profile_fingerprint=BROKER, symbol="EURUSD")

    def test_learning_has_no_execution_or_governance_authority(self) -> None:
        pairs = tuple(sample_pair(i, TradeSide.LONG if i % 2 == 0 else TradeSide.SHORT) for i in range(30))
        learned = learn_demo_execution_costs(pairs, broker_profile_fingerprint=BROKER, symbol="EURUSD")
        self.assertFalse(learned.broker_write_authority)
        self.assertFalse(learned.retry_authority)
        self.assertFalse(learned.risk_override_authority)
        self.assertFalse(learned.guardian_override_authority)
        self.assertFalse(learned.promotion_authority)


if __name__ == "__main__":
    unittest.main()
