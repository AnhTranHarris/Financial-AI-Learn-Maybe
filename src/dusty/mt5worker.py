from __future__ import annotations

import importlib
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .mt5lab import MT5TestRequest, MT5TickMode


_MODEL = {
    MT5TickMode.EVERY_TICK: 0,
    MT5TickMode.ONE_MINUTE_OHLC: 1,
    MT5TickMode.OPEN_PRICES: 2,
    MT5TickMode.REAL_TICKS: 4,
}


@dataclass(frozen=True, slots=True)
class MT5BarRequest:
    terminal_path: str
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    chunk_days: int = 7

    def __post_init__(self) -> None:
        if not all((self.terminal_path, self.symbol, self.timeframe)):
            raise ValueError("terminal, symbol, and timeframe are required")
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("bar request start must be timezone-aware")
        if self.end.tzinfo is None or self.end.utcoffset() is None:
            raise ValueError("bar request end must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("bar request end must be after start")
        if self.chunk_days < 1:
            raise ValueError("chunk_days must be positive")


@dataclass(frozen=True, slots=True)
class MT5Bar:
    at: datetime
    open: float
    high: float
    low: float
    close: float
    tick_volume: int
    spread: int
    real_volume: int


class ReadOnlyMT5Worker:
    """Lazy optional MetaTrader5 adapter exposing history only, never broker writes."""

    def __init__(self, module: Any | None = None) -> None:
        self._module = module

    @property
    def broker_write_authorized(self) -> bool:
        return False

    def _mt5(self) -> Any:
        if self._module is None:
            self._module = importlib.import_module("MetaTrader5")
        return self._module

    def stream_bars(self, request: MT5BarRequest) -> Iterator[MT5Bar]:
        mt5 = self._mt5()
        if not mt5.initialize(request.terminal_path):
            error = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
            raise RuntimeError(f"MT5 initialize failed: {error}")
        try:
            timeframe_name = f"TIMEFRAME_{request.timeframe.upper()}"
            if not hasattr(mt5, timeframe_name):
                raise ValueError(f"unsupported MT5 timeframe: {request.timeframe}")
            timeframe = getattr(mt5, timeframe_name)
            cursor = request.start.astimezone(timezone.utc)
            end = request.end.astimezone(timezone.utc)
            step = timedelta(days=request.chunk_days)
            last_epoch: int | None = None
            while cursor < end:
                chunk_end = min(cursor + step, end)
                rows = mt5.copy_rates_range(request.symbol, timeframe, cursor, chunk_end)
                if rows is None:
                    error = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
                    raise RuntimeError(f"MT5 history request failed: {error}")
                for row in rows:
                    epoch = int(_field(row, "time", 0))
                    if last_epoch is not None and epoch <= last_epoch:
                        continue
                    last_epoch = epoch
                    yield MT5Bar(
                        at=datetime.fromtimestamp(epoch, tz=timezone.utc),
                        open=float(_field(row, "open", 1)),
                        high=float(_field(row, "high", 2)),
                        low=float(_field(row, "low", 3)),
                        close=float(_field(row, "close", 4)),
                        tick_volume=int(_field(row, "tick_volume", 5)),
                        spread=int(_field(row, "spread", 6)),
                        real_volume=int(_field(row, "real_volume", 7)),
                    )
                cursor = chunk_end
        finally:
            mt5.shutdown()


def _field(row: Any, name: str, index: int) -> Any:
    try:
        return row[name]
    except (KeyError, TypeError, IndexError, ValueError):
        return row[index]


def render_tester_ini(
    request: MT5TestRequest,
    *,
    expert: str,
    report: str,
    deposit: int = 10_000,
    currency: str = "USD",
    leverage: str = "1:100",
) -> str:
    """Render an official command-line tester config with live trading disabled."""
    if not expert.strip() or not report.strip():
        raise ValueError("expert and report are required")
    if deposit <= 0:
        raise ValueError("deposit must be positive")
    model = _MODEL[request.tick_mode]
    return "\n".join(
        (
            "[Experts]",
            "AllowLiveTrading=0",
            "AllowDllImport=0",
            "Enabled=1",
            "",
            "[Tester]",
            f"Expert={expert}",
            f"Symbol={request.symbol}",
            f"Period={request.timeframe.upper()}",
            f"Model={model}",
            "ExecutionMode=0",
            "Optimization=0",
            f"FromDate={request.start:%Y.%m.%d}",
            f"ToDate={request.end:%Y.%m.%d}",
            f"Deposit={deposit}",
            f"Currency={currency.upper()}",
            f"Leverage={leverage}",
            f"Report={report}",
            "ReplaceReport=1",
            "UseLocal=1",
            "UseRemote=0",
            "UseCloud=0",
            "Visual=0",
            "ShutdownTerminal=1",
            "",
        )
    )


def tester_command(request: MT5TestRequest, config_path: str | Path) -> tuple[str, str]:
    return request.terminal_path, f"/config:{Path(config_path)}"


def launch_tester(
    request: MT5TestRequest,
    config_path: str | Path,
    *,
    timeout_seconds: float,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
) -> int:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    completed = runner(
        tester_command(request, config_path),
        check=False,
        timeout=timeout_seconds,
        capture_output=True,
    )
    return int(completed.returncode)
