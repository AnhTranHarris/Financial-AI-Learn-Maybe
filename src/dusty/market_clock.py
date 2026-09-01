from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import IntEnum, StrEnum
from hashlib import sha256


MARKET_CLOSED_RETCODE = 10018


class SessionKind(StrEnum):
    TRADE = "trade"
    QUOTE = "quote"


class MarketClockState(StrEnum):
    UNKNOWN = "unknown"
    SCHEDULED_CLOSED = "scheduled_closed"
    PRE_OPEN = "pre_open"
    OPEN = "open"
    CLOSING_SOON = "closing_soon"
    SESSION_BREAK = "session_break"
    BROKER_MAINTENANCE = "broker_maintenance"
    TRADE_RESTRICTED = "trade_restricted"
    UNEXPECTED_STALE_MARKET = "unexpected_stale_market"
    HALTED = "halted"


class SymbolTradeMode(IntEnum):
    DISABLED = 0
    LONG_ONLY = 1
    SHORT_ONLY = 2
    CLOSE_ONLY = 3
    FULL = 4


@dataclass(frozen=True, slots=True)
class WeeklySession:
    kind: SessionKind
    weekday: int
    session_index: int
    start_second: int
    end_second: int

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6 or self.session_index < 0:
            raise ValueError("market session weekday/index is invalid")
        if not 0 <= self.start_second < 86_400 or not 0 <= self.end_second <= 86_400:
            raise ValueError("market session seconds must lie within one day")
        # MT5 can encode a 24-hour session with identical start/end times.


