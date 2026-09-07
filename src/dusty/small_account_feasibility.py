from __future__ import annotations

"""M197 small-account feasibility and capital-compression engine.

M197 never invents strategy profitability.  It owns only the capital experiment:
keep the strategy/broker assumptions frozen, start from a successful reference
backtest, reduce starting capital by 25%, and ask the upstream quantitative gate
to rerun the same experiment.  Compression stops at the first failed rerun or
a successful run below the configured capital target.

The lower-level feasibility calculation remains useful for identifying a hard
broker/risk floor.  Neither layer can grant execution or promotion authority.
"""

from collections.abc import Callable
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


class CapitalCompressionStatus(StrEnum):
    BELOW_TARGET_PROVEN = "below_target_proven"
    MINIMUM_BOUND_FOUND = "minimum_bound_found"
    REFERENCE_FAILED = "reference_failed"


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


@dataclass(frozen=True, slots=True)
class CapitalBacktestEvidence:
    """One upstream quantitative rerun at one starting-capital level.

    ``passed`` is supplied by the already-governed A1/A2/A3/robustness policy.
    M197 deliberately does not reinterpret P&L into a pass.
    """

    equity: float
    passed: bool
    evidence_fingerprint: str
    reasons: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.equity) or self.equity <= 0:
            raise ValueError("M197 backtest evidence equity must be finite and positive")
        if not self.evidence_fingerprint.strip():
            raise ValueError("M197 backtest evidence requires immutable evidence identity")


@dataclass(frozen=True, slots=True)
class CapitalCompressionAssessment:
    status: CapitalCompressionStatus
    starting_equity: float
    reduction_fraction: float
    target_equity: float
    evidence: tuple[CapitalBacktestEvidence, ...]
    lowest_passing_equity: float | None
    first_failing_equity: float | None

    @property
    def fingerprint(self) -> str:
        return _digest({
            "protocol": "dusty-m197-capital-compression-v1",
            "status": self.status.value,
            "starting_equity": self.starting_equity,
            "reduction_fraction": self.reduction_fraction,
            "target_equity": self.target_equity,
            "evidence": tuple((row.equity, row.passed, row.evidence_fingerprint, row.reasons) for row in self.evidence),
            "lowest_passing_equity": self.lowest_passing_equity,
            "first_failing_equity": self.first_failing_equity,
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


def capital_compression_equities(
    *,
    starting_equity: float = 100_000.0,
    reduction_fraction: float = 0.25,
    target_equity: float = 100.0,
    max_steps: int = 64,
) -> tuple[float, ...]:
    """Return the exact 100% -> 75% -> ... experiment schedule.

    One value below the target is included so Dusty can prove sub-$100 success
    instead of stopping merely because the next mathematical step crossed $100.
    """
    values = (starting_equity, reduction_fraction, target_equity)
    if any(not math.isfinite(value) for value in values):
        raise ValueError("M197 capital compression parameters must be finite")
    if starting_equity <= 0 or target_equity <= 0:
        raise ValueError("M197 capital compression equities must be positive")
    if not 0.0 < reduction_fraction < 1.0:
        raise ValueError("M197 reduction fraction must be in (0,1)")
    if max_steps < 2:
        raise ValueError("M197 capital compression requires at least two steps")

    multiplier = 1.0 - reduction_fraction
    rows: list[float] = []
    equity = float(starting_equity)
    for _ in range(max_steps):
        rows.append(equity)
        if equity < target_equity:
            return tuple(rows)
        equity *= multiplier
    raise ValueError("M197 capital compression exceeded bounded step count")


def run_capital_compression_campaign(
    evaluate: Callable[[float], CapitalBacktestEvidence],
    *,
    starting_equity: float = 100_000.0,
    reduction_fraction: float = 0.25,
    target_equity: float = 100.0,
    max_steps: int = 64,
) -> CapitalCompressionAssessment:
    """Rerun a frozen strategy until capital fails or sub-target success is proven."""
    schedule = capital_compression_equities(
        starting_equity=starting_equity,
        reduction_fraction=reduction_fraction,
        target_equity=target_equity,
        max_steps=max_steps,
    )
    rows: list[CapitalBacktestEvidence] = []
    lowest_pass: float | None = None
    first_fail: float | None = None

    for expected_equity in schedule:
        row = evaluate(expected_equity)
        if abs(row.equity - expected_equity) > max(1e-9, expected_equity * 1e-12):
            raise ValueError("M197 evaluator returned evidence for the wrong starting equity")
        if any(existing.evidence_fingerprint == row.evidence_fingerprint for existing in rows):
            raise ValueError("M197 capital reruns require distinct immutable evidence identities")
        rows.append(row)

        if not row.passed:
            first_fail = row.equity
            status = CapitalCompressionStatus.REFERENCE_FAILED if len(rows) == 1 else CapitalCompressionStatus.MINIMUM_BOUND_FOUND
            break

        lowest_pass = row.equity
        if row.equity < target_equity:
            status = CapitalCompressionStatus.BELOW_TARGET_PROVEN
            break
    else:  # pragma: no cover - bounded schedule always terminates below target
        raise AssertionError("M197 capital compression schedule did not terminate")

    return CapitalCompressionAssessment(
        status=status,
        starting_equity=starting_equity,
        reduction_fraction=reduction_fraction,
        target_equity=target_equity,
        evidence=tuple(rows),
        lowest_passing_equity=lowest_pass,
        first_failing_equity=first_fail,
    )


def assess_capital_ladder(
    request: SmallAccountFeasibilityRequest,
    equities: tuple[float, ...] | None = None,
) -> tuple[SmallAccountFeasibility, ...]:
    """Evaluate a supplied ladder, or the canonical descending 25% compression schedule."""
    selected = capital_compression_equities() if equities is None else equities
    if not selected:
        raise ValueError("M197 capital ladder cannot be empty")
    rows = []
    for equity in selected:
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
