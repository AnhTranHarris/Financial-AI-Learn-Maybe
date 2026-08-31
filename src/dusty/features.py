from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping, Sequence

from .mt5worker import MT5Bar
from .research import Scalar


@dataclass(frozen=True, slots=True)
class FeatureBar:
    """A completed OHLC bar stamped at the instant its full contents are knowable.

    ``source_open_at`` preserves the original MT5 bar-open timestamp when available. ``at`` is the
    observation/availability timestamp used by all point-in-time features and decisions.
    """

    at: datetime
    open: float
    high: float
    low: float
    close: float
    spread_points: float = 0.0
    tick_volume: float = 0.0
    source_open_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("feature bar availability timestamp must be timezone-aware")
        if self.source_open_at is not None:
            if self.source_open_at.tzinfo is None or self.source_open_at.utcoffset() is None:
                raise ValueError("source bar-open timestamp must be timezone-aware")
            if self.source_open_at >= self.at:
                raise ValueError("completed MT5 bar cannot be available at or before its open time")
        if any(not math.isfinite(v) or v <= 0 for v in (self.open, self.high, self.low, self.close)):
            raise ValueError("feature OHLC prices must be finite and positive")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("feature OHLC geometry is invalid")
        if not math.isfinite(self.spread_points) or self.spread_points < 0:
            raise ValueError("spread points must be finite and nonnegative")
        if not math.isfinite(self.tick_volume) or self.tick_volume < 0:
            raise ValueError("tick volume must be finite and nonnegative")

    @classmethod
    def from_mt5(cls, bar: MT5Bar, *, available_at: datetime) -> "FeatureBar":
        """Convert an MT5 bar-open record only when a later timestamp proves the bar completed."""
        if available_at.tzinfo is None or available_at.utcoffset() is None:
            raise ValueError("MT5 bar availability timestamp must be timezone-aware")
        if available_at <= bar.at:
            raise ValueError("MT5 completed bar must become available after its open timestamp")
        return cls(
            available_at,
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            float(bar.spread),
            float(bar.tick_volume),
            source_open_at=bar.at,
        )


def completed_feature_bars_from_mt5(bars: Iterable[MT5Bar]) -> tuple[FeatureBar, ...]:
    """Convert MT5 bar-open records into completed observations without lookahead.

    MT5's Python bar ``time`` is the bar opening time. A historical OHLC row is treated as knowable only
    when the next bar has actually opened. The final raw bar is therefore dropped because this bounded
    history slice contains no later observation proving it completed. This is intentionally conservative
    across weekend/session gaps and avoids assuming a bar's final high/low/close were known at its open.
    """
    rows = tuple(bars)
    if tuple(sorted(rows, key=lambda row: row.at)) != rows:
        raise ValueError("MT5 bars must be chronological")
    if len({row.at for row in rows}) != len(rows):
        raise ValueError("MT5 bar-open timestamps must be unique")
    if len(rows) < 2:
        return ()
    return tuple(
        FeatureBar.from_mt5(current, available_at=following.at)
        for current, following in zip(rows, rows[1:])
    )


@dataclass(frozen=True, slots=True)
class FeatureConfig:
    ma_period: int = 20
    atr_period: int = 14
    rsi_period: int = 14

    def __post_init__(self) -> None:
        if min(self.ma_period, self.atr_period, self.rsi_period) < 2:
            raise ValueError("indicator periods must be at least 2")


@dataclass(frozen=True, slots=True)
class FeatureVector:
    at: datetime
    values: tuple[tuple[str, Scalar], ...]

    @classmethod
    def of(cls, at: datetime, values: Mapping[str, Scalar]) -> "FeatureVector":
        return cls(at, tuple(sorted(values.items())))

    def feature_map(self) -> dict[str, Scalar]:
        return dict(self.values)


def _period(period: int) -> int:
    if period < 1:
        raise ValueError("period must be positive")
    return period