@dataclass(frozen=True, slots=True)
class BrokerMarketSchedule:
    broker: str
    server: str
    symbol: str
    captured_at: datetime
    server_utc_offset_seconds: int
    sessions: tuple[WeeklySession, ...]
    closed_dates: tuple[date, ...] = ()

    def __post_init__(self) -> None:
        if not self.broker.strip() or not self.server.strip() or not self.symbol.strip():
            raise ValueError("broker market schedule identity is incomplete")
        _aware(self.captured_at, "market schedule capture time")
        if abs(self.server_utc_offset_seconds) > 18 * 3600:
            raise ValueError("broker server UTC offset is invalid")
        if not self.sessions:
            raise ValueError("broker market schedule requires sessions")
        if not any(row.kind is SessionKind.TRADE for row in self.sessions):
            raise ValueError("broker market schedule requires at least one trade session")
        identities = {(row.kind, row.weekday, row.session_index) for row in self.sessions}
        if len(identities) != len(self.sessions):
            raise ValueError("broker market sessions must be unique")
        if len(set(self.closed_dates)) != len(self.closed_dates):
            raise ValueError("broker closed dates must be unique")

    @property
    def fingerprint(self) -> str:
        payload = {
            "broker": self.broker,
            "server": self.server,
            "symbol": self.symbol.upper(),
            "captured_at": self.captured_at.isoformat(),
            "offset": self.server_utc_offset_seconds,
            "sessions": tuple(
                (row.kind.value, row.weekday, row.session_index, row.start_second, row.end_second)
                for row in self.sessions
            ),
            "closed_dates": tuple(value.isoformat() for value in self.closed_dates),
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MarketClockObservation:
    observed_at: datetime
    last_tick_at: datetime | None
    trade_mode: SymbolTradeMode
    broker_maintenance: bool = False
    last_trade_retcode: int | None = None

    def __post_init__(self) -> None:
        _aware(self.observed_at, "market clock observation")
        if self.last_tick_at is not None:
            _aware(self.last_tick_at, "market last tick")
            if self.last_tick_at > self.observed_at:
                raise ValueError("market last tick cannot lie in the future")
        if self.last_trade_retcode is not None and self.last_trade_retcode < 0:
            raise ValueError("trade return code cannot be negative")


@dataclass(frozen=True, slots=True)
class MarketClockAssessment:
    state: MarketClockState
    normal_condition: bool
    long_entries_authorized: bool
    short_entries_authorized: bool
    position_supervision_required: bool
    research_authorized: bool
    next_open_at: datetime | None
    reasons: tuple[str, ...]

    @property
    def new_entries_authorized(self) -> bool:
        return self.long_entries_authorized or self.short_entries_authorized


@dataclass(frozen=True, slots=True)
class MT5SessionExport:
    terminal_build: int
    schedule: BrokerMarketSchedule
    trade_mode: SymbolTradeMode
    last_tick_at: datetime | None


def assess_market_clock(
    schedule: BrokerMarketSchedule,
    observation: MarketClockObservation,
    *,
    pre_open_seconds: int = 300,
    closing_soon_seconds: int = 300,
    maximum_tick_age_seconds: int = 180,
    maximum_schedule_age_days: int = 14,
) -> MarketClockAssessment:
    if min(pre_open_seconds, closing_soon_seconds, maximum_tick_age_seconds, maximum_schedule_age_days) < 1:
        raise ValueError("market clock thresholds must be positive")
    at = observation.observed_at.astimezone(timezone.utc)
    if schedule.captured_at.astimezone(timezone.utc) > at:
        return _assessment(MarketClockState.UNKNOWN, False, False, None, "market_schedule_not_yet_known")
    if at - schedule.captured_at.astimezone(timezone.utc) > timedelta(days=maximum_schedule_age_days):
        return _assessment(MarketClockState.UNKNOWN, False, False, None, "market_schedule_stale")
    if observation.broker_maintenance:
        return _assessment(MarketClockState.BROKER_MAINTENANCE, True, False, _next_trade_open(schedule, at), "broker_maintenance")

    intervals = _trade_intervals(schedule, at.date() - timedelta(days=1), days=10)
    active = next(((start, end) for start, end in intervals if start <= at < end), None)
    next_open = next((start for start, _ in intervals if start > at), None)
    closed_retcode = observation.last_trade_retcode == MARKET_CLOSED_RETCODE
    if active is not None:
        _, end = active
        if closed_retcode:
            return _assessment(MarketClockState.HALTED, False, False, next_open, "market_closed_retcode_during_scheduled_open")
        if observation.trade_mode is SymbolTradeMode.DISABLED:
            return _assessment(MarketClockState.HALTED, False, False, next_open, "symbol_trade_mode_disabled")
        if observation.last_tick_at is None or (at - observation.last_tick_at.astimezone(timezone.utc)).total_seconds() > maximum_tick_age_seconds:
            return _assessment(MarketClockState.UNEXPECTED_STALE_MARKET, False, False, next_open, "open_market_tick_stale")
        if (end - at).total_seconds() <= closing_soon_seconds:
            return _assessment(MarketClockState.CLOSING_SOON, True, False, next_open, "scheduled_close_approaching")
        if observation.trade_mode is SymbolTradeMode.CLOSE_ONLY:
            return _assessment(MarketClockState.TRADE_RESTRICTED, True, False, next_open, "symbol_trade_mode_close_only")
        if observation.trade_mode is SymbolTradeMode.LONG_ONLY:
            return _assessment(MarketClockState.TRADE_RESTRICTED, True, True, next_open, "symbol_trade_mode_long_only", short=False)
        if observation.trade_mode is SymbolTradeMode.SHORT_ONLY:
            return _assessment(MarketClockState.TRADE_RESTRICTED, True, False, next_open, "symbol_trade_mode_short_only", short=True)
        return _assessment(MarketClockState.OPEN, True, True, next_open, "broker_trade_session_open", short=True)

    if next_open is not None and (next_open - at).total_seconds() <= pre_open_seconds:
        return _assessment(MarketClockState.PRE_OPEN, True, False, next_open, "scheduled_open_approaching")
    local_at = at.astimezone(_server_timezone(schedule))
    today_intervals = tuple(
        (start, end)
        for start, end in intervals
        if start.astimezone(_server_timezone(schedule)).date() == local_at.date()
    )
    had_session = any(end <= at for _, end in today_intervals)
    has_later_session = any(start > at for start, _ in today_intervals)
    state = MarketClockState.SESSION_BREAK if had_session and has_later_session else MarketClockState.SCHEDULED_CLOSED
    reason = "scheduled_session_break" if state is MarketClockState.SESSION_BREAK else "scheduled_market_closure"
    if closed_retcode:
        reason += ":market_closed_retcode_expected"
    return _assessment(state, True, False, next_open, reason)


def parse_mt5_session_export(text: str) -> MT5SessionExport:
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "schema", "terminal_build", "broker", "server", "symbol", "captured_epoch",
        "utc_offset_seconds", "kind", "weekday", "session_index", "from_seconds", "to_seconds",
        "trade_mode", "last_tick_epoch",
    }
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("MT5 session export header is incomplete")
    rows = tuple(reader)
    if not rows:
        raise ValueError("MT5 session export is empty")
    if any(row["schema"] != "dusty-session-v1" for row in rows):
        raise ValueError("unsupported MT5 session export schema")
    identity = {
        (
            row["terminal_build"], row["broker"], row["server"], row["symbol"],
            row["captured_epoch"], row["utc_offset_seconds"], row["trade_mode"], row["last_tick_epoch"],
        )
        for row in rows
    }
    if len(identity) != 1:
        raise ValueError("MT5 session export environment identity drifted")
    terminal_build, broker, server, symbol, captured, offset, trade_mode, last_tick = next(iter(identity))
    captured_at = datetime.fromtimestamp(int(captured), tz=timezone.utc)
    sessions = tuple(
        WeeklySession(
            SessionKind(row["kind"]),
            int(row["weekday"]),
            int(row["session_index"]),
            int(row["from_seconds"]),
            int(row["to_seconds"]),
        )
        for row in rows
    )
    schedule = BrokerMarketSchedule(broker, server, symbol, captured_at, int(offset), sessions)
    return MT5SessionExport(
        int(terminal_build),
        schedule,
        SymbolTradeMode(int(trade_mode)),
        None if int(last_tick) <= 0 else datetime.fromtimestamp(int(last_tick), tz=timezone.utc),
    )


