from __future__ import annotations

"""M197 small-account feasibility engine.

This layer answers one question only: can a broker/symbol/strategy stop be
expressed at a given equity without relaxing Dusty's Risk Constitution?
It never increases percentage risk, never rounds volume upward, and never
grants trading authority.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math

from .capital import PositionSizingRequest, minimum_viable_capital, size_position
from .experience import TradeSide
from .markets import InstrumentEconomics
from .risk import RiskConstitution


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


class CapitalState(StrEnum):
    NORMAL = "normal"
    COMPRESSED = "compressed"
    MICRO_CAPITAL = "micro_capital"
    CAPITAL_INSUFFICIENT = "capital_insufficient"


class FeasibilityDecision(StrEnum):
    TRADEABLE = "tradeable"
    NO_TRADE = "no_trade"


@dataclass(frozen=True, slots=True)
class SmallAccountFeasibilityRequest:
    equity: float
    side: TradeSide
    entry_price: float
    strategy_stop_price: float
    economics: InstrumentEconomics
    spread_price: float = 0.0
    expected_slippage_price: float = 0.0
    commission_per_lot: float | None = None
    margin_for_minimum_lot: float | None = None
    constitution: RiskConstitution = RiskConstitution()
    risk_fraction: float | None = None

    def __post_init__(self) -> None:
        values = (self.equity, self.entry_price, self.strategy_stop_price, self.spread_price, self.expected_slippage_price)
        if self.margin_for_minimum_lot is not None:
            values += (self.margin_for_minimum_lot,)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("M197 feasibility inputs must be finite")
        if self.equity <= 0 or self.entry_price <= 0 or self.strategy_stop_price <= 0:
            raise ValueError("M197 equity and prices must be positive")
        if self.spread_price < 0 or self.expected_slippage_price < 0:
            raise ValueError("M197 spread/slippage cannot be negative")
        if self.margin_for_minimum_lot is not None and self.margin_for_minimum_lot < 0:
            raise ValueError("M197 margin cannot be negative")
        if self.side is TradeSide.LONG and self.strategy_stop_price >= self.entry_price:
            raise ValueError("M197 long stop must be below entry")
        if self.side is TradeSide.SHORT and self.strategy_stop_price <= self.entry_price:
            raise ValueError("M197 short stop must be above entry")
        risk = self.constitution.normal_trade_risk if self.risk_fraction is None else self.risk_fraction
        if not math.isfinite(risk) or not 0.0 < risk <= self.constitution.hard_max_trade_risk:
            raise ValueError("M197 risk fraction must remain within the Risk Constitution")

    @property
    def approved_risk_fraction(self) -> float:
        return self.constitution.normal_trade_risk if self.risk_fraction is None else float(self.risk_fraction)


@dataclass(frozen=True, slots=True)
class SmallAccountFeasibility:
    decision: FeasibilityDecision
    capital_state: CapitalState
    equity: float
    approved_risk_fraction: float
    allowed_loss: float
    required_stop_price: float
    required_stop_distance: float
    broker_stop_floor_applied: bool
    broker_minimum_volume: float
    approved_volume: float
    expected_loss: float
    effective_risk_fraction: float
    risk_minimum_capital: float
    margin_minimum_capital: float | None
    minimum_compliant_capital: float
    margin_for_minimum_lot: float | None
    margin_fraction_if_minimum_lot: float | None
    reasons: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest({
            "protocol": "dusty-m197-small-account-feasibility-v1",
            "decision": self.decision.value,
            "capital_state": self.capital_state.value,
            "equity": self.equity,
            "risk": self.approved_risk_fraction,
            "allowed_loss": self.allowed_loss,
            "required_stop_price": self.required_stop_price,
            "required_stop_distance": self.required_stop_distance,
            "broker_stop_floor_applied": self.broker_stop_floor_applied,
            "broker_minimum_volume": self.broker_minimum_volume,
            "approved_volume": self.approved_volume,
            "expected_loss": self.expected_loss,
            "effective_risk_fraction": self.effective_risk_fraction,
            "risk_minimum_capital": self.risk_minimum_capital,
            "margin_minimum_capital": self.margin_minimum_capital,
            "minimum_compliant_capital": self.minimum_compliant_capital,
            "margin_for_minimum_lot": self.margin_for_minimum_lot,
            "margin_fraction_if_minimum_lot": self.margin_fraction_if_minimum_lot,
            "reasons": self.reasons,
        })

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False


def _required_stop(request: SmallAccountFeasibilityRequest) -> tuple[float, float, bool]:
    strategy_distance = abs(request.entry_price - request.strategy_stop_price)
    economics = request.economics
    broker_floor = economics.stop_level_points * economics.point_size
    required_distance = max(strategy_distance, broker_floor)
    applied = required_distance > strategy_distance + 1e-15
    stop = request.entry_price - required_distance if request.side is TradeSide.LONG else request.entry_price + required_distance
    if stop <= 0:
        raise ValueError("M197 broker stop floor produced non-positive stop")
    return stop, required_distance, applied


def _capital_state(equity: float, minimum: float) -> CapitalState:
    if equity + 1e-12 < minimum:
        return CapitalState.CAPITAL_INSUFFICIENT
    if equity <= 250.0:
        return CapitalState.MICRO_CAPITAL
    if equity <= 1000.0:
        return CapitalState.COMPRESSED
    return CapitalState.NORMAL


def assess_small_account_feasibility(request: SmallAccountFeasibilityRequest) -> SmallAccountFeasibility:
    stop, distance, floor_applied = _required_stop(request)
    risk = request.approved_risk_fraction
    sizing_request = PositionSizingRequest(
        equity=request.equity,
        risk_fraction=risk,
        entry_price=request.entry_price,
        stop_price=stop,
        economics=request.economics,
        spread_price=request.spread_price,
        expected_slippage_price=request.expected_slippage_price,
        commission_per_lot=request.commission_per_lot,
    )
    sizing = size_position(sizing_request)
    risk_minimum = minimum_viable_capital(sizing_request, risk_fraction=risk)

    margin_minimum: float | None = None
    margin_fraction: float | None = None
    reasons = list(sizing.reasons)
    if floor_applied:
        reasons.append("broker_minimum_stop_floor_applied")

    if request.margin_for_minimum_lot is not None:
        margin_fraction = request.margin_for_minimum_lot / request.equity
        margin_minimum = request.margin_for_minimum_lot / request.constitution.margin_hard
        if margin_fraction > request.constitution.margin_hard + 1e-12:
            reasons.append("broker_minimum_volume_exceeds_margin_budget")

    minimum = max(risk_minimum, margin_minimum or 0.0)
    feasible = sizing.feasible and request.equity + 1e-12 >= minimum
    if not feasible and not reasons:
        reasons.append("capital_below_minimum_compliant_capital")

    return SmallAccountFeasibility(
        FeasibilityDecision.TRADEABLE if feasible else FeasibilityDecision.NO_TRADE,
        _capital_state(request.equity, minimum),
        request.equity,
        risk,
        sizing.allowed_loss,
        stop,
        distance,
        floor_applied,
        request.economics.volume_min,
        sizing.approved_volume if feasible else 0.0,
        sizing.expected_loss if feasible else 0.0,
        sizing.effective_risk_fraction if feasible else 0.0,
        risk_minimum,
        margin_minimum,
        minimum,
        request.margin_for_minimum_lot,
        margin_fraction,
        tuple(dict.fromkeys(reasons)),
    )


def assess_capital_ladder(
    request: SmallAccountFeasibilityRequest,
    equities: tuple[float, ...] = (100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0, 25000.0),
) -> tuple[SmallAccountFeasibility, ...]:
    if not equities:
        raise ValueError("M197 capital ladder cannot be empty")
    rows = []
    for equity in equities:
        if not math.isfinite(equity) or equity <= 0:
            raise ValueError("M197 capital ladder equities must be finite and positive")
        rows.append(assess_small_account_feasibility(SmallAccountFeasibilityRequest(
            equity=equity,
            side=request.side,
            entry_price=request.entry_price,
            strategy_stop_price=request.strategy_stop_price,
            economics=request.economics,
            spread_price=request.spread_price,
            expected_slippage_price=request.expected_slippage_price,
            commission_per_lot=request.commission_per_lot,
            margin_for_minimum_lot=request.margin_for_minimum_lot,
            constitution=request.constitution,
            risk_fraction=request.risk_fraction,
        )))
    return tuple(rows)
