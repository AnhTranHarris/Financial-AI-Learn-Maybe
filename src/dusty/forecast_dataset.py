from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from statistics import fmean, pstdev
from typing import Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class ForecastMarketBar:
    symbol: str
    timeframe: str
    source_open: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float
    spread_points: float = 0.0
    tick_volume: float = 0.0

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip():
            raise ValueError("forecast bar requires symbol and timeframe")
        _aware(self.source_open, "forecast bar source time")
        _aware(self.available_at, "forecast bar availability")
        if self.available_at <= self.source_open:
            raise ValueError("forecast bar must become available after source open")
        values = (self.open, self.high, self.low, self.close, self.spread_points, self.tick_volume)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("forecast bar values must be finite")
        if min(self.open, self.high, self.low, self.close) <= 0 or self.spread_points < 0 or self.tick_volume < 0:
            raise ValueError("forecast bar prices must be positive and activity nonnegative")
        if self.high < max(self.open, self.low, self.close) or self.low > min(self.open, self.high, self.close):
            raise ValueError("forecast bar OHLC geometry is invalid")


@dataclass(frozen=True, slots=True)
class ForecastContextValue:
    key: str
    value: float
    known_at: datetime
    source: str

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.source.strip() or not math.isfinite(self.value):
            raise ValueError("forecast context value is invalid")
        _aware(self.known_at, "forecast context knowledge time")


@dataclass(frozen=True, slots=True)
class ForecastFeatureFrame:
    symbol: str
    timeframe: str
    as_of: datetime
    values: tuple[tuple[str, float], ...]
    sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip() or not self.values:
            raise ValueError("forecast feature frame is incomplete")
        _aware(self.as_of, "forecast feature time")
        if len(dict(self.values)) != len(self.values):
            raise ValueError("forecast feature names must be unique")
        if any(not key.strip() or not math.isfinite(value) for key, value in self.values):
            raise ValueError("forecast features must be named and finite")
        if not self.sources or any(not source.strip() for source in self.sources):
            raise ValueError("forecast feature provenance is required")

    @property
    def fingerprint(self) -> str:
        payload = {
            "symbol": self.symbol.upper(),
            "timeframe": self.timeframe.upper(),
            "as_of": self.as_of.isoformat(),
            "values": self.values,
            "sources": self.sources,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class RollingForecastExample:
    features: ForecastFeatureFrame
    origin_price: float
    target_return: float
    target_known_at: datetime
    horizon_steps: int

    def __post_init__(self) -> None:
        if not math.isfinite(self.origin_price) or self.origin_price <= 0 or not math.isfinite(self.target_return):
            raise ValueError("rolling forecast example values are invalid")
        _aware(self.target_known_at, "forecast target knowledge time")
        if self.target_known_at <= self.features.as_of or self.horizon_steps < 1:
            raise ValueError("rolling forecast target must lie after the input frame")


def build_feature_frame(
    bars: Sequence[ForecastMarketBar],
    *,
    as_of: datetime,
    context: Iterable[ForecastContextValue] = (),
) -> ForecastFeatureFrame:
    _aware(as_of, "forecast frame as_of")
    rows = tuple(bar for bar in bars if bar.available_at <= as_of)
    if len(rows) < 2:
        raise ValueError("forecast frame requires at least two available bars")
    if tuple(sorted(rows, key=lambda row: row.available_at)) != rows:
        raise ValueError("forecast bars must be chronological")
    identities = {(row.symbol.upper(), row.timeframe.upper()) for row in rows}
    if len(identities) != 1:
        raise ValueError("forecast frame bars must share symbol and timeframe")
    returns = tuple(rows[index].close / rows[index - 1].close - 1 for index in range(1, len(rows)))
    values: dict[str, float] = {
        "price": rows[-1].close,
        "return_1": returns[-1],
        "return_mean": fmean(returns),
        "realized_volatility": pstdev(returns) if len(returns) > 1 else 0.0,
        "range_fraction_mean": fmean((row.high - row.low) / row.close for row in rows),
        "spread_points_mean": fmean(row.spread_points for row in rows),
        "tick_volume_mean": fmean(row.tick_volume for row in rows),
    }
    sources = {"mt5_completed_bars"}
    for item in context:
        if item.known_at > as_of:
            raise ValueError(f"future forecast context is not available: {item.key}")
        if item.key in values:
            raise ValueError(f"duplicate forecast feature: {item.key}")
        values[item.key] = item.value
        sources.add(item.source)
    symbol, timeframe = next(iter(identities))
    return ForecastFeatureFrame(symbol, timeframe, as_of, tuple(sorted(values.items())), tuple(sorted(sources)))


def build_rolling_examples(
    bars: Sequence[ForecastMarketBar],
    *,
    lookback: int,
    horizon_steps: int,
    contexts_by_as_of: Mapping[datetime, Sequence[ForecastContextValue]] | None = None,
) -> tuple[RollingForecastExample, ...]:
    if lookback < 2 or horizon_steps < 1:
        raise ValueError("rolling forecast widths are invalid")
    rows = tuple(bars)
    if tuple(sorted(rows, key=lambda row: row.available_at)) != rows:
        raise ValueError("rolling forecast bars must be chronological")
    if len({(row.symbol.upper(), row.timeframe.upper()) for row in rows}) > 1:
        raise ValueError("rolling forecast examples require one symbol/timeframe")
    contexts = contexts_by_as_of or {}
    examples = []
    for origin_index in range(lookback - 1, len(rows) - horizon_steps):
        window = rows[origin_index - lookback + 1 : origin_index + 1]
        origin = rows[origin_index]
        target = rows[origin_index + horizon_steps]
        frame = build_feature_frame(window, as_of=origin.available_at, context=contexts.get(origin.available_at, ()))
        examples.append(
            RollingForecastExample(
                frame,
                origin.close,
                target.close / origin.close - 1,
                target.available_at,
                horizon_steps,
            )
        )
    return tuple(examples)


def training_examples_as_of(
    examples: Iterable[RollingForecastExample],
    *,
    cutoff: datetime,
) -> tuple[RollingForecastExample, ...]:
    _aware(cutoff, "training cutoff")
    return tuple(example for example in examples if example.target_known_at <= cutoff)


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
