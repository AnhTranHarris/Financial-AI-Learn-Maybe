from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .analysis_runtime import AnalysisReplay
from .backtest import BacktestResultV2, PriceMark, SimulatedTrade, simulate_portfolio
from .markets import InstrumentEconomics


@dataclass(frozen=True, slots=True)
class ResearchFriction:
    """Explicit per-lot research friction; no broker cost is silently invented."""

    entry_cost_per_lot: float = 0.0
    exit_cost_per_lot: float = 0.0
    swap_cost_per_lot: float = 0.0
    basis: str = "explicit_unverified_research_friction"

    def __post_init__(self) -> None:
        values = (self.entry_cost_per_lot, self.exit_cost_per_lot, self.swap_cost_per_lot)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("research friction must be finite and nonnegative")
        if not self.basis.strip():
            raise ValueError("research friction requires an explicit basis")


@dataclass(frozen=True, slots=True)
class MinimumLotFinancialReplay:
    strategy_hash: str
    symbol: str
    volume: float
    friction: ResearchFriction
    simulated_trades: tuple[SimulatedTrade, ...]
    backtest: BacktestResultV2

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def run_minimum_lot_financial_replay(
    replay: AnalysisReplay,
    marks: Iterable[PriceMark],
    *,
    symbol: str,
    economics: InstrumentEconomics,
    starting_equity: float,
    friction: ResearchFriction = ResearchFriction(),
) -> MinimumLotFinancialReplay:
    """Price a completed Strategy V3 semantic replay through Dusty's existing A1 ledger.

    This bridge deliberately does not generate decisions, model fills, or create a second account
    simulator. It maps already-closed ``AnalysisReplayTrade`` rows to the established
    ``simulate_portfolio`` ledger at the broker minimum lot. Exact native fill parity remains a
    later/native boundary.
    """
    symbol_norm = symbol.strip().upper()
    if not symbol_norm:
        raise ValueError("financial replay requires a symbol")
    if not math.isfinite(starting_equity) or starting_equity <= 0:
        raise ValueError("financial replay starting equity must be finite and positive")
    if replay.open_position is not None:
        raise ValueError("financial replay requires a flat completed semantic replay")

    volume = economics.volume_min
    rows: list[SimulatedTrade] = []
    for index, trade in enumerate(replay.trades):
        if trade.exit_at <= trade.entry_at:
            raise ValueError("analysis replay contains nonchronological trade")
        rows.append(
            SimulatedTrade(
                trade_id=f"a1-{index:06d}",
                symbol=symbol_norm,
                side=trade.side,
                entry_at=trade.entry_at,
                exit_at=trade.exit_at,
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                volume=volume,
                entry_cost=friction.entry_cost_per_lot * volume,
                exit_cost=friction.exit_cost_per_lot * volume,
                swap_cost=friction.swap_cost_per_lot * volume,
            )
        )

    mark_rows = tuple(marks)
    if any(mark.symbol.strip().upper() != symbol_norm for mark in mark_rows):
        raise ValueError("financial replay marks cannot mix symbols")

    result = simulate_portfolio(
        rows,
        mark_rows,
        {symbol_norm: economics},
        starting_equity=starting_equity,
    )
    return MinimumLotFinancialReplay(
        strategy_hash=replay.strategy_hash,
        symbol=symbol_norm,
        volume=volume,
        friction=friction,
        simulated_trades=tuple(rows),
        backtest=result,
    )
