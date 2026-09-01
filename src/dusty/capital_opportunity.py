from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .experience import TradeSide
from .market_clock import MarketClockAssessment


@dataclass(frozen=True, slots=True)
class SettledCapitalState:
    starting_balance: float
    deposits: float
    withdrawals: float
    settled_realized_pnl: float
    balance: float
    equity: float
    protected_reserve: float = 0.0

    def __post_init__(self) -> None:
        values = (self.starting_balance, self.deposits, self.withdrawals, self.settled_realized_pnl, self.balance, self.equity, self.protected_reserve)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("capital state must be finite")
        if min(self.starting_balance, self.deposits, self.withdrawals, self.balance, self.equity, self.protected_reserve) < 0:
            raise ValueError("capital balances/flows cannot be negative")
        expected = self.starting_balance + self.deposits - self.withdrawals + self.settled_realized_pnl
        if abs(expected - self.balance) > max(1e-6, abs(self.balance) * 1e-9):
            raise ValueError("settled capital ledger does not reconcile to balance")

    @property
    def floating_pnl(self) -> float:
        return self.equity - self.balance

    @property
    def conservative_deployable_capital(self) -> float:
        return max(0.0, min(self.balance, self.equity) - self.protected_reserve)

    @property
    def realized_growth_capital(self) -> float:
        return max(0.0, self.settled_realized_pnl)


@dataclass(frozen=True, slots=True)
class CertifiedOpportunity:
    opportunity_id: str
    symbol: str
    side: TradeSide
    strategy_hash: str
    forecast_model_fingerprint: str
    minimum_viable_capital: float
    requested_risk_fraction: float
    expected_net_edge_fraction: float
    expires_at: datetime
    setup_present: bool
    clock: MarketClockAssessment
    certified: bool = True

    def __post_init__(self) -> None:
        if not self.opportunity_id.strip() or not self.symbol.strip() or any(len(value) != 64 for value in (self.strategy_hash, self.forecast_model_fingerprint)):
            raise ValueError("capital opportunity identity is incomplete")
        if any(not math.isfinite(value) for value in (self.minimum_viable_capital, self.requested_risk_fraction, self.expected_net_edge_fraction)):
            raise ValueError("capital opportunity values must be finite")
        if self.minimum_viable_capital <= 0 or not 0 < self.requested_risk_fraction <= 1:
            raise ValueError("capital opportunity requirements are invalid")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("capital opportunity expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CapitalOpportunityPolicy:
    maximum_risk_fraction_per_trade: float = 0.005
    maximum_total_risk_fraction: float = 0.02
    maximum_concurrent_opportunities: int = 6
    base_opportunity_slots: int = 1
    realized_gain_per_extra_slot: float = 500.0

    def __post_init__(self) -> None:
        if not 0 < self.maximum_risk_fraction_per_trade <= self.maximum_total_risk_fraction <= 1:
            raise ValueError("capital opportunity risk policy is invalid")
        if self.maximum_concurrent_opportunities < 1 or not 1 <= self.base_opportunity_slots <= self.maximum_concurrent_opportunities:
            raise ValueError("capital opportunity slot policy is invalid")
        if self.realized_gain_per_extra_slot <= 0:
            raise ValueError("realized gain slot threshold must be positive")


@dataclass(frozen=True, slots=True)
class OpportunityAllocation:
    opportunity_id: str
    risk_fraction: float
    risk_cash: float


@dataclass(frozen=True, slots=True)
class CapitalOpportunityDecision:
    allocations: tuple[OpportunityAllocation, ...]
    conservative_capital: float
    available_slots: int
    daily_goal_forced_trade: bool
    reasons: tuple[str, ...]


def allocate_certified_opportunities(
    capital: SettledCapitalState,
    opportunities: Iterable[CertifiedOpportunity],
    *,
    at: datetime,
    policy: CapitalOpportunityPolicy = CapitalOpportunityPolicy(),
    daily_goal_fraction: float | None = None,
) -> CapitalOpportunityDecision:
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("capital allocation time must be timezone-aware")
    if daily_goal_fraction is not None and (not math.isfinite(daily_goal_fraction) or daily_goal_fraction < 0):
        raise ValueError("daily goal must be finite and nonnegative")
    deployable = capital.conservative_deployable_capital
    growth_slots = int(capital.realized_growth_capital // policy.realized_gain_per_extra_slot)
    slots = min(policy.maximum_concurrent_opportunities, policy.base_opportunity_slots + growth_slots)
    ranked = sorted(opportunities, key=lambda row: (-row.expected_net_edge_fraction, row.opportunity_id))
    remaining_capital = deployable
    used_risk = 0.0
    allocations = []
    reasons: list[str] = []
    for row in ranked:
        allowed_direction = row.clock.long_entries_authorized if row.side is TradeSide.LONG else row.clock.short_entries_authorized
        rejection = None
        if not row.certified:
            rejection = "not_certified"
        elif not row.setup_present:
            rejection = "setup_absent"
        elif row.expires_at <= at:
            rejection = "opportunity_expired"
        elif not allowed_direction:
            rejection = f"market_clock_{row.clock.state.value}"
        elif row.expected_net_edge_fraction <= 0:
            rejection = "nonpositive_net_edge"
        elif row.requested_risk_fraction > policy.maximum_risk_fraction_per_trade:
            rejection = "per_trade_risk_exceeded"
        elif len(allocations) >= slots:
            rejection = "settled_growth_slots_exhausted"
        elif row.minimum_viable_capital > remaining_capital:
            rejection = "insufficient_conservative_capital"
        elif used_risk + row.requested_risk_fraction > policy.maximum_total_risk_fraction + 1e-12:
            rejection = "portfolio_risk_budget_exhausted"
        if rejection:
            reasons.append(f"{row.opportunity_id}:{rejection}")
            continue
        allocations.append(OpportunityAllocation(row.opportunity_id, row.requested_risk_fraction, deployable * row.requested_risk_fraction))
        remaining_capital -= row.minimum_viable_capital
        used_risk += row.requested_risk_fraction
    if not allocations:
        reasons.append("no_qualified_opportunity_wait")
    # A daily/weekly/monthly goal is observational. It cannot create an allocation.
    return CapitalOpportunityDecision(tuple(allocations), deployable, slots, False, tuple(reasons))
