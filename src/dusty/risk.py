from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from .experience import TradeSide


class RiskState(StrEnum):
    NORMAL = "normal"
    CAUTION = "caution"
    DEFENSIVE = "defensive"
    RESEARCH_ONLY = "research_only"
    FAILED = "failed"


class OutcomeQuality(StrEnum):
    GOOD_WIN = "good_win"
    BAD_WIN = "bad_win"
    GOOD_LOSS = "good_loss"
    BAD_LOSS = "bad_loss"
    VALID_FLAT = "valid_flat"
    INVALID_FLAT = "invalid_flat"


@dataclass(frozen=True, slots=True)
class RiskConstitution:
    normal_trade_risk: float = 0.0025
    champion_soft_max_trade_risk: float = 0.005
    hard_max_trade_risk: float = 0.01
    same_symbol_hard_risk: float = 0.01
    portfolio_heat_soft: float = 0.015
    portfolio_heat_hard: float = 0.02
    daily_warning_loss: float = 0.01
    daily_hard_loss: float = 0.02
    weekly_warning_loss: float = 0.025
    weekly_hard_loss: float = 0.04
    drawdown_caution: float = 0.02
    drawdown_defensive: float = 0.04
    drawdown_research_only: float = 0.06
    drawdown_fail: float = 0.08
    margin_soft: float = 0.15
    margin_hard: float = 0.30

    def __post_init__(self) -> None:
        values = (
            self.normal_trade_risk,
            self.champion_soft_max_trade_risk,
            self.hard_max_trade_risk,
            self.same_symbol_hard_risk,
            self.portfolio_heat_soft,
            self.portfolio_heat_hard,
            self.daily_warning_loss,
            self.daily_hard_loss,
            self.weekly_warning_loss,
            self.weekly_hard_loss,
            self.drawdown_caution,
            self.drawdown_defensive,
            self.drawdown_research_only,
            self.drawdown_fail,
            self.margin_soft,
            self.margin_hard,
        )
        if any(not math.isfinite(value) or not 0.0 < value < 1.0 for value in values):
            raise ValueError("risk constitution fractions must be finite and in (0,1)")
        if not (
            self.normal_trade_risk
            <= self.champion_soft_max_trade_risk
            <= self.hard_max_trade_risk
        ):
            raise ValueError("trade-risk hierarchy is invalid")
        if self.portfolio_heat_soft > self.portfolio_heat_hard:
            raise ValueError("portfolio heat hierarchy is invalid")
        if self.margin_soft > self.margin_hard:
            raise ValueError("margin hierarchy is invalid")
        if not (
            self.drawdown_caution
            < self.drawdown_defensive
            < self.drawdown_research_only
            < self.drawdown_fail
        ):
            raise ValueError("drawdown hierarchy is invalid")


@dataclass(frozen=True, slots=True)
class AccountRiskSnapshot:
    equity: float
    balance: float
    high_water_mark: float
    day_start_equity: float
    week_start_equity: float
    margin_used: float
    portfolio_heat: float
    same_symbol_heat: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.equity,
            self.balance,
            self.high_water_mark,
            self.day_start_equity,
            self.week_start_equity,
            self.margin_used,
            self.portfolio_heat,
            self.same_symbol_heat,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("account risk values must be finite")
        if self.equity < 0 or self.balance < 0:
            raise ValueError("account equity and balance cannot be negative")
        if min(self.high_water_mark, self.day_start_equity, self.week_start_equity) <= 0:
            raise ValueError("account reference equities must be positive")
        if self.margin_used < 0 or self.portfolio_heat < 0 or self.same_symbol_heat < 0:
            raise ValueError("margin and risk heat cannot be negative")
        if self.high_water_mark + 1e-12 < self.equity:
            raise ValueError("high-water mark cannot be below equity")

    @property
    def drawdown(self) -> float:
        return max(0.0, (self.high_water_mark - self.equity) / self.high_water_mark)

    @property
    def daily_loss(self) -> float:
        return max(0.0, (self.day_start_equity - self.equity) / self.day_start_equity)

    @property
    def weekly_loss(self) -> float:
        return max(0.0, (self.week_start_equity - self.equity) / self.week_start_equity)

    @property
    def margin_fraction(self) -> float:
        if self.equity == 0:
            return math.inf if self.margin_used > 0 else 0.0
        return self.margin_used / self.equity


