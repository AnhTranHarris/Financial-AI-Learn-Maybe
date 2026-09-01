from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable, Sequence

from .analytical_tools import ToolOrigin
from .chart_intelligence import ChartAnchor, ChartObjectKind, ChartObjectSpec


@dataclass(frozen=True, slots=True)
class PriceBar:
    source_open: datetime
    available_at: datetime
    open: float
    high: float
    low: float
    close: float

    def __post_init__(self) -> None:
        if self.source_open.tzinfo is None or self.source_open.utcoffset() is None or self.available_at.tzinfo is None or self.available_at.utcoffset() is None:
            raise ValueError("price structure bars require aware timestamps")
        if self.available_at <= self.source_open:
            raise ValueError("price bar availability must follow source open")
        if any(not math.isfinite(value) or value <= 0 for value in (self.open, self.high, self.low, self.close)):
            raise ValueError("price structure OHLC must be finite and positive")
        if self.high < max(self.open, self.low, self.close) or self.low > min(self.open, self.high, self.close):
            raise ValueError("price structure OHLC geometry is invalid")


class PivotKind(StrEnum):
    HIGH = "high"
    LOW = "low"


@dataclass(frozen=True, slots=True)
class ConfirmedPivot:
    kind: PivotKind
    source_open: datetime
    known_at: datetime
    price: float

    def __post_init__(self) -> None:
        if self.known_at <= self.source_open or not math.isfinite(self.price) or self.price <= 0:
            raise ValueError("confirmed pivot identity is invalid")


def confirmed_pivots(
    bars: Sequence[PriceBar],
    *,
    left_bars: int = 2,
    right_bars: int = 2,
) -> tuple[ConfirmedPivot, ...]:
    if left_bars < 1 or right_bars < 1:
        raise ValueError("pivot confirmation widths must be positive")
    if tuple(sorted(bars, key=lambda row: row.source_open)) != tuple(bars):
        raise ValueError("price structure bars must be chronological")
    result: list[ConfirmedPivot] = []
    for index in range(left_bars, len(bars) - right_bars):
        candidate = bars[index]
        neighbors = bars[index - left_bars : index] + bars[index + 1 : index + right_bars + 1]
        known_at = bars[index + right_bars].available_at
        if all(candidate.high > row.high for row in neighbors):
            result.append(ConfirmedPivot(PivotKind.HIGH, candidate.source_open, known_at, candidate.high))
        if all(candidate.low < row.low for row in neighbors):
            result.append(ConfirmedPivot(PivotKind.LOW, candidate.source_open, known_at, candidate.low))
    return tuple(sorted(result, key=lambda row: (row.known_at, row.source_open, row.kind.value)))


class MarketStructure(StrEnum):
    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    RANGE_OR_MIXED = "range_or_mixed"
    UNDETERMINED = "undetermined"


def classify_market_structure(pivots: Iterable[ConfirmedPivot]) -> MarketStructure:
    rows = tuple(pivots)
    highs = tuple(row for row in rows if row.kind is PivotKind.HIGH)
    lows = tuple(row for row in rows if row.kind is PivotKind.LOW)
    if len(highs) < 2 or len(lows) < 2:
        return MarketStructure.UNDETERMINED
    higher_high = highs[-1].price > highs[-2].price
    higher_low = lows[-1].price > lows[-2].price
    lower_high = highs[-1].price < highs[-2].price
    lower_low = lows[-1].price < lows[-2].price
    if higher_high and higher_low:
        return MarketStructure.UPTREND
    if lower_high and lower_low:
        return MarketStructure.DOWNTREND
    return MarketStructure.RANGE_OR_MIXED


def pivot_trendline(
    pivots: Iterable[ConfirmedPivot],
    *,
    kind: PivotKind,
    symbol: str,
    timeframe: str,
    object_id: str,
) -> ChartObjectSpec:
    rows = tuple(row for row in pivots if row.kind is kind)
    if len(rows) < 2:
        raise ValueError("trendline requires two confirmed pivots")
    left, right = rows[-2:]
    return ChartObjectSpec(
        object_id,
        ChartObjectKind.TREND_LINE,
        symbol,
        timeframe,
        (ChartAnchor(left.source_open, left.price), ChartAnchor(right.source_open, right.price)),
        max(left.known_at, right.known_at),
        ToolOrigin.DUSTY_GENERATED,
        label=f"confirmed_{kind.value}_trendline",
    )


@dataclass(frozen=True, slots=True)
class PriceZone:
    lower: float
    upper: float
    touch_count: int
    known_at: datetime

    def __post_init__(self) -> None:
        if self.lower <= 0 or self.upper < self.lower or self.touch_count < 1:
            raise ValueError("price zone is invalid")


def cluster_pivot_zones(
    pivots: Iterable[ConfirmedPivot],
    *,
    maximum_distance: float,
    minimum_touches: int = 2,
    maximum_zones: int = 16,
) -> tuple[PriceZone, ...]:
    if not math.isfinite(maximum_distance) or maximum_distance <= 0 or min(minimum_touches, maximum_zones) < 1:
        raise ValueError("price-zone policy is invalid")
    rows = tuple(sorted(pivots, key=lambda row: row.price))
    clusters: list[list[ConfirmedPivot]] = []
    for row in rows:
        if not clusters or row.price - clusters[-1][-1].price > maximum_distance:
            clusters.append([row])
        else:
            clusters[-1].append(row)
    zones = tuple(
        PriceZone(group[0].price, group[-1].price, len(group), max(row.known_at for row in group))
        for group in clusters
        if len(group) >= minimum_touches
    )
    if len(zones) > maximum_zones:
        raise ValueError("price-zone cardinality budget exceeded")
    return zones
