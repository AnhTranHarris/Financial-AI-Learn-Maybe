from __future__ import annotations

"""M165 broker-economics calibration from observed quotes and executions.

No universal spread/slippage/commission assumption is invented here.  A profile is
CALIBRATED only from sufficiently broad observations for one exact broker profile,
symbol and point size; otherwise it remains explicitly uncalibrated/insufficient.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import median
from typing import Iterable


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("broker observation timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("quantile requires observations")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


class CalibrationStatus(StrEnum):
    UNCALIBRATED = "uncalibrated"
    INSUFFICIENT = "insufficient"
    CALIBRATED = "calibrated"


@dataclass(frozen=True, slots=True)
class BrokerExecutionObservation:
    broker_profile_fingerprint: str
    symbol: str
    side: TradeSide
    observed_at: datetime
    point_size: float
    bid: float
    ask: float
    requested_price: float
    fill_price: float
    volume_lots: float
    commission: float
    fee: float = 0.0
    swap: float = 0.0
    evidence_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "broker_profile_fingerprint", _sha(self.broker_profile_fingerprint, "broker profile"))
        symbol = str(self.symbol).strip().upper()
        if not symbol:
            raise ValueError("broker observation symbol required")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "observed_at", _aware(self.observed_at))
        for name in ("point_size", "bid", "ask", "requested_price", "fill_price", "volume_lots", "commission", "fee", "swap"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if self.point_size <= 0 or self.volume_lots <= 0:
            raise ValueError("point_size and volume_lots must be positive")
        if self.ask < self.bid:
            raise ValueError("ask cannot be below bid")
        if self.requested_price <= 0 or self.fill_price <= 0:
            raise ValueError("requested/fill prices must be positive")
        if self.evidence_fingerprint is not None:
            object.__setattr__(self, "evidence_fingerprint", _sha(self.evidence_fingerprint, "broker evidence"))

    @property
    def spread_points(self) -> float:
        return (self.ask - self.bid) / self.point_size

    @property
    def adverse_slippage_points(self) -> float:
        raw = (
            self.fill_price - self.requested_price
            if self.side is TradeSide.BUY
            else self.requested_price - self.fill_price
        ) / self.point_size
        return max(0.0, raw)

    @property
    def commission_fee_per_lot(self) -> float:
        return max(0.0, -(self.commission + self.fee) / self.volume_lots)

    @property
    def absolute_swap_per_lot(self) -> float:
        return abs(self.swap) / self.volume_lots

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "broker": self.broker_profile_fingerprint,
                "symbol": self.symbol,
                "side": self.side.value,
                "observed_at": self.observed_at.isoformat(),
                "point_size": self.point_size,
                "bid": self.bid,
                "ask": self.ask,
                "requested_price": self.requested_price,
                "fill_price": self.fill_price,
                "volume_lots": self.volume_lots,
                "commission": self.commission,
                "fee": self.fee,
                "swap": self.swap,
                "evidence": self.evidence_fingerprint,
            }
        )


@dataclass(frozen=True, slots=True)
class BrokerCalibrationPolicy:
    minimum_observations: int = 30
    minimum_distinct_days: int = 3
    require_both_sides: bool = True

    def __post_init__(self) -> None:
        if not 1 <= int(self.minimum_observations) <= 1_000_000:
            raise ValueError("minimum_observations out of range")
        if not 1 <= int(self.minimum_distinct_days) <= 3650:
            raise ValueError("minimum_distinct_days out of range")


@dataclass(frozen=True, slots=True)
class BrokerEconomicsCalibration:
    status: CalibrationStatus
    broker_profile_fingerprint: str
    symbol: str
    observation_count: int
    distinct_days: int
    observation_fingerprints: tuple[str, ...]
    spread_p50_points: float | None
    spread_p95_points: float | None
    spread_p99_points: float | None
    adverse_slippage_p50_points: float | None
    adverse_slippage_p95_points: float | None
    adverse_slippage_p99_points: float | None
    commission_fee_p50_per_lot: float | None
    commission_fee_p95_per_lot: float | None
    absolute_swap_p95_per_lot: float | None
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "broker_profile_fingerprint", _sha(self.broker_profile_fingerprint, "calibration broker"))
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())
        object.__setattr__(self, "observation_fingerprints", tuple(sorted(_sha(row, "calibration observation") for row in self.observation_fingerprints)))
        if not self.reason.strip():
            raise ValueError("calibration reason required")
        metrics = (
            self.spread_p50_points,
            self.spread_p95_points,
            self.spread_p99_points,
            self.adverse_slippage_p50_points,
            self.adverse_slippage_p95_points,
            self.adverse_slippage_p99_points,
            self.commission_fee_p50_per_lot,
            self.commission_fee_p95_per_lot,
            self.absolute_swap_p95_per_lot,
        )
        if self.status is CalibrationStatus.CALIBRATED and any(value is None for value in metrics):
            raise ValueError("calibrated broker profile requires all metrics")
        if self.status is not CalibrationStatus.CALIBRATED and any(value is not None for value in metrics):
            raise ValueError("uncalibrated broker profile cannot expose stress metrics")

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m165-broker-calibration-v1",
            "status": self.status.value,
            "broker": self.broker_profile_fingerprint,
            "symbol": self.symbol,
            "observation_count": self.observation_count,
            "distinct_days": self.distinct_days,
            "observation_fingerprints": list(self.observation_fingerprints),
            "spread_p50_points": self.spread_p50_points,
            "spread_p95_points": self.spread_p95_points,
            "spread_p99_points": self.spread_p99_points,
            "adverse_slippage_p50_points": self.adverse_slippage_p50_points,
            "adverse_slippage_p95_points": self.adverse_slippage_p95_points,
            "adverse_slippage_p99_points": self.adverse_slippage_p99_points,
            "commission_fee_p50_per_lot": self.commission_fee_p50_per_lot,
            "commission_fee_p95_per_lot": self.commission_fee_p95_per_lot,
            "absolute_swap_p95_per_lot": self.absolute_swap_p95_per_lot,
            "reason": self.reason,
        }

    @property
    def broker_write_authority(self) -> bool:
        return False


def calibrate_broker_economics(
    observations: Iterable[BrokerExecutionObservation],
    *,
    broker_profile_fingerprint: str,
    symbol: str,
    policy: BrokerCalibrationPolicy = BrokerCalibrationPolicy(),
) -> BrokerEconomicsCalibration:
    broker = _sha(broker_profile_fingerprint, "calibration broker")
    symbol_norm = str(symbol).strip().upper()
    if not symbol_norm:
        raise ValueError("calibration symbol required")
    rows = tuple(observations)
    if not rows:
        return BrokerEconomicsCalibration(
            CalibrationStatus.UNCALIBRATED,
            broker,
            symbol_norm,
            0,
            0,
            (),
            None, None, None, None, None, None, None, None, None,
            "no observed broker executions",
        )
    if any(row.broker_profile_fingerprint != broker or row.symbol != symbol_norm for row in rows):
        raise ValueError("broker calibration cannot mix broker profiles or symbols")
    fingerprints = tuple(row.fingerprint for row in rows)
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("broker calibration cannot contain duplicate observations")
    distinct_days = len({row.observed_at.date() for row in rows})
    sides = {row.side for row in rows}
    sufficient = (
        len(rows) >= policy.minimum_observations
        and distinct_days >= policy.minimum_distinct_days
        and (not policy.require_both_sides or sides == {TradeSide.BUY, TradeSide.SELL})
    )
    if not sufficient:
        return BrokerEconomicsCalibration(
            CalibrationStatus.INSUFFICIENT,
            broker,
            symbol_norm,
            len(rows),
            distinct_days,
            fingerprints,
            None, None, None, None, None, None, None, None, None,
            "insufficient broker observations for calibrated stress metrics",
        )

    spreads = [row.spread_points for row in rows]
    slippage = [row.adverse_slippage_points for row in rows]
    commissions = [row.commission_fee_per_lot for row in rows]
    swaps = [row.absolute_swap_per_lot for row in rows]
    return BrokerEconomicsCalibration(
        CalibrationStatus.CALIBRATED,
        broker,
        symbol_norm,
        len(rows),
        distinct_days,
        fingerprints,
        _quantile(spreads, 0.50),
        _quantile(spreads, 0.95),
        _quantile(spreads, 0.99),
        _quantile(slippage, 0.50),
        _quantile(slippage, 0.95),
        _quantile(slippage, 0.99),
        median(commissions),
        _quantile(commissions, 0.95),
        _quantile(swaps, 0.95),
        "observed broker economics calibrated",
    )