def _assessment(
    state: MarketClockState,
    normal: bool,
    long: bool,
    next_open: datetime | None,
    reason: str,
    *,
    short: bool = False,
) -> MarketClockAssessment:
    return MarketClockAssessment(state, normal, long, short, True, True, next_open, (reason,))


def _server_timezone(schedule: BrokerMarketSchedule) -> timezone:
    return timezone(timedelta(seconds=schedule.server_utc_offset_seconds))


def _trade_intervals(schedule: BrokerMarketSchedule, first_utc_date: date, *, days: int) -> tuple[tuple[datetime, datetime], ...]:
    server_tz = _server_timezone(schedule)
    first_local = datetime.combine(first_utc_date, time.min, tzinfo=timezone.utc).astimezone(server_tz).date()
    result = []
    closed = set(schedule.closed_dates)
    sessions = tuple(row for row in schedule.sessions if row.kind is SessionKind.TRADE)
    for offset in range(days + 2):
        local_date = first_local + timedelta(days=offset)
        if local_date in closed:
            continue
        for row in sessions:
            if row.weekday != local_date.weekday():
                continue
            start = datetime.combine(local_date, time.min, tzinfo=server_tz) + timedelta(seconds=row.start_second)
            if row.end_second > row.start_second:
                end = datetime.combine(local_date, time.min, tzinfo=server_tz) + timedelta(seconds=row.end_second)
            else:
                end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=server_tz) + timedelta(seconds=row.end_second)
            result.append((start.astimezone(timezone.utc), end.astimezone(timezone.utc)))
    return tuple(sorted(result))


def _next_trade_open(schedule: BrokerMarketSchedule, at: datetime) -> datetime | None:
    return next((start for start, _ in _trade_intervals(schedule, at.date() - timedelta(days=1), days=10) if start > at), None)


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
