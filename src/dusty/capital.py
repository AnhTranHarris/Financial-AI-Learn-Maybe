from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .markets import InstrumentEconomics


class SizingMode(StrEnum):
    MINIMUM_LOT_STRATEGY_TEST = "minimum_lot_strategy_test"
    GROWTH_RISK = "growth_risk"


@dataclass(frozen=True, slots=True)
class PositionSizingRequest:
    equity: float
    risk_fraction: float
    entry_price: float
    stop_price: float
    economics: InstrumentEconomics
    spread_price: float = 0.0
    expected_slippage_price: float = 0.0
    commission_per_lot: float | None = None

    def __post_init__(self) -> None:
        if self.equity <= 0 or self.entry_price <= 0 or self.stop_price <= 0:
            raise ValueError("equity and prices must be positive")
        if not 0.0 < self.risk_fraction <= 1.0:
            raise ValueError("risk fraction must be in (0,1]")
        if self.entry_price == self.stop_price:
            raise ValueError("stop must differ from entry")
        if self.spread_price < 0 or self.expected_slippage_price < 0:
            raise ValueError("spread and slippage cannot be negative")
        if self.commission_per_lot is not None and self.commission_per_lot < 0:
            raise ValueError("commission cannot be negative")


@dataclass(frozen=True, slots=True)
class PositionSizingResult:
    mode: SizingMode
    feasible: bool
    allowed_loss: float
    loss_per_lot: float
    raw_volume: float
    approved_volume: float
    expected_loss: float
    effective_risk_fraction: float
    reasons: tuple[str, ...] = ()


def loss_per_lot(request: PositionSizingRequest) -> float:
    """Worst planned loss per lot including stop distance and explicit trading friction."""
    economics = request.economics
    adverse_price = (
        abs(request.entry_price - request.stop_price)
        + request.spread_price
        + request.expected_slippage_price
    )
    movement_cost = adverse_price / economics.tick_size * economics.tick_value
    commission = (
        economics.commission_per_lot
        if request.commission_per_lot is None
        else request.commission_per_lot
    )
    total = movement_cost + commission
    if total <= 0:
        raise ValueError("loss per lot must be positive")
    return total


def size_position(
    request: PositionSizingRequest,
    *,
    mode: SizingMode = SizingMode.GROWTH_RISK,
) -> PositionSizingResult:
    """Stop first, size second. Volume is never rounded upward to manufacture a trade."""
    per_lot = loss_per_lot(request)
    allowed = request.equity * request.risk_fraction
    if mode is SizingMode.MINIMUM_LOT_STRATEGY_TEST:
        volume = request.economics.volume_min
        expected = per_lot * volume
        return PositionSizingResult(
            mode=mode,
            feasible=True,
            allowed_loss=allowed,
            loss_per_lot=per_lot,
            raw_volume=volume,
            approved_volume=volume,
            expected_loss=expected,
            effective_risk_fraction=expected / request.equity,
            reasons=(),
        )

    raw = allowed / per_lot
    volume = request.economics.normalize_volume_down(raw)
    if volume <= 0:
        minimum_loss = per_lot * request.economics.volume_min
        return PositionSizingResult(
            mode=mode,
            feasible=False,
            allowed_loss=allowed,
            loss_per_lot=per_lot,
            raw_volume=raw,
            approved_volume=0.0,
            expected_loss=0.0,
            effective_risk_fraction=0.0,
            reasons=(
                "broker_minimum_volume_exceeds_risk_budget",
                f"minimum_loss:{minimum_loss:.12g}",
            ),
        )
    expected = per_lot * volume
    if expected > allowed + 1e-9:
        raise AssertionError("volume normalization increased risk above budget")
    return PositionSizingResult(
        mode=mode,
        feasible=True,
        allowed_loss=allowed,
        loss_per_lot=per_lot,
        raw_volume=raw,
        approved_volume=volume,
        expected_loss=expected,
        effective_risk_fraction=expected / request.equity,
        reasons=(),
    )


def minimum_viable_capital(
    request: PositionSizingRequest,
    *,
    risk_fraction: float | None = None,
) -> float:
    """Equity required for the broker minimum lot to fit the approved percentage risk."""
    risk = request.risk_fraction if risk_fraction is None else risk_fraction
    if not 0.0 < risk <= 1.0:
        raise ValueError("risk fraction must be in (0,1]")
    minimum_loss = loss_per_lot(request) * request.economics.volume_min
    return minimum_loss / risk
