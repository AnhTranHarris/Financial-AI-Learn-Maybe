from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping

from .risk import OutcomeQuality, RiskConstitution, classify_outcome


class CapitalHealth(StrEnum):
    THRIVING = "thriving"
    HEALTHY = "healthy"
    CAUTION = "caution"
    DEFENSIVE = "defensive"
    CRITICAL = "critical"
    CAPITAL_INSUFFICIENT = "capital_insufficient"


class GrowthAction(StrEnum):
    EXPAND_OPPORTUNITY_SET = "expand_opportunity_set"
    HOLD = "hold"
    DE_RISK = "de_risk"
    RESEARCH_ONLY = "research_only"


class CapitalFeedbackClass(StrEnum):
    EXEMPLARY_GROWTH = "exemplary_growth"
    VALID_GROWTH = "valid_growth"
    VALID_LOSS = "valid_loss"
    VALID_FLAT = "valid_flat"
    GOVERNANCE_FAILURE = "governance_failure"


@dataclass(frozen=True, slots=True)
class CapitalState:
    starting_capital: float
    current_equity: float
    high_water_mark: float
    net_external_flows: float = 0.0

    def __post_init__(self) -> None:
        if min(self.starting_capital, self.current_equity, self.high_water_mark) <= 0:
            raise ValueError("capital values must be positive")
        if self.high_water_mark + 1e-12 < self.current_equity:
            raise ValueError("high-water mark cannot be below current equity")

    @property
    def trading_pnl(self) -> float:
        return self.current_equity - self.starting_capital - self.net_external_flows

    @property
    def growth_fraction(self) -> float:
        return self.trading_pnl / self.starting_capital

    @property
    def drawdown_fraction(self) -> float:
        return max(0.0, (self.high_water_mark - self.current_equity) / self.high_water_mark)


@dataclass(frozen=True, slots=True)
class CapitalFeedback:
    classification: CapitalFeedbackClass
    financial_outcome: OutcomeQuality
    research_priority_increased: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResearchCycle:
    starting_capital: float
    ending_equity: float
    max_drawdown_fraction: float
    trade_count: int
    rules_ok: bool
    statistical_ok: bool
    execution_ok: bool
    largest_winner_fraction: float = 0.0

    def __post_init__(self) -> None:
        if self.starting_capital <= 0 or self.ending_equity <= 0 or self.trade_count < 0:
            raise ValueError("invalid research cycle capital/trade count")
        if not 0.0 <= self.max_drawdown_fraction <= 1.0:
            raise ValueError("drawdown must be in [0,1]")
        if not 0.0 <= self.largest_winner_fraction <= 1.0:
            raise ValueError("winner concentration must be in [0,1]")


@dataclass(frozen=True, slots=True)
class ResearchCyclePolicy:
    min_growth_fraction: float = 0.0
    max_drawdown_fraction: float = 0.08
    max_largest_winner_fraction: float = 0.60
    min_trades: int = 1

    def __post_init__(self) -> None:
        if self.min_growth_fraction < 0 or not 0 < self.max_drawdown_fraction <= 1:
            raise ValueError("invalid cycle growth/drawdown policy")
        if not 0 < self.max_largest_winner_fraction <= 1 or self.min_trades < 1:
            raise ValueError("invalid cycle concentration/sample policy")


@dataclass(frozen=True, slots=True)
class CycleAssessment:
    passed: bool
    growth_fraction: float
    reasons: tuple[str, ...]


def classify_capital_health(
    state: CapitalState,
    *,
    minimum_viable_capital: float = 0.0,
    constitution: RiskConstitution = RiskConstitution(),
) -> CapitalHealth:
    if minimum_viable_capital < 0:
        raise ValueError("minimum viable capital cannot be negative")
    if minimum_viable_capital and state.current_equity < minimum_viable_capital:
        return CapitalHealth.CAPITAL_INSUFFICIENT
    drawdown = state.drawdown_fraction
    if drawdown >= constitution.drawdown_research_only:
        return CapitalHealth.CRITICAL
    if drawdown >= constitution.drawdown_defensive:
        return CapitalHealth.DEFENSIVE
    if drawdown >= constitution.drawdown_caution:
        return CapitalHealth.CAUTION
    if state.growth_fraction >= 0.05 and drawdown < 0.01:
        return CapitalHealth.THRIVING
    return CapitalHealth.HEALTHY


def deployment_multiplier(health: CapitalHealth) -> float:
    """Growth may use less of the envelope, never more than the envelope it was given."""
    return {
        CapitalHealth.THRIVING: 1.0,
        CapitalHealth.HEALTHY: 1.0,
        CapitalHealth.CAUTION: 0.75,
        CapitalHealth.DEFENSIVE: 0.50,
        CapitalHealth.CRITICAL: 0.0,
        CapitalHealth.CAPITAL_INSUFFICIENT: 0.0,
    }[health]


