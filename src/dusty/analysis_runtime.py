from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from .chart_intelligence import AnalysisSnapshot, MarketAnalysisGraph
from .core import HealthState
from .experience import TradeSide
from .strategy_v3 import (
    PositionView,
    StrategySpecV3,
    TradeDirective,
    TradeLifecycleDecision,
    TradeLifecycleRequest,
    reason_trade_lifecycle,
)


@dataclass(frozen=True, slots=True)
class AnalysisFrame:
    snapshot: AnalysisSnapshot
    execution_price: float
    health: HealthState = HealthState.HEALTHY
    event_blocked: bool = False
    spread_acceptable: bool = True
    governance_approved: bool = True
    tools_valid: bool = True

    def __post_init__(self) -> None:
        if not math.isfinite(self.execution_price) or self.execution_price <= 0:
            raise ValueError("analysis execution price must be finite and positive")


@dataclass(frozen=True, slots=True)
class AnalysisDecisionTrace:
    at: datetime
    outputs: tuple[tuple[str, bool], ...]
    decision: TradeLifecycleDecision
    position_before: PositionView | None
    position_after: PositionView | None


@dataclass(frozen=True, slots=True)
class AnalysisReplayTrade:
    side: TradeSide
    entry_at: datetime
    exit_at: datetime
    entry_price: float
    exit_price: float
    initial_volume: float
    exit_reason: str


@dataclass(frozen=True, slots=True)
class AnalysisReplay:
    strategy_hash: str
    graph_hash: str
    traces: tuple[AnalysisDecisionTrace, ...]
    trades: tuple[AnalysisReplayTrade, ...]
    open_position: PositionView | None


def replay_analysis_strategy(
    graph: MarketAnalysisGraph,
    strategy: StrategySpecV3,
    frames: Iterable[AnalysisFrame],
    *,
    initial_volume: float = 1.0,
) -> AnalysisReplay:
    """Replay the exact analysis graph and lifecycle semantics before financial/native testing.

    This is a semantic reference replay, not a substitute for the realistic account ledger or MT5
    Strategy Tester. Entry uses the explicit execution-time price carried by each frame; the graph
    snapshot remains completed, point-in-time evidence.
    """
    if graph.fingerprint != strategy.analysis_graph_hash:
        raise ValueError("strategy and analysis graph identity do not match")
    if graph.tool_fingerprints != strategy.tool_fingerprints:
        raise ValueError("strategy and analysis graph tool dependencies do not match")
    if not math.isfinite(initial_volume) or initial_volume <= 0:
        raise ValueError("analysis replay volume must be finite and positive")
    rows = tuple(frames)
    if tuple(sorted(rows, key=lambda row: row.snapshot.at)) != rows:
        raise ValueError("analysis replay frames must be chronological")
    previous: AnalysisSnapshot | None = None
    position: PositionView | None = None
    entry_at = None
    entry_price = 0.0
    entry_volume = 0.0
    traces: list[AnalysisDecisionTrace] = []
    trades: list[AnalysisReplayTrade] = []
    for frame in rows:
        evaluated = graph.evaluate(frame.snapshot, previous=previous)
        if any(not isinstance(value, bool) for value in evaluated.values()):
            raise ValueError("strategy lifecycle outputs must all be boolean")
        before = position
        decision = reason_trade_lifecycle(
            strategy,
            TradeLifecycleRequest.of(
                frame.snapshot.at,
                {key: bool(value) for key, value in evaluated.items()},
                frame.health,
                position=position,
                event_blocked=frame.event_blocked,
                spread_acceptable=frame.spread_acceptable,
                governance_approved=frame.governance_approved,
                tools_valid=frame.tools_valid,
            ),
        )
        if decision.directive in {TradeDirective.ENTER_LONG, TradeDirective.ENTER_SHORT}:
            side = TradeSide.LONG if decision.directive is TradeDirective.ENTER_LONG else TradeSide.SHORT
            stop = _initial_stop(strategy.protection.stop_rule, side, frame.execution_price, dict(frame.snapshot.values))
            position = PositionView(side, frame.execution_price, stop, initial_volume, 0)
            entry_at = frame.snapshot.at
            entry_price = frame.execution_price
            entry_volume = initial_volume
        elif position is not None and decision.directive is TradeDirective.PARTIAL_EXIT:
            remaining = position.volume * (1 - decision.partial_fraction)
            position = PositionView(position.side, position.entry_price, position.current_stop, remaining, position.held_steps + 1)
        elif position is not None and decision.directive is TradeDirective.TIGHTEN_PROTECTION:
            values = dict(frame.snapshot.values)
            proposed = values.get("__proposed_stop__")
            if isinstance(proposed, bool) or not isinstance(proposed, (int, float)):
                raise ValueError("protection-tightening decision requires numeric __proposed_stop__")
            stop = float(proposed)
            if position.side is TradeSide.LONG and stop < position.current_stop:
                raise ValueError("analysis replay cannot widen long stop")
            if position.side is TradeSide.SHORT and stop > position.current_stop:
                raise ValueError("analysis replay cannot widen short stop")
            position = PositionView(position.side, position.entry_price, stop, position.volume, position.held_steps + 1)
        elif position is not None and decision.directive is TradeDirective.EXIT:
            if entry_at is None:
                raise AssertionError("analysis replay position lacks entry identity")
            trades.append(
                AnalysisReplayTrade(
                    position.side,
                    entry_at,
                    frame.snapshot.at,
                    entry_price,
                    frame.execution_price,
                    entry_volume,
                    decision.reasons[0],
                )
            )
            position = None
            entry_at = None
        elif position is not None:
            position = PositionView(
                position.side,
                position.entry_price,
                position.current_stop,
                position.volume,
                position.held_steps + 1,
            )
        traces.append(
            AnalysisDecisionTrace(
                frame.snapshot.at,
                tuple(sorted((key, bool(value)) for key, value in evaluated.items())),
                decision,
                before,
                position,
            )
        )
        previous = frame.snapshot
    return AnalysisReplay(strategy.strategy_hash, graph.fingerprint, tuple(traces), tuple(trades), position)


def _initial_stop(rule: str, side: TradeSide, entry: float, values: dict[str, float | bool]) -> float:
    raw = rule.strip().lower()
    if ":" not in raw:
        raise ValueError("analysis stop must use typed kind:value form")
    kind, value_text = raw.split(":", 1)
    value = float(value_text)
    if not math.isfinite(value) or value <= 0:
        raise ValueError("analysis stop value must be finite and positive")
    if kind == "price":
        stop = value
    elif kind == "pct":
        distance = entry * value
        stop = entry - distance if side is TradeSide.LONG else entry + distance
    elif kind == "atr":
        atr = values.get("atr")
        if isinstance(atr, bool) or not isinstance(atr, (int, float)) or not math.isfinite(float(atr)) or float(atr) <= 0:
            raise ValueError("ATR stop requires positive point-in-time ATR")
        distance = float(atr) * value
        stop = entry - distance if side is TradeSide.LONG else entry + distance
    else:
        raise ValueError(f"unsupported analysis stop rule: {kind}")
    valid = stop < entry if side is TradeSide.LONG else stop > entry
    if not valid or stop <= 0:
        raise ValueError("analysis stop is on wrong side of entry")
    return stop
