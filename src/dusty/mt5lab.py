from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Protocol


class MT5TickMode(StrEnum):
    OPEN_PRICES = "open_prices"
    ONE_MINUTE_OHLC = "one_minute_ohlc"
    EVERY_TICK = "every_tick"
    REAL_TICKS = "real_ticks"


class MT5Fidelity(IntEnum):
    OPEN_PRICES = 0
    ONE_MINUTE_OHLC = 1
    EVERY_TICK = 2
    REAL_TICKS = 3


FIDELITY = {
    MT5TickMode.OPEN_PRICES: MT5Fidelity.OPEN_PRICES,
    MT5TickMode.ONE_MINUTE_OHLC: MT5Fidelity.ONE_MINUTE_OHLC,
    MT5TickMode.EVERY_TICK: MT5Fidelity.EVERY_TICK,
    MT5TickMode.REAL_TICKS: MT5Fidelity.REAL_TICKS,
}


@dataclass(frozen=True, slots=True)
class MT5TestRequest:
    request_id: str
    terminal_path: str
    strategy_hash: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    tick_mode: MT5TickMode

    def __post_init__(self) -> None:
        if not all((self.request_id, self.terminal_path, self.strategy_hash, self.symbol, self.timeframe)):
            raise ValueError("MT5 test request identity, terminal, strategy, symbol and timeframe are required")
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("MT5 test start must be timezone-aware")
        if self.end.tzinfo is None or self.end.utcoffset() is None:
            raise ValueError("MT5 test end must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("MT5 test end must be after start")

    @property
    def broker_write_authorized(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class MT5TestResult:
    request_id: str
    strategy_hash: str
    terminal_id: str
    tick_mode: MT5TickMode
    trade_count: int
    net_return: float
    max_drawdown: float
    provenance: str

    def __post_init__(self) -> None:
        if not all((self.request_id, self.strategy_hash, self.terminal_id, self.provenance)):
            raise ValueError("MT5 result provenance is required")
        if self.trade_count < 0:
            raise ValueError("trade count cannot be negative")
        if self.max_drawdown < 0:
            raise ValueError("drawdown is expressed as a non-negative magnitude")


class MT5TesterPort(Protocol):
    """Replaceable Strategy Tester boundary. Implementations may simulate trades but never write to broker execution."""

    def run_test(self, request: MT5TestRequest) -> MT5TestResult:
        ...


def next_tick_mode(mode: MT5TickMode) -> MT5TickMode | None:
    ordered = (
        MT5TickMode.OPEN_PRICES,
        MT5TickMode.ONE_MINUTE_OHLC,
        MT5TickMode.EVERY_TICK,
        MT5TickMode.REAL_TICKS,
    )
    index = ordered.index(mode)
    return ordered[index + 1] if index + 1 < len(ordered) else None


def fidelity_at_least(actual: MT5TickMode, required: MT5TickMode) -> bool:
    return FIDELITY[actual] >= FIDELITY[required]
