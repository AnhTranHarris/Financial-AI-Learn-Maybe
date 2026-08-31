from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from statistics import fmean
from typing import Iterable

from .experience import TradeSide


class EstimateState(StrEnum):
    MEASURED = "measured"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class StrategyReturn:
    strategy_hash: str
    symbol: str
    at: datetime
    value: float

    def __post_init__(self) -> None:
        if not self.strategy_hash.strip() or not self.symbol.strip():
            raise ValueError("strategy return requires strategy and symbol")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("strategy return timestamps must be timezone-aware")
        if not math.isfinite(self.value):
            raise ValueError("strategy return must be finite")


@dataclass(frozen=True, slots=True)
class VolatilityEstimate:
    strategy_hash: str
    sample_count: int
    volatility: float
    state: EstimateState


@dataclass(frozen=True, slots=True)
class CorrelationEstimate:
    left_hash: str
    right_hash: str
    sample_count: int
    correlation: float
    state: EstimateState


@dataclass(frozen=True, slots=True)
class PortfolioRiskModel:
    as_of: datetime
    volatilities: tuple[VolatilityEstimate, ...]
    correlations: tuple[CorrelationEstimate, ...]

    def correlation_map(self) -> dict[tuple[str, str], float]:
        return {(row.left_hash, row.right_hash): row.correlation for row in self.correlations}

    def volatility_map(self) -> dict[str, float]:
        return {row.strategy_hash: row.volatility for row in self.volatilities}


def _sample_std(values: tuple[float, ...]) -> float:
    if len(values) < 2:
        return 0.0
    mean = fmean(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _correlation(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("correlation requires paired samples")
    left_mean = fmean(left)
    right_mean = fmean(right)
    left_dev = tuple(value - left_mean for value in left)
    right_dev = tuple(value - right_mean for value in right)
    denominator = math.sqrt(sum(value * value for value in left_dev) * sum(value * value for value in right_dev))
    if denominator <= 1e-18:
        return 1.0
    value = sum(a * b for a, b in zip(left_dev, right_dev)) / denominator
    return max(-1.0, min(1.0, value))


def build_portfolio_risk_model(
    returns: Iterable[StrategyReturn],
    *,
    as_of: datetime,
    min_samples: int = 20,
    insufficient_correlation: float = 1.0,
    insufficient_volatility: float = 1.0,
) -> PortfolioRiskModel:
    """Estimate only from returns knowable by as_of; sparse history fails conservatively."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if min_samples < 2:
        raise ValueError("min_samples must be at least two")
    if not -1.0 <= insufficient_correlation <= 1.0 or not math.isfinite(insufficient_volatility) or insufficient_volatility <= 0:
        raise ValueError("invalid conservative fallback")
    rows = tuple(row for row in returns if row.at <= as_of)
    by_strategy: dict[str, list[StrategyReturn]] = {}
    for row in rows:
        by_strategy.setdefault(row.strategy_hash, []).append(row)
    volatilities = []
    for strategy_hash, group in sorted(by_strategy.items()):
        values = tuple(row.value for row in sorted(group, key=lambda item: item.at))
        if len(values) < min_samples:
            volatilities.append(VolatilityEstimate(strategy_hash, len(values), insufficient_volatility, EstimateState.INSUFFICIENT))
        else:
            measured = _sample_std(values)
            if measured <= 1e-12:
                measured = insufficient_volatility
                state = EstimateState.INSUFFICIENT
            else:
                state = EstimateState.MEASURED
            volatilities.append(VolatilityEstimate(strategy_hash, len(values), measured, state))

    correlations = []
    hashes = sorted(by_strategy)
    indexed = {
        key: {row.at: row.value for row in by_strategy[key]}
        for key in hashes
    }
    for left_index, left_hash in enumerate(hashes):
        for right_hash in hashes[left_index + 1 :]:
            common = sorted(set(indexed[left_hash]) & set(indexed[right_hash]))
            if len(common) < min_samples:
                correlations.append(
                    CorrelationEstimate(left_hash, right_hash, len(common), insufficient_correlation, EstimateState.INSUFFICIENT)
                )
                continue
            left = tuple(indexed[left_hash][at] for at in common)
            right = tuple(indexed[right_hash][at] for at in common)
            correlations.append(
                CorrelationEstimate(left_hash, right_hash, len(common), _correlation(left, right), EstimateState.MEASURED)
            )
    return PortfolioRiskModel(as_of, tuple(volatilities), tuple(correlations))


def derive_fx_factor_exposures(symbol: str, side: TradeSide) -> tuple[tuple[str, float], ...]:
    """Encode obvious signed currency exposure so pairs sharing USD cannot appear independent."""
    compact = "".join(ch for ch in symbol.upper() if ch.isalpha())
    if len(compact) != 6:
        return ((f"SYMBOL:{symbol.upper()}", 1.0),)
    base, quote = compact[:3], compact[3:]
    direction = 1.0 if side is TradeSide.LONG else -1.0
    return tuple(sorted(((f"CCY:{base}", direction), (f"CCY:{quote}", -direction))))
