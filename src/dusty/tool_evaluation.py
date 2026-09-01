from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class PerformanceWindow:
    window_id: str
    trade_count: int
    net_pnl: float
    gross_profit: float
    gross_loss: float
    maximum_drawdown_fraction: float
    transaction_cost: float
    profit_concentration: float
    rule_violations: int = 0

    def __post_init__(self) -> None:
        values = (
            self.net_pnl,
            self.gross_profit,
            self.gross_loss,
            self.maximum_drawdown_fraction,
            self.transaction_cost,
            self.profit_concentration,
        )
        if not self.window_id.strip() or self.trade_count < 0 or self.rule_violations < 0:
            raise ValueError("performance window identity/counts are invalid")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("performance values must be finite")
        if self.gross_profit < 0 or self.gross_loss > 0 or self.transaction_cost < 0:
            raise ValueError("performance cash-flow signs are invalid")
        if not 0 <= self.maximum_drawdown_fraction <= 1 or not 0 <= self.profit_concentration <= 1:
            raise ValueError("performance fractions must be in [0,1]")

    @property
    def expectancy(self) -> float:
        return 0.0 if self.trade_count == 0 else self.net_pnl / self.trade_count

    @property
    def profit_factor(self) -> float:
        if self.gross_loss == 0:
            return math.inf if self.gross_profit > 0 else 0.0
        return self.gross_profit / abs(self.gross_loss)


class ContributionDecision(StrEnum):
    RETAIN = "retain"
    RESTRICT = "restrict"
    MODIFY = "modify"
    RETIRE = "retire"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


@dataclass(frozen=True, slots=True)
class ToolAblationEvidence:
    tool_fingerprint: str
    full_strategy: PerformanceWindow
    without_tool: PerformanceWindow
    regime_deltas: tuple[tuple[str, float], ...] = ()
    repair_hypothesis: str = ""

    def __post_init__(self) -> None:
        if len(self.tool_fingerprint) != 64:
            raise ValueError("ablation tool identity must be SHA-256")
        if any(not name.strip() or not math.isfinite(value) for name, value in self.regime_deltas):
            raise ValueError("ablation regime deltas are invalid")

    @property
    def incremental_expectancy(self) -> float:
        return self.full_strategy.expectancy - self.without_tool.expectancy


@dataclass(frozen=True, slots=True)
class ContributionAssessment:
    decision: ContributionDecision
    incremental_expectancy: float
    reasons: tuple[str, ...]


def assess_tool_contribution(
    evidence: ToolAblationEvidence,
    *,
    minimum_trades: int = 30,
    minimum_incremental_expectancy: float = 0.0,
) -> ContributionAssessment:
    if minimum_trades < 1 or not math.isfinite(minimum_incremental_expectancy):
        raise ValueError("ablation policy is invalid")
    if min(evidence.full_strategy.trade_count, evidence.without_tool.trade_count) < minimum_trades:
        return ContributionAssessment(ContributionDecision.INSUFFICIENT_EVIDENCE, evidence.incremental_expectancy, ("insufficient_ablation_trades",))
    if evidence.full_strategy.rule_violations > evidence.without_tool.rule_violations:
        return ContributionAssessment(ContributionDecision.RETIRE, evidence.incremental_expectancy, ("tool_increases_rule_violations",))
    positive_regimes = tuple(name for name, delta in evidence.regime_deltas if delta > minimum_incremental_expectancy)
    negative_regimes = tuple(name for name, delta in evidence.regime_deltas if delta <= minimum_incremental_expectancy)
    if positive_regimes and negative_regimes and evidence.incremental_expectancy <= minimum_incremental_expectancy:
        return ContributionAssessment(
            ContributionDecision.RESTRICT,
            evidence.incremental_expectancy,
            tuple(f"useful_regime:{name}" for name in positive_regimes),
        )
    if evidence.incremental_expectancy <= minimum_incremental_expectancy:
        if evidence.repair_hypothesis.strip():
            return ContributionAssessment(ContributionDecision.MODIFY, evidence.incremental_expectancy, ("repairable_failure_hypothesis",))
        return ContributionAssessment(ContributionDecision.RETIRE, evidence.incremental_expectancy, ("no_incremental_value",))
    if evidence.full_strategy.maximum_drawdown_fraction > evidence.without_tool.maximum_drawdown_fraction:
        return ContributionAssessment(ContributionDecision.MODIFY, evidence.incremental_expectancy, ("value_with_drawdown_penalty",))
    return ContributionAssessment(ContributionDecision.RETAIN, evidence.incremental_expectancy, ("positive_incremental_value",))


@dataclass(frozen=True, slots=True)
class TournamentPolicy:
    minimum_test_trades: int = 100
    minimum_expectancy: float = 0.0
    minimum_profit_factor: float = 1.0
    maximum_drawdown_fraction: float = 0.20
    maximum_profit_concentration: float = 0.35
    minimum_parameter_neighbors_passed: int = 2

    def __post_init__(self) -> None:
        if self.minimum_test_trades < 1 or self.minimum_parameter_neighbors_passed < 1:
            raise ValueError("tournament count policy is invalid")
        if self.minimum_profit_factor < 1 or not 0 < self.maximum_drawdown_fraction <= 1:
            raise ValueError("tournament financial policy is invalid")
        if not 0 <= self.maximum_profit_concentration <= 1 or not math.isfinite(self.minimum_expectancy):
            raise ValueError("tournament concentration/expectancy policy is invalid")


@dataclass(frozen=True, slots=True)
class TournamentCandidate:
    strategy_hash: str
    graph_hash: str
    validation: PerformanceWindow
    untouched_test: PerformanceWindow
    native_indicator_parity: bool
    native_execution_parity: bool
    tools_valid: bool
    parameter_neighbors_passed: int
    registered_trial_count: int

    def __post_init__(self) -> None:
        if len(self.strategy_hash) != 64 or len(self.graph_hash) != 64:
            raise ValueError("tournament candidate requires strategy/graph hashes")
        if self.parameter_neighbors_passed < 0 or self.registered_trial_count < 1:
            raise ValueError("tournament search evidence is invalid")


@dataclass(frozen=True, slots=True)
class CandidateAssessment:
    eligible: bool
    reasons: tuple[str, ...]


def assess_tournament_candidate(
    candidate: TournamentCandidate,
    policy: TournamentPolicy = TournamentPolicy(),
) -> CandidateAssessment:
    reasons: list[str] = []
    test = candidate.untouched_test
    if not candidate.tools_valid:
        reasons.append("invalid_analytical_dependency")
    if not candidate.native_indicator_parity:
        reasons.append("native_indicator_parity_failed")
    if not candidate.native_execution_parity:
        reasons.append("native_execution_parity_failed")
    if test.trade_count < policy.minimum_test_trades:
        reasons.append("insufficient_untouched_trades")
    if test.expectancy <= policy.minimum_expectancy:
        reasons.append("untouched_expectancy_failed")
    if test.profit_factor < policy.minimum_profit_factor:
        reasons.append("profit_factor_failed")
    if test.maximum_drawdown_fraction > policy.maximum_drawdown_fraction:
        reasons.append("drawdown_failed")
    if test.profit_concentration > policy.maximum_profit_concentration:
        reasons.append("profit_concentration_failed")
    if test.rule_violations:
        reasons.append("trading_rule_violation")
    if candidate.parameter_neighbors_passed < policy.minimum_parameter_neighbors_passed:
        reasons.append("parameter_neighborhood_unstable")
    if candidate.validation.expectancy <= 0:
        reasons.append("validation_expectancy_failed")
    return CandidateAssessment(not reasons, tuple(reasons))

