from __future__ import annotations

"""M171 historical-to-forward performance decay measurement."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


class DecayStatus(StrEnum):
    MISSING_FORWARD = "missing_forward"
    INSUFFICIENT_FORWARD = "insufficient_forward"
    MEASURED = "measured"


@dataclass(frozen=True, slots=True)
class PerformanceEvidence:
    evidence_fingerprint: str
    strategy_fingerprint: str
    period_start: datetime
    period_end: datetime
    metric_name: str
    metric_value: float
    trade_count: int
    is_forward: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_fingerprint", _sha(self.evidence_fingerprint, "performance evidence"))
        object.__setattr__(self, "strategy_fingerprint", _sha(self.strategy_fingerprint, "performance strategy"))
        object.__setattr__(self, "period_start", _aware(self.period_start, "performance period_start"))
        object.__setattr__(self, "period_end", _aware(self.period_end, "performance period_end"))
        if self.period_end <= self.period_start:
            raise ValueError("performance period_end must follow period_start")
        metric = str(self.metric_name).strip().lower()
        if not metric or "\n" in metric or "\r" in metric:
            raise ValueError("metric_name must be one line")
        object.__setattr__(self, "metric_name", metric)
        object.__setattr__(self, "metric_value", _finite(self.metric_value, "performance metric_value"))
        if isinstance(self.trade_count, bool) or int(self.trade_count) != self.trade_count or int(self.trade_count) < 0:
            raise ValueError("trade_count must be nonnegative")
        object.__setattr__(self, "trade_count", int(self.trade_count))


@dataclass(frozen=True, slots=True)
class HistoricalForwardDecay:
    status: DecayStatus
    strategy_fingerprint: str
    historical_evidence_fingerprint: str
    forward_evidence_fingerprint: str | None
    historical_value: float
    forward_value: float | None
    retention_ratio: float | None
    decay_fraction: float | None
    forward_trade_count: int
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_fingerprint", _sha(self.strategy_fingerprint, "decay strategy"))
        object.__setattr__(self, "historical_evidence_fingerprint", _sha(self.historical_evidence_fingerprint, "historical evidence"))
        if self.forward_evidence_fingerprint is not None:
            object.__setattr__(self, "forward_evidence_fingerprint", _sha(self.forward_evidence_fingerprint, "forward evidence"))
        if not self.reason.strip():
            raise ValueError("decay reason required")
        if self.status is DecayStatus.MEASURED:
            if self.forward_evidence_fingerprint is None or self.forward_value is None or self.retention_ratio is None or self.decay_fraction is None:
                raise ValueError("measured decay requires real forward values")
        elif self.retention_ratio is not None or self.decay_fraction is not None:
            raise ValueError("unmeasured decay cannot expose inferred retention")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m171-historical-forward-decay-v1",
                "status": self.status.value,
                "strategy": self.strategy_fingerprint,
                "historical_evidence": self.historical_evidence_fingerprint,
                "forward_evidence": self.forward_evidence_fingerprint,
                "historical_value": self.historical_value,
                "forward_value": self.forward_value,
                "retention_ratio": self.retention_ratio,
                "decay_fraction": self.decay_fraction,
                "forward_trade_count": self.forward_trade_count,
                "reason": self.reason,
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


def measure_historical_forward_decay(
    historical: PerformanceEvidence,
    forward: PerformanceEvidence | None,
    *,
    minimum_forward_trades: int = 30,
) -> HistoricalForwardDecay:
    if historical.is_forward:
        raise ValueError("historical evidence cannot be marked forward")
    if historical.metric_value <= 0:
        raise ValueError("historical decay baseline metric must be positive")
    if not 1 <= int(minimum_forward_trades) <= 1_000_000:
        raise ValueError("minimum_forward_trades out of range")
    if forward is None:
        return HistoricalForwardDecay(
            DecayStatus.MISSING_FORWARD,
            historical.strategy_fingerprint,
            historical.evidence_fingerprint,
            None,
            historical.metric_value,
            None,
            None,
            None,
            0,
            "chronologically later forward evidence does not exist yet",
        )
    if not forward.is_forward:
        raise ValueError("forward evidence must be explicitly marked forward")
    if forward.strategy_fingerprint != historical.strategy_fingerprint or forward.metric_name != historical.metric_name:
        raise ValueError("historical/forward evidence identity mismatch")
    if forward.period_start <= historical.period_end:
        raise ValueError("forward period must begin strictly after historical evidence ends")
    if forward.trade_count < minimum_forward_trades:
        return HistoricalForwardDecay(
            DecayStatus.INSUFFICIENT_FORWARD,
            historical.strategy_fingerprint,
            historical.evidence_fingerprint,
            forward.evidence_fingerprint,
            historical.metric_value,
            forward.metric_value,
            None,
            None,
            forward.trade_count,
            "forward evidence exists but sample depth is insufficient",
        )
    retention = forward.metric_value / historical.metric_value
    decay = 1.0 - retention
    return HistoricalForwardDecay(
        DecayStatus.MEASURED,
        historical.strategy_fingerprint,
        historical.evidence_fingerprint,
        forward.evidence_fingerprint,
        historical.metric_value,
        forward.metric_value,
        retention,
        decay,
        forward.trade_count,
        "decay measured from actual chronologically later forward evidence",
    )