def sma(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    period = _period(period)
    result: list[float | None] = [None] * len(values)
    window_sum = 0.0
    for index, raw in enumerate(values):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("indicator input must be finite")
        window_sum += value
        if index >= period:
            window_sum -= float(values[index - period])
        if index + 1 >= period:
            result[index] = window_sum / period
    return tuple(result)


def ema(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    period = _period(period)
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    seed_values = [float(value) for value in values[:period]]
    if any(not math.isfinite(value) for value in seed_values):
        raise ValueError("indicator input must be finite")
    previous = sum(seed_values) / period
    result[period - 1] = previous
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        value = float(values[index])
        if not math.isfinite(value):
            raise ValueError("indicator input must be finite")
        previous = alpha * value + (1.0 - alpha) * previous
        result[index] = previous
    return tuple(result)


def smma(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    """Wilder-style smoothed moving average with an arithmetic-mean seed."""
    period = _period(period)
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    seed_values = [float(value) for value in values[:period]]
    if any(not math.isfinite(value) for value in seed_values):
        raise ValueError("indicator input must be finite")
    previous = sum(seed_values) / period
    result[period - 1] = previous
    for index in range(period, len(values)):
        value = float(values[index])
        if not math.isfinite(value):
            raise ValueError("indicator input must be finite")
        previous = (previous * (period - 1) + value) / period
        result[index] = previous
    return tuple(result)


def true_range(bars: Sequence[FeatureBar]) -> tuple[float, ...]:
    result: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        value = bar.high - bar.low
        if previous_close is not None:
            value = max(value, abs(bar.high - previous_close), abs(bar.low - previous_close))
        result.append(value)
        previous_close = bar.close
    return tuple(result)


def atr(bars: Sequence[FeatureBar], period: int) -> tuple[float | None, ...]:
    """MetaTrader built-in iATR-compatible target: simple moving average of True Range.

    Wilder/SMMA ATR remains a distinct concept and must not be silently substituted for MT5's built-in
    iATR semantics. Native parity is still verified by DustyIndicatorParity.mq5 on the user's terminal.
    """
    return sma(true_range(bars), period)


def wilder_atr(bars: Sequence[FeatureBar], period: int) -> tuple[float | None, ...]:
    """Explicit Wilder/SMMA ATR for research that intentionally requests that variant."""
    return smma(true_range(bars), period)


def rsi(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    period = _period(period)
    result: list[float | None] = [None] * len(values)
    if len(values) <= period:
        return tuple(result)
    gains: list[float] = []
    losses: list[float] = []
    for index in range(1, len(values)):
        delta = float(values[index]) - float(values[index - 1])
        if not math.isfinite(delta):
            raise ValueError("indicator input must be finite")
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    def score(gain: float, loss: float) -> float:
        if loss == 0.0:
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return 100.0 - 100.0 / (1.0 + rs)

    result[period] = score(average_gain, average_loss)
    for delta_index in range(period, len(gains)):
        average_gain = (average_gain * (period - 1) + gains[delta_index]) / period
        average_loss = (average_loss * (period - 1) + losses[delta_index]) / period
        result[delta_index + 1] = score(average_gain, average_loss)
    return tuple(result)


def compute_standard_features(
    bars: Iterable[FeatureBar],
    config: FeatureConfig = FeatureConfig(),
) -> tuple[FeatureVector, ...]:
    """Point-in-time feature engine. Row i uses only completed bars available at or before row i."""
    rows = tuple(bars)
    if tuple(sorted(rows, key=lambda row: row.at)) != rows:
        raise ValueError("feature bars must be chronological by availability time")
    if len({row.at for row in rows}) != len(rows):
        raise ValueError("feature bar availability timestamps must be unique")
    closes = tuple(row.close for row in rows)
    sma_values = sma(closes, config.ma_period)
    ema_values = ema(closes, config.ma_period)
    atr_values = atr(rows, config.atr_period)
    rsi_values = rsi(closes, config.rsi_period)
    vectors = []
    for index, bar in enumerate(rows):
        values: dict[str, Scalar] = {
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "spread_points": bar.spread_points,
            "tick_volume": bar.tick_volume,
        }
        if index:
            values["return_1"] = bar.close / rows[index - 1].close - 1.0
        if sma_values[index] is not None:
            values[f"sma_{config.ma_period}"] = float(sma_values[index])
            values["sma"] = float(sma_values[index])
        if ema_values[index] is not None:
            values[f"ema_{config.ma_period}"] = float(ema_values[index])
            values["ema"] = float(ema_values[index])
        if atr_values[index] is not None:
            values[f"atr_{config.atr_period}"] = float(atr_values[index])
            values["atr"] = float(atr_values[index])
        if rsi_values[index] is not None:
            values[f"rsi_{config.rsi_period}"] = float(rsi_values[index])
            values["rsi"] = float(rsi_values[index])
        vectors.append(FeatureVector.of(bar.at, values))
    return tuple(vectors)


@dataclass(frozen=True, slots=True)
class MT5IndicatorRow:
    at: datetime
    sma: float
    ema: float
    atr: float
    rsi: float
    source_open_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class IndicatorParityResult:
    matched_rows: int
    max_abs_error: tuple[tuple[str, float], ...]
    passed: bool
    reasons: tuple[str, ...]


def parse_mt5_indicator_csv(text: str) -> tuple[MT5IndicatorRow, ...]:
    rows = []
    for row in csv.DictReader(io.StringIO(text)):
        raw_available = (row.get("available_time") or row.get("time") or "").strip()
        if not raw_available:
            continue
        available_at = datetime.fromtimestamp(int(raw_available), tz=timezone.utc)
        raw_open = (row.get("source_open_time") or "").strip()
        source_open_at = datetime.fromtimestamp(int(raw_open), tz=timezone.utc) if raw_open else None
        rows.append(
            MT5IndicatorRow(
                available_at,
                float(row["sma"]),
                float(row["ema"]),
                float(row["atr"]),
                float(row["rsi"]),
                source_open_at=source_open_at,
            )
        )
    return tuple(rows)


def compare_mt5_indicators(
    features: Iterable[FeatureVector],
    mt5_rows: Iterable[MT5IndicatorRow],
    *,
    config: FeatureConfig = FeatureConfig(),
    abs_tolerance: float = 1e-8,
    min_rows: int = 20,
) -> IndicatorParityResult:
    if abs_tolerance < 0 or min_rows < 1:
        raise ValueError("invalid parity thresholds")
    feature_map = {row.at: row.feature_map() for row in features}
    keys = {
        "sma": f"sma_{config.ma_period}",
        "ema": f"ema_{config.ma_period}",
        "atr": f"atr_{config.atr_period}",
        "rsi": f"rsi_{config.rsi_period}",
    }
    errors = {name: 0.0 for name in keys}
    matched = 0
    for row in mt5_rows:
        values = feature_map.get(row.at)
        if values is None or any(key not in values for key in keys.values()):
            continue
        matched += 1
        for name, key in keys.items():
            errors[name] = max(errors[name], abs(float(values[key]) - float(getattr(row, name))))
    reasons = []
    if matched < min_rows:
        reasons.append("insufficient_parity_rows")
    for name, error in errors.items():
        if error > abs_tolerance:
            reasons.append(f"{name}_parity_failed:{error:.12g}")
    return IndicatorParityResult(matched, tuple(sorted(errors.items())), not reasons, tuple(reasons))
