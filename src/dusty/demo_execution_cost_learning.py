from __future__ import annotations

"""M189 empirical Demo execution-cost learning.

M189 converts completed M188 broker-history reconciliations into the already
certified M165 broker-economics calibration contract and adds empirical fill and
latency distributions. It learns only from observed Demo execution evidence; it
cannot send/retry orders, alter risk, mutate positions, promote Champions, or
change Guardian state.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math

from .broker_calibration import (
    BrokerCalibrationPolicy,
    BrokerEconomicsCalibration,
    BrokerExecutionObservation,
    CalibrationStatus,
    TradeSide as CalibrationSide,
    calibrate_broker_economics,
)
from .execution_reconciliation import ExecutionReconciliation, ReconciliationStatus
from .experience import TradeSide
from .shadow_execution import ShadowExecutionIntent


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


def _quantile(values: tuple[float, ...], q: float) -> float:
    if not values:
        raise ValueError("quantile requires observations")
    rows = sorted(values)
    if len(rows) == 1:
        return rows[0]
    position = (len(rows) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return rows[lower]
    weight = position - lower
    return rows[lower] * (1.0 - weight) + rows[upper] * weight


class DemoCostLearningStatus(StrEnum):
    UNCALIBRATED = "uncalibrated"
    INSUFFICIENT = "insufficient"
    CALIBRATED = "calibrated"


@dataclass(frozen=True, slots=True)
class DemoExecutionCostSample:
    broker_profile_fingerprint: str
    reconciliation_fingerprint: str
    shadow_fingerprint: str
    symbol: str
    side: TradeSide
    point_size: float
    captured_bid: float
    captured_ask: float
    requested_price: float
    fill_price: float
    filled_volume: float
    fill_fraction: float
    first_fill_latency_ms: float
    last_fill_latency_ms: float
    commission: float
    swap: float
    fee: float

    def __post_init__(self) -> None:
        for field, label in (
            ("broker_profile_fingerprint", "broker profile"),
            ("reconciliation_fingerprint", "M188 reconciliation"),
            ("shadow_fingerprint", "M186 shadow"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        if not self.symbol:
            raise ValueError("sample symbol required")
        for field in (
            "point_size", "captured_bid", "captured_ask", "requested_price",
            "fill_price", "filled_volume", "fill_fraction", "first_fill_latency_ms",
            "last_fill_latency_ms", "commission", "swap", "fee",
        ):
            object.__setattr__(self, field, _finite(getattr(self, field), field))
        if self.point_size <= 0 or self.captured_bid <= 0 or self.captured_ask <= 0:
            raise ValueError("sample quote economics must be positive")
        if self.captured_ask < self.captured_bid:
            raise ValueError("sample ask cannot be below bid")
        if self.requested_price <= 0 or self.fill_price <= 0 or self.filled_volume <= 0:
            raise ValueError("sample execution economics must be positive")
        if not 0 < self.fill_fraction <= 1:
            raise ValueError("sample fill_fraction must be in (0,1]")
        if self.first_fill_latency_ms < 0 or self.last_fill_latency_ms < self.first_fill_latency_ms:
            raise ValueError("sample latency ordering invalid")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m189-demo-execution-cost-sample-v1",
            self.broker_profile_fingerprint,
            self.reconciliation_fingerprint,
            self.shadow_fingerprint,
            self.symbol,
            self.side.value,
            self.point_size,
            self.captured_bid,
            self.captured_ask,
            self.requested_price,
            self.fill_price,
            self.filled_volume,
            self.fill_fraction,
            self.first_fill_latency_ms,
            self.last_fill_latency_ms,
            self.commission,
            self.swap,
            self.fee,
        ))


def sample_from_reconciliation(
    shadow: ShadowExecutionIntent,
    reconciliation: ExecutionReconciliation,
    *,
    broker_profile_fingerprint: str,
    point_size: float,
) -> DemoExecutionCostSample:
    """Create one empirical M189 sample from actual M188 fill evidence."""

    if reconciliation.status not in {ReconciliationStatus.PARTIAL, ReconciliationStatus.FILLED}:
        raise ValueError("M189 learns costs only from PARTIAL/FILLED M188 evidence")
    if reconciliation.intent_hash != shadow.intent_hash or reconciliation.shadow_fingerprint != shadow.fingerprint:
        raise ValueError("M188/M186 identity drift")
    if reconciliation.weighted_average_fill_price is None:
        raise ValueError("filled M188 evidence requires weighted_average_fill_price")
    if reconciliation.first_fill_latency_ms is None or reconciliation.last_fill_latency_ms is None:
        raise ValueError("filled M188 evidence requires latency")
    return DemoExecutionCostSample(
        broker_profile_fingerprint,
        reconciliation.fingerprint,
        shadow.fingerprint,
        shadow.symbol,
        shadow.side,
        point_size,
        shadow.capture_quote.bid,
        shadow.capture_quote.ask,
        reconciliation.expected_price,
        reconciliation.weighted_average_fill_price,
        reconciliation.filled_volume,
        reconciliation.fill_fraction,
        reconciliation.first_fill_latency_ms,
        reconciliation.last_fill_latency_ms,
        reconciliation.commission,
        reconciliation.swap,
        reconciliation.fee,
    )


def _to_observation(sample: DemoExecutionCostSample, reconciliation: ExecutionReconciliation) -> BrokerExecutionObservation:
    side = CalibrationSide.BUY if sample.side is TradeSide.LONG else CalibrationSide.SELL
    return BrokerExecutionObservation(
        sample.broker_profile_fingerprint,
        sample.symbol,
        side,
        reconciliation.observed_at,
        sample.point_size,
        sample.captured_bid,
        sample.captured_ask,
        sample.requested_price,
        sample.fill_price,
        sample.filled_volume,
        sample.commission,
        sample.fee,
        sample.swap,
        sample.reconciliation_fingerprint,
    )


@dataclass(frozen=True, slots=True)
class DemoExecutionCostLearning:
    status: DemoCostLearningStatus
    calibration: BrokerEconomicsCalibration
    sample_fingerprints: tuple[str, ...]
    fill_fraction_p50: float | None
    fill_fraction_p05: float | None
    first_fill_latency_p50_ms: float | None
    first_fill_latency_p95_ms: float | None
    last_fill_latency_p95_ms: float | None
    reason: str

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m189-demo-execution-cost-learning-v1",
            self.status.value,
            self.calibration.fingerprint,
            self.sample_fingerprints,
            self.fill_fraction_p50,
            self.fill_fraction_p05,
            self.first_fill_latency_p50_ms,
            self.first_fill_latency_p95_ms,
            self.last_fill_latency_p95_ms,
            self.reason,
        ))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def retry_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


def learn_demo_execution_costs(
    pairs: tuple[tuple[DemoExecutionCostSample, ExecutionReconciliation], ...],
    *,
    broker_profile_fingerprint: str,
    symbol: str,
    policy: BrokerCalibrationPolicy = BrokerCalibrationPolicy(),
) -> DemoExecutionCostLearning:
    broker = _sha(broker_profile_fingerprint, "learning broker profile")
    symbol_norm = str(symbol).strip().upper()
    if not symbol_norm:
        raise ValueError("learning symbol required")
    if not pairs:
        calibration = calibrate_broker_economics(
            (), broker_profile_fingerprint=broker, symbol=symbol_norm, policy=policy
        )
        return DemoExecutionCostLearning(
            DemoCostLearningStatus.UNCALIBRATED,
            calibration,
            (), None, None, None, None, None,
            "no reconciled Demo fills",
        )
    samples = tuple(row[0] for row in pairs)
    fingerprints = tuple(row.fingerprint for row in samples)
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("M189 cannot learn from duplicate execution samples")
    for sample, reconciliation in pairs:
        if sample.broker_profile_fingerprint != broker or sample.symbol != symbol_norm:
            raise ValueError("M189 cannot mix broker profiles or symbols")
        if sample.reconciliation_fingerprint != reconciliation.fingerprint:
            raise ValueError("M189 sample/reconciliation identity drift")
    if len({row.point_size for row in samples}) != 1:
        raise ValueError("M189 cannot mix point-size economics")
    observations = tuple(_to_observation(sample, reconciliation) for sample, reconciliation in pairs)
    calibration = calibrate_broker_economics(
        observations,
        broker_profile_fingerprint=broker,
        symbol=symbol_norm,
        policy=policy,
    )
    if calibration.status is CalibrationStatus.CALIBRATED:
        fractions = tuple(row.fill_fraction for row in samples)
        first = tuple(row.first_fill_latency_ms for row in samples)
        last = tuple(row.last_fill_latency_ms for row in samples)
        return DemoExecutionCostLearning(
            DemoCostLearningStatus.CALIBRATED,
            calibration,
            tuple(sorted(fingerprints)),
            _quantile(fractions, 0.50),
            _quantile(fractions, 0.05),
            _quantile(first, 0.50),
            _quantile(first, 0.95),
            _quantile(last, 0.95),
            "observed Demo execution costs calibrated",
        )
    return DemoExecutionCostLearning(
        DemoCostLearningStatus.INSUFFICIENT,
        calibration,
        tuple(sorted(fingerprints)),
        None, None, None, None, None,
        "insufficient reconciled Demo fills for calibrated distributions",
    )
