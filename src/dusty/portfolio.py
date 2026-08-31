from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping


class AllocationMethod(StrEnum):
    EQUAL_RISK = "equal_risk"
    INVERSE_VOLATILITY = "inverse_volatility"
    QUALITY_VOL_CORRELATION = "quality_vol_correlation"


@dataclass(frozen=True, slots=True)
class PortfolioCandidate:
    strategy_hash: str
    symbol: str
    quality_score: float
    volatility: float
    max_risk: float
    factor_exposures: tuple[tuple[str, float], ...] = ()

    def __post_init__(self) -> None:
        if not self.strategy_hash.strip() or not self.symbol.strip():
            raise ValueError("portfolio candidate requires strategy and symbol")
        if any(not math.isfinite(value) for value in (self.quality_score, self.volatility, self.max_risk)):
            raise ValueError("candidate quality/volatility/risk must be finite")
        if self.quality_score < 0 or self.volatility <= 0 or self.max_risk <= 0:
            raise ValueError("candidate quality/risk must be nonnegative and volatility positive")
        factors = [name for name, _ in self.factor_exposures]
        if len(set(factors)) != len(factors):
            raise ValueError("factor exposures must be unique")
        if any(
            not name.strip() or not math.isfinite(exposure) or abs(exposure) > 1.0
            for name, exposure in self.factor_exposures
        ):
            raise ValueError("factor exposures require names and finite coefficients in [-1,1]")


@dataclass(frozen=True, slots=True)
class QuantPortfolioPolicy:
    max_symbol_heat: float = 0.01
    max_factor_heat: float = 0.0125

    def __post_init__(self) -> None:
        if any(not math.isfinite(value) for value in (self.max_symbol_heat, self.max_factor_heat)):
            raise ValueError("portfolio policy heat limits must be finite")
        if self.max_symbol_heat <= 0 or self.max_factor_heat <= 0:
            raise ValueError("portfolio policy heat limits must be positive")


@dataclass(frozen=True, slots=True)
class RiskAllocation:
    strategy_hash: str
    symbol: str
    risk: float
    score: float


@dataclass(frozen=True, slots=True)
class PortfolioAllocation:
    allocations: tuple[RiskAllocation, ...]
    portfolio_heat: float
    unallocated_risk: float
    symbol_heat: tuple[tuple[str, float], ...]
    factor_heat: tuple[tuple[str, float], ...]


def _correlation(
    left: str,
    right: str,
    correlations: Mapping[tuple[str, str], float],
) -> float:
    if left == right:
        return 1.0
    value = float(correlations.get((left, right), correlations.get((right, left), 0.0)))
    if not math.isfinite(value) or not -1.0 <= value <= 1.0:
        raise ValueError("correlations must be finite and in [-1,1]")
    return value


def _candidate_score(
    candidate: PortfolioCandidate,
    all_candidates: tuple[PortfolioCandidate, ...],
    correlations: Mapping[tuple[str, str], float],
    method: AllocationMethod,
) -> float:
    if method is AllocationMethod.EQUAL_RISK:
        return 1.0
    if method is AllocationMethod.INVERSE_VOLATILITY:
        return 1.0 / candidate.volatility
    peers = [
        abs(_correlation(candidate.strategy_hash, peer.strategy_hash, correlations))
        for peer in all_candidates
        if peer.strategy_hash != candidate.strategy_hash
    ]
    correlation_pressure = sum(peers) / len(peers) if peers else 0.0
    return candidate.quality_score / (candidate.volatility * (1.0 + correlation_pressure))


def _factor_room(current: float, exposure: float, cap: float) -> float:
    if abs(current) > cap + 1e-12:
        return 0.0
    if exposure > 0:
        return max(0.0, (cap - current) / exposure)
    if exposure < 0:
        return max(0.0, (cap + current) / -exposure)
    return float("inf")


def allocate_portfolio(
    candidates: Iterable[PortfolioCandidate],
    *,
    total_risk_budget: float,
    correlations: Mapping[tuple[str, str], float] | None = None,
    policy: QuantPortfolioPolicy = QuantPortfolioPolicy(),
    method: AllocationMethod = AllocationMethod.QUALITY_VOL_CORRELATION,
) -> PortfolioAllocation:
    """Allocate a supplied risk budget; the Quant PM is never allowed to create more risk."""
    if not math.isfinite(total_risk_budget) or total_risk_budget < 0:
        raise ValueError("total risk budget must be finite and nonnegative")
    rows = tuple(candidates)
    if len({row.strategy_hash for row in rows}) != len(rows):
        raise ValueError("strategy candidates must be unique")
    if not rows or total_risk_budget == 0:
        return PortfolioAllocation((), 0.0, total_risk_budget, (), ())
    matrix = correlations or {}
    scored = tuple((row, _candidate_score(row, rows, matrix, method)) for row in rows)
    if any(not math.isfinite(score) or score < 0 for _, score in scored):
        raise ValueError("portfolio candidate scores must be finite and nonnegative")
    total_score = sum(score for _, score in scored)
    if total_score <= 0:
        return PortfolioAllocation((), 0.0, total_risk_budget, (), ())

    symbol_heat: dict[str, float] = {}
    factor_heat: dict[str, float] = {}
    allocations: list[RiskAllocation] = []
    used = 0.0

    for candidate, score in sorted(scored, key=lambda item: (-item[1], item[0].strategy_hash)):
        target = total_risk_budget * score / total_score
        room = min(
            candidate.max_risk,
            max(0.0, total_risk_budget - used),
            max(0.0, policy.max_symbol_heat - symbol_heat.get(candidate.symbol, 0.0)),
        )
        for factor, exposure in candidate.factor_exposures:
            room = min(
                room,
                _factor_room(factor_heat.get(factor, 0.0), exposure, policy.max_factor_heat),
            )
        risk = min(target, room)
        if risk <= 0:
            continue
        allocations.append(RiskAllocation(candidate.strategy_hash, candidate.symbol, risk, score))
        used += risk
        symbol_heat[candidate.symbol] = symbol_heat.get(candidate.symbol, 0.0) + risk
        for factor, exposure in candidate.factor_exposures:
            factor_heat[factor] = factor_heat.get(factor, 0.0) + risk * exposure

    if used > total_risk_budget + 1e-12:
        raise AssertionError("portfolio manager created risk beyond supplied budget")
    if any(value > policy.max_symbol_heat + 1e-12 for value in symbol_heat.values()):
        raise AssertionError("portfolio manager exceeded symbol heat")
    if any(abs(value) > policy.max_factor_heat + 1e-12 for value in factor_heat.values()):
        raise AssertionError("portfolio manager exceeded factor heat")
    return PortfolioAllocation(
        allocations=tuple(sorted(allocations, key=lambda item: item.strategy_hash)),
        portfolio_heat=used,
        unallocated_risk=max(0.0, total_risk_budget - used),
        symbol_heat=tuple(sorted(symbol_heat.items())),
        factor_heat=tuple(sorted(factor_heat.items())),
    )
