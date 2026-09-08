from __future__ import annotations

"""Pure fail-closed runtime envelope for one M165 Demo calibration entry.

A planning artifact authorizes only identity, minimum volume and a stop-distance
shape.  Volatile absolute prices are rebound from a fresh native quote before a
one-shot M187 permit is created.  This module has no MetaTrader5 import and no
broker-write authority.
"""

from dataclasses import dataclass
import math

from .risk import RiskConstitution


@dataclass(frozen=True, slots=True)
class M165RuntimeExecutionEnvelope:
    symbol: str
    volume_lots: float
    tick_size: float
    stop_distance: float
    quote_tolerance_ticks: int
    current_reference_price: float
    stop_price: float
    worst_case_reference_price: float
    maximum_execution_loss_cash: float
    actual_risk_fraction: float
    current_equity: float

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip().upper()
        if not symbol or len(symbol) > 64:
            raise ValueError("runtime symbol required")
        object.__setattr__(self, "symbol", symbol)
        for name in (
            "volume_lots",
            "tick_size",
            "stop_distance",
            "current_reference_price",
            "stop_price",
            "worst_case_reference_price",
            "maximum_execution_loss_cash",
            "actual_risk_fraction",
            "current_equity",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
            object.__setattr__(self, name, value)
        if isinstance(self.quote_tolerance_ticks, bool) or not 1 <= int(self.quote_tolerance_ticks) <= 8:
            raise ValueError("quote_tolerance_ticks out of range")
        object.__setattr__(self, "quote_tolerance_ticks", int(self.quote_tolerance_ticks))
        if self.stop_price >= self.current_reference_price:
            raise ValueError("runtime long stop must remain below current reference")
        if self.worst_case_reference_price < self.current_reference_price:
            raise ValueError("worst-case reference cannot improve the entry")
        if self.actual_risk_fraction > RiskConstitution().normal_trade_risk + 1e-15:
            raise ValueError("runtime risk exceeds Dusty normal-trade risk")

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    retry_authority = False


def build_runtime_execution_envelope(
    *,
    symbol: str,
    volume_lots: float,
    tick_size: float,
    planned_reference_price: float,
    planned_stop_price: float,
    current_reference_price: float,
    current_equity: float,
    worst_case_loss_cash: float,
    discovery_loss_ceiling_cash: float,
    quote_tolerance_ticks: int = 3,
) -> M165RuntimeExecutionEnvelope:
    tick = float(tick_size)
    planned_reference = float(planned_reference_price)
    planned_stop = float(planned_stop_price)
    current = float(current_reference_price)
    equity = float(current_equity)
    worst_loss = float(worst_case_loss_cash)
    discovery_ceiling = float(discovery_loss_ceiling_cash)
    if any(not math.isfinite(value) or value <= 0 for value in (tick, planned_reference, planned_stop, current, equity, worst_loss, discovery_ceiling)):
        raise ValueError("runtime envelope inputs must be finite and positive")
    stop_distance = planned_reference - planned_stop
    if stop_distance <= 0:
        raise ValueError("planned stop distance must be positive")
    if stop_distance + tick * 1e-9 < tick:
        raise ValueError("planned stop distance cannot be below native tick size")
    if isinstance(quote_tolerance_ticks, bool) or not 1 <= int(quote_tolerance_ticks) <= 8:
        raise ValueError("quote_tolerance_ticks out of range")

    # Keep the same native stop distance, but rebase its absolute price to the
    # fresh quote.  The caller is responsible for native tick alignment.
    stop_price = current - stop_distance
    worst_reference = current + int(quote_tolerance_ticks) * tick
    current_normal_ceiling = equity * RiskConstitution().normal_trade_risk
    maximum = min(discovery_ceiling, current_normal_ceiling)
    if worst_loss > maximum + 1e-9:
        raise ValueError("fresh worst-case broker loss exceeds calibration risk ceiling")
    risk_fraction = worst_loss / equity
    return M165RuntimeExecutionEnvelope(
        symbol=symbol,
        volume_lots=volume_lots,
        tick_size=tick,
        stop_distance=stop_distance,
        quote_tolerance_ticks=int(quote_tolerance_ticks),
        current_reference_price=current,
        stop_price=stop_price,
        worst_case_reference_price=worst_reference,
        maximum_execution_loss_cash=worst_loss,
        actual_risk_fraction=risk_fraction,
        current_equity=equity,
    )
