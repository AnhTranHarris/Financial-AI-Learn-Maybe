from __future__ import annotations

"""Deterministic point-in-time research-session classification.

These labels are research context, not broker trading-session authority. They
are derived only from a timezone-aware UTC observation timestamp and fixed civil
session definitions. Broker open/closed authority remains owned by
``market_clock`` and native MT5 schedule evidence.
"""

from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json


SUPPORTED_RESEARCH_SESSIONS = ("ASIA", "LONDON", "NEW_YORK")
_SESSION_EVIDENCE_PROTOCOL_BASE = "dusty-pit-research-sessions-v1"
SESSION_EVIDENCE_DEFINITION = {
    "protocol": _SESSION_EVIDENCE_PROTOCOL_BASE,
    "sessions": {
        "ASIA": {
            "civil_zone": "Asia/Tokyo",
            "local_start": "09:00",
            "local_end": "18:00",
            "dst_rule": "none",
        },
        "LONDON": {
            "civil_zone": "Europe/London",
            "local_start": "08:00",
            "local_end": "17:00",
            "dst_rule": "post-1996:last_sunday_march_0100utc:last_sunday_october_0100utc",
        },
        "NEW_YORK": {
            "civil_zone": "America/New_York",
            "local_start": "08:00",
            "local_end": "17:00",
            "dst_rule": "post-2007:second_sunday_march_0200local:first_sunday_november_0200local",
        },
    },
    "overlap_semantics": "retain_all_active_sessions",
    "broker_authority": False,
}
SESSION_EVIDENCE_FINGERPRINT = sha256(
    json.dumps(SESSION_EVIDENCE_DEFINITION, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
SESSION_EVIDENCE_PROTOCOL = f"{_SESSION_EVIDENCE_PROTOCOL_BASE}:{SESSION_EVIDENCE_FINGERPRINT}"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("research session timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    if not 0 <= weekday <= 6 or occurrence < 1:
        raise ValueError("weekday/occurrence is invalid")
    first = date(year, month, 1)
    delta = (weekday - first.weekday()) % 7
    return first + timedelta(days=delta + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        first_next = date(year + 1, 1, 1)
    else:
        first_next = date(year, month + 1, 1)
    candidate = first_next - timedelta(days=1)
    return candidate - timedelta(days=(candidate.weekday() - weekday) % 7)


def _london_dst(at: datetime) -> bool:
    """Post-1996 UK DST rule, evaluated in UTC."""

    at = _aware_utc(at)
    year = at.year
    start_day = _last_weekday(year, 3, 6)
    end_day = _last_weekday(year, 10, 6)
    start = datetime(year, 3, start_day.day, 1, tzinfo=timezone.utc)
    end = datetime(year, 10, end_day.day, 1, tzinfo=timezone.utc)
    return start <= at < end


def _new_york_dst(at: datetime) -> bool:
    """Post-2007 US Eastern DST rule, evaluated in UTC."""

    at = _aware_utc(at)
    year = at.year
    start_day = _nth_weekday(year, 3, 6, 2)
    end_day = _nth_weekday(year, 11, 6, 1)
    # 02:00 local standard = 07:00 UTC; 02:00 local daylight = 06:00 UTC.
    start = datetime(year, 3, start_day.day, 7, tzinfo=timezone.utc)
    end = datetime(year, 11, end_day.day, 6, tzinfo=timezone.utc)
    return start <= at < end


def active_research_sessions(at: datetime) -> tuple[str, ...]:
    """Return all active canonical research sessions at ``at``.

    Civil definitions are explicit and content-addressed above. Overlap is
    retained; a timestamp may therefore belong to both LONDON and NEW_YORK.
    This function never asserts that a broker is open.
    """

    at = _aware_utc(at)
    minute = at.hour * 60 + at.minute
    result: list[str] = []

    # Tokyo 09:00-18:00 = 00:00-09:00 UTC.
    if 0 <= minute < 9 * 60:
        result.append("ASIA")

    london_offset_minutes = 60 if _london_dst(at) else 0
    london_local_minute = (minute + london_offset_minutes) % (24 * 60)
    if 8 * 60 <= london_local_minute < 17 * 60:
        result.append("LONDON")

    ny_offset_minutes = -4 * 60 if _new_york_dst(at) else -5 * 60
    ny_local_minute = (minute + ny_offset_minutes) % (24 * 60)
    if 8 * 60 <= ny_local_minute < 17 * 60:
        result.append("NEW_YORK")

    return tuple(result)


def matching_research_session(at: datetime, requested: tuple[str, ...]) -> str:
    """Return one requested active session for the legacy RuntimeBar channel.

    ``RuntimeBar.session`` is singular, while session windows can overlap. The
    evaluator calls this function with the frozen strategy's allowed set, so any
    active requested session is sufficient. Selection follows canonical sorted
    order and therefore stays deterministic.
    """

    normalized = tuple(sorted({item.strip().upper() for item in requested if item.strip()}))
    unknown = tuple(item for item in normalized if item not in SUPPORTED_RESEARCH_SESSIONS)
    if unknown:
        raise ValueError(f"unsupported research session filters: {','.join(unknown)}")
    active = set(active_research_sessions(at))
    return next((item for item in normalized if item in active), "")