@dataclass(frozen=True, slots=True)
class TradeRiskRequest:
    proposed_risk: float
    post_trade_portfolio_heat: float
    post_trade_same_symbol_heat: float
    post_trade_margin_used: float
    has_initial_stop: bool
    stop_widening: bool = False
    martingale: bool = False
    loss_recovery_sizing: bool = False
    unbounded_averaging: bool = False
    complete_risk_data: bool = True

    def __post_init__(self) -> None:
        values = (
            self.proposed_risk,
            self.post_trade_portfolio_heat,
            self.post_trade_same_symbol_heat,
            self.post_trade_margin_used,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("trade risk request values must be finite")
        if any(value < 0 for value in values):
            raise ValueError("trade risk request values cannot be negative")


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    allowed: bool
    state: RiskState
    risk_multiplier: float
    reasons: tuple[str, ...]


def risk_state(
    snapshot: AccountRiskSnapshot,
    constitution: RiskConstitution = RiskConstitution(),
) -> RiskState:
    if snapshot.equity == 0 or snapshot.drawdown >= constitution.drawdown_fail:
        return RiskState.FAILED
    if (
        snapshot.drawdown >= constitution.drawdown_research_only
        or snapshot.daily_loss >= constitution.daily_hard_loss
        or snapshot.weekly_loss >= constitution.weekly_hard_loss
    ):
        return RiskState.RESEARCH_ONLY
    if snapshot.drawdown >= constitution.drawdown_defensive:
        return RiskState.DEFENSIVE
    if (
        snapshot.drawdown >= constitution.drawdown_caution
        or snapshot.daily_loss >= constitution.daily_warning_loss
        or snapshot.weekly_loss >= constitution.weekly_warning_loss
    ):
        return RiskState.CAUTION
    return RiskState.NORMAL


def risk_multiplier(state: RiskState) -> float:
    return {
        RiskState.NORMAL: 1.0,
        RiskState.CAUTION: 0.75,
        RiskState.DEFENSIVE: 0.50,
        RiskState.RESEARCH_ONLY: 0.0,
        RiskState.FAILED: 0.0,
    }[state]


def stop_change_allowed(side: TradeSide, current_stop: float, proposed_stop: float) -> bool:
    """Protective stops may tighten or stay unchanged; never widen risk after entry."""
    if any(not math.isfinite(value) for value in (current_stop, proposed_stop)):
        raise ValueError("stop prices must be finite")
    if current_stop <= 0 or proposed_stop <= 0:
        raise ValueError("stop prices must be positive")
    if side is TradeSide.LONG:
        return proposed_stop >= current_stop
    return proposed_stop <= current_stop


def assess_trade_risk(
    snapshot: AccountRiskSnapshot,
    request: TradeRiskRequest,
    constitution: RiskConstitution = RiskConstitution(),
) -> RiskAssessment:
    state = risk_state(snapshot, constitution)
    reasons: list[str] = []
    if state in {RiskState.RESEARCH_ONLY, RiskState.FAILED}:
        reasons.append(f"account_state:{state.value}")
    if request.proposed_risk <= 0 or request.proposed_risk > constitution.hard_max_trade_risk:
        reasons.append("trade_risk_ceiling")
    if request.post_trade_portfolio_heat > constitution.portfolio_heat_hard:
        reasons.append("portfolio_heat_ceiling")
    if request.post_trade_same_symbol_heat > constitution.same_symbol_hard_risk:
        reasons.append("same_symbol_heat_ceiling")
    margin_fraction = (
        math.inf
        if snapshot.equity == 0 and request.post_trade_margin_used > 0
        else (request.post_trade_margin_used / snapshot.equity if snapshot.equity > 0 else 0.0)
    )
    if margin_fraction > constitution.margin_hard:
        reasons.append("margin_ceiling")
    if not request.has_initial_stop:
        reasons.append("initial_stop_required")
    if request.stop_widening:
        reasons.append("stop_widening_prohibited")
    if request.martingale:
        reasons.append("martingale_prohibited")
    if request.loss_recovery_sizing:
        reasons.append("loss_recovery_sizing_prohibited")
    if request.unbounded_averaging:
        reasons.append("unbounded_averaging_prohibited")
    if not request.complete_risk_data:
        reasons.append("incomplete_risk_data")
    return RiskAssessment(not reasons, state, risk_multiplier(state), tuple(reasons))


def classify_outcome(*, pnl: float, rules_followed: bool) -> OutcomeQuality:
    """Profit never retroactively legitimizes an invalid decision."""
    if not math.isfinite(pnl):
        raise ValueError("pnl must be finite")
    if pnl > 0:
        return OutcomeQuality.GOOD_WIN if rules_followed else OutcomeQuality.BAD_WIN
    if pnl < 0:
        return OutcomeQuality.GOOD_LOSS if rules_followed else OutcomeQuality.BAD_LOSS
    return OutcomeQuality.VALID_FLAT if rules_followed else OutcomeQuality.INVALID_FLAT