def growth_action(health: CapitalHealth) -> GrowthAction:
    return {
        CapitalHealth.THRIVING: GrowthAction.EXPAND_OPPORTUNITY_SET,
        CapitalHealth.HEALTHY: GrowthAction.HOLD,
        CapitalHealth.CAUTION: GrowthAction.DE_RISK,
        CapitalHealth.DEFENSIVE: GrowthAction.DE_RISK,
        CapitalHealth.CRITICAL: GrowthAction.RESEARCH_ONLY,
        CapitalHealth.CAPITAL_INSUFFICIENT: GrowthAction.RESEARCH_ONLY,
    }[health]


def capital_feedback(
    *,
    pnl: float,
    rules_followed: bool,
    drawdown_fraction: float,
) -> CapitalFeedback:
    """Automaton-inspired resource feedback without allowing profit to override the constitution."""
    outcome = classify_outcome(pnl=pnl, rules_followed=rules_followed)
    if not rules_followed:
        return CapitalFeedback(
            CapitalFeedbackClass.GOVERNANCE_FAILURE,
            outcome,
            True,
            ("rules_override_financial_outcome",),
        )
    if pnl > 0:
        classification = (
            CapitalFeedbackClass.EXEMPLARY_GROWTH
            if drawdown_fraction <= 0.01
            else CapitalFeedbackClass.VALID_GROWTH
        )
        return CapitalFeedback(classification, outcome, False)
    if pnl < 0:
        return CapitalFeedback(
            CapitalFeedbackClass.VALID_LOSS,
            outcome,
            True,
            ("capital_loss_increases_investigation_not_position_size",),
        )
    return CapitalFeedback(CapitalFeedbackClass.VALID_FLAT, outcome, False)


def assess_research_cycle(
    cycle: ResearchCycle,
    policy: ResearchCyclePolicy = ResearchCyclePolicy(),
) -> CycleAssessment:
    growth = (cycle.ending_equity - cycle.starting_capital) / cycle.starting_capital
    reasons: list[str] = []
    if growth <= policy.min_growth_fraction:
        reasons.append("growth_requirement_failed")
    if cycle.max_drawdown_fraction > policy.max_drawdown_fraction:
        reasons.append("drawdown_requirement_failed")
    if cycle.trade_count < policy.min_trades:
        reasons.append("insufficient_trades")
    if not cycle.rules_ok:
        reasons.append("rule_violation")
    if not cycle.statistical_ok:
        reasons.append("statistical_gate_failed")
    if not cycle.execution_ok:
        reasons.append("execution_gate_failed")
    if cycle.largest_winner_fraction > policy.max_largest_winner_fraction:
        reasons.append("profit_concentration_failed")
    return CycleAssessment(not reasons, growth, tuple(reasons))


def next_cycle_capital(
    current_starting_capital: float,
    proposed_next_capital: float,
    *,
    cycle_passed: bool,
) -> float:
    """Capital compression is earned. Failure repeats the same starting capital."""
    if current_starting_capital <= 0 or proposed_next_capital <= 0:
        raise ValueError("cycle capital must be positive")
    if proposed_next_capital > current_starting_capital:
        raise ValueError("capital compression cannot increase starting capital")
    return proposed_next_capital if cycle_passed else current_starting_capital


def compression_ladder(
    starting_capital: float,
    *,
    floor: float = 100.0,
    ratio: float = 0.8,
    max_levels: int = 32,
) -> tuple[float, ...]:
    if starting_capital <= 0 or floor <= 0 or starting_capital < floor:
        raise ValueError("starting capital must be at or above positive floor")
    if not 0.0 < ratio < 1.0 or max_levels < 1:
        raise ValueError("compression ratio/max_levels invalid")
    values = [float(starting_capital)]
    while values[-1] > floor and len(values) < max_levels:
        candidate = max(floor, values[-1] * ratio)
        if abs(candidate - values[-1]) < 1e-12:
            break
        values.append(candidate)
    if values[-1] > floor and len(values) < max_levels:
        values.append(float(floor))
    return tuple(values)


def eligible_strategies_at_capital(
    capital: float,
    minimum_viable_capital_by_strategy: Mapping[str, float],
) -> tuple[str, ...]:
    """At micro-capital, zero eligible strategies is a valid and safe portfolio."""
    if capital <= 0:
        raise ValueError("capital must be positive")
    invalid = [name for name, minimum in minimum_viable_capital_by_strategy.items() if minimum <= 0]
    if invalid:
        raise ValueError("minimum viable capital estimates must be positive")
    return tuple(
        sorted(
            strategy
            for strategy, minimum in minimum_viable_capital_by_strategy.items()
            if capital + 1e-12 >= minimum
        )
    )
