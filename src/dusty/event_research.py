from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable

from .scenario import ScenarioState


class TradingSession(StrEnum):
    ASIA = "asia"
    LONDON = "london"
    NEW_YORK = "new_york"
    OTHER = "other"


class LiquidityState(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    UNKNOWN = "unknown"


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


@dataclass(frozen=True, slots=True)
class MarketReaction:
    event_key: str
    symbol: str
    minutes_from_event: int
    return_value: float
    spread_bps: float
    volume_proxy: float
    session: TradingSession
    liquidity: LiquidityState
    interval_start_minute: int | None = None
    event_at: datetime | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.event_key.strip() or not self.symbol.strip():
            raise ValueError("market reaction requires event and symbol")
        if self.minutes_from_event < 0:
            raise ValueError("market reaction cannot precede its event")
        if self.spread_bps < 0 or self.volume_proxy < 0:
            raise ValueError("spread and volume proxy cannot be negative")
        if self.interval_start_minute is not None:
            if self.interval_start_minute < 0 or self.interval_start_minute > self.minutes_from_event:
                raise ValueError("invalid reaction interval")
        if (self.event_at is None) != (self.observed_at is None):
            raise ValueError("event_at and observed_at must be supplied together")
        if self.event_at is not None and self.observed_at is not None:
            event_at = _aware(self.event_at, "event_at")
            observed_at = _aware(self.observed_at, "observed_at")
            if observed_at < event_at:
                raise ValueError("reaction observation cannot precede event")
            elapsed = int((observed_at - event_at).total_seconds() // 60)
            if elapsed != self.minutes_from_event:
                raise ValueError("minutes_from_event disagrees with timestamps")


@dataclass(frozen=True, slots=True)
class ReactionWindowStats:
    max_minutes: int
    sample_count: int
    mean_return: float
    positive_rate: float
    mean_spread_bps: float
    mean_volume_proxy: float


def summarize_reaction_windows(
    observations: Iterable[MarketReaction],
    *,
    windows: tuple[int, ...] = (5, 15, 60, 240),
) -> tuple[ReactionWindowStats, ...]:
    collected = tuple(observations)
    if not collected:
        return ()
    keys = {item.event_key for item in collected}
    symbols = {item.symbol for item in collected}
    if len(keys) != 1 or len(symbols) != 1:
        raise ValueError("reaction summary must refer to one event and symbol")
    result = []
    for window in sorted(set(windows)):
        if window < 0:
            raise ValueError("reaction windows cannot be negative")
        rows = [item for item in collected if 0 <= item.minutes_from_event <= window]
        if not rows:
            continue
        count = len(rows)
        result.append(
            ReactionWindowStats(
                max_minutes=window,
                sample_count=count,
                mean_return=sum(item.return_value for item in rows) / count,
                positive_rate=sum(item.return_value > 0 for item in rows) / count,
                mean_spread_bps=sum(item.spread_bps for item in rows) / count,
                mean_volume_proxy=sum(item.volume_proxy for item in rows) / count,
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class SessionRepricingAssessment:
    sufficient: bool
    low_liquidity_return: float
    high_liquidity_return: float
    continuation: bool
    reversal: bool
    reasons: tuple[str, ...] = ()


def _reaction_return(rows: list[MarketReaction]) -> float:
    """Aggregate explicit non-overlapping intervals, otherwise use mean point/cumulative estimate.

    Older M51 observations did not encode interval starts, so summing them could double count
    cumulative horizons. New observations can carry interval_start_minute and are then safely
    additive only when intervals do not overlap.
    """
    if not rows:
        return 0.0
    if not all(item.interval_start_minute is not None for item in rows):
        return sum(item.return_value for item in rows) / len(rows)
    ordered = sorted(rows, key=lambda item: (int(item.interval_start_minute), item.minutes_from_event))
    previous_end = -1
    total = 0.0
    for item in ordered:
        start = int(item.interval_start_minute)
        if start < previous_end:
            raise ValueError("reaction intervals overlap and would double-count return")
        previous_end = item.minutes_from_event
        total += item.return_value
    return total


def assess_session_repricing(observations: Iterable[MarketReaction]) -> SessionRepricingAssessment:
    """Research whether thin-session movement persisted or reversed when higher liquidity returned."""
    rows = tuple(observations)
    if rows:
        keys = {item.event_key for item in rows}
        symbols = {item.symbol for item in rows}
        if len(keys) != 1 or len(symbols) != 1:
            raise ValueError("session repricing must refer to one event and symbol")
    low = [item for item in rows if item.liquidity is LiquidityState.LOW]
    high = [item for item in rows if item.liquidity is LiquidityState.HIGH]
    reasons = []
    if not low:
        reasons.append("no_low_liquidity_observation")
    if not high:
        reasons.append("no_high_liquidity_observation")
    if reasons:
        return SessionRepricingAssessment(False, 0.0, 0.0, False, False, tuple(reasons))
    low_return = _reaction_return(low)
    high_return = _reaction_return(high)
    continuation = low_return != 0.0 and high_return != 0.0 and (low_return > 0) == (high_return > 0)
    reversal = low_return != 0.0 and high_return != 0.0 and (low_return > 0) != (high_return > 0)
    return SessionRepricingAssessment(True, low_return, high_return, continuation, reversal)


@dataclass(frozen=True, slots=True)
class StrategyEventObservation:
    strategy_hash: str
    event_class: str
    scenario_state: ScenarioState
    session: TradingSession
    return_value: float

    def __post_init__(self) -> None:
        if not self.strategy_hash.strip() or not self.event_class.strip():
            raise ValueError("strategy-event observation requires strategy and event class")


@dataclass(frozen=True, slots=True)
class StrategyEventStats:
    event_class: str
    scenario_state: ScenarioState
    session: TradingSession
    sample_count: int
    mean_return: float
    hit_rate: float


def summarize_strategy_event_interactions(
    observations: Iterable[StrategyEventObservation],
    *,
    max_cells: int = 64,
) -> tuple[StrategyEventStats, ...]:
    if max_cells < 1:
        raise ValueError("max_cells must be positive")
    strategy_hash: str | None = None
    totals: dict[tuple[str, ScenarioState, TradingSession], list[float | int]] = {}
    for item in observations:
        if strategy_hash is None:
            strategy_hash = item.strategy_hash
        elif item.strategy_hash != strategy_hash:
            raise ValueError("interaction summary must refer to one strategy")
        key = (item.event_class.strip().lower(), item.scenario_state, item.session)
        if key not in totals and len(totals) >= max_cells:
            raise ValueError("strategy-event cardinality budget exceeded")
        state = totals.setdefault(key, [0, 0.0, 0])
        state[0] = int(state[0]) + 1
        state[1] = float(state[1]) + item.return_value
        state[2] = int(state[2]) + int(item.return_value > 0)
    result = []
    for (event_class, scenario_state, session), state in sorted(
        totals.items(), key=lambda item: (item[0][0], item[0][1].value, item[0][2].value)
    ):
        count = int(state[0])
        result.append(
            StrategyEventStats(
                event_class,
                scenario_state,
                session,
                count,
                float(state[1]) / count,
                int(state[2]) / count,
            )
        )
    return tuple(result)
