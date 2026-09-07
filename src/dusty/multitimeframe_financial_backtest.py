from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

from .analysis_runtime import AnalysisReplay
from .backtest import (
    BacktestResultV2,
    PriceMark,
    SimulatedTrade,
    simulate_portfolio,
    trade_gross_pnl,
    trade_net_pnl,
)
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
class FinancialReplaySummary:
    gross_pnl: float
    net_pnl: float
    total_costs: float
    ending_equity: float
    max_drawdown_fraction: float
    max_margin_used: float
    trade_count: int

    def __post_init__(self) -> None:
        values = (
            self.gross_pnl,
            self.net_pnl,
            self.total_costs,
            self.ending_equity,
            self.max_drawdown_fraction,
            self.max_margin_used,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("financial replay summary must be finite")
        if self.total_costs < -1e-12 or self.max_drawdown_fraction < 0 or self.max_margin_used < 0:
            raise ValueError("financial replay summary cannot expose negative cost/drawdown/margin")
        if self.trade_count < 0:
            raise ValueError("financial replay summary trade count cannot be negative")


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


def summarize_minimum_lot_financial_replay(
    replay: MinimumLotFinancialReplay,
    economics: InstrumentEconomics,
) -> FinancialReplaySummary:
    """Return tested derived ledger metrics without assuming BacktestResultV2 fields that do not exist."""
    gross = sum(trade_gross_pnl(trade, economics) for trade in replay.simulated_trades)
    net_from_trades = sum(trade_net_pnl(trade, economics) for trade in replay.simulated_trades)
    costs = gross - net_from_trades
    if abs(net_from_trades - replay.backtest.net_pnl) > 1e-8:
        raise ValueError("financial replay trade PnL does not reconcile to ledger net PnL")
    max_margin = max((point.margin_used for point in replay.backtest.ledger), default=0.0)
    return FinancialReplaySummary(
        gross_pnl=gross,
        net_pnl=replay.backtest.net_pnl,
        total_costs=costs,
        ending_equity=replay.backtest.ending_equity,
        max_drawdown_fraction=replay.backtest.max_drawdown_fraction,
        max_margin_used=max_margin,
        trade_count=replay.backtest.trade_count,
    )


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
