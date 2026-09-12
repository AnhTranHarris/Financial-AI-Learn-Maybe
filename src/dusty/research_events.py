from __future__ import annotations

"""Point-in-time scheduled-event evidence for frozen quant research.

This module owns no acquisition, broker, execution, or promotion authority. It
only validates already-collected schedule evidence and deterministically marks
runtime bars whose decision timestamp falls inside a strategy exclusion window.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Iterable

from .runtime import RuntimeBar

UTC = timezone.utc
EVENT_EVIDENCE_PROTOCOL = "dusty-pit-research-event-evidence-v3"


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


@dataclass(frozen=True, slots=True)
class ResearchScheduledEvent:
    event_id: str
    scheduled_at: datetime
    known_at: datetime
    currencies: tuple[str, ...]
    source_id: str
    source_fingerprint: str

    def __post_init__(self) -> None:
        event_id = self.event_id.strip()
        source_id = self.source_id.strip()
        if not event_id or not source_id:
            raise ValueError("research event requires event and source identity")
        scheduled = _aware_utc(self.scheduled_at, "scheduled_at")
        known = _aware_utc(self.known_at, "known_at")
        if known > scheduled:
            raise ValueError("event schedule was not known before the scheduled event")
        if not self.currencies or any(not str(item).strip() for item in self.currencies):
            raise ValueError("research event requires currencies")
        fp = self.source_fingerprint.strip().lower()
        if len(fp) != 64 or any(ch not in "0123456789abcdef" for ch in fp):
            raise ValueError("event source fingerprint must be SHA-256")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "source_id", source_id)
        object.__setattr__(self, "scheduled_at", scheduled)
        object.__setattr__(self, "known_at", known)
        object.__setattr__(self, "currencies", tuple(sorted({str(item).strip().upper() for item in self.currencies})))
        object.__setattr__(self, "source_fingerprint", fp)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "scheduled_at": self.scheduled_at.isoformat(),
            "known_at": self.known_at.isoformat(),
            "currencies": list(self.currencies),
            "source_id": self.source_id,
            "source_fingerprint": self.source_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ResearchEventEvidence:
    symbol: str
    coverage_start: datetime
    coverage_end: datetime
    events: tuple[ResearchScheduledEvent, ...]

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        start = _aware_utc(self.coverage_start, "coverage_start")
        end = _aware_utc(self.coverage_end, "coverage_end")
        if not symbol or end <= start:
            raise ValueError("invalid event evidence coverage")
        ids = [row.event_id for row in self.events]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate research event identity")
        if any(not start <= row.scheduled_at < end for row in self.events):
            raise ValueError("event falls outside declared evidence coverage")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "coverage_start", start)
        object.__setattr__(self, "coverage_end", end)
        object.__setattr__(self, "events", tuple(sorted(self.events, key=lambda row: (row.scheduled_at, row.event_id))))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": EVENT_EVIDENCE_PROTOCOL,
            "symbol": self.symbol,
            "coverage_start": self.coverage_start.isoformat(),
            "coverage_end": self.coverage_end.isoformat(),
            "events": [row.payload for row in self.events],
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return sha256(_canonical(self.payload).encode("utf-8")).hexdigest()


def symbol_currencies(symbol: str) -> tuple[str, ...]:
    value = symbol.strip().upper()
    if len(value) == 6 and value.isalpha():
        return value[:3], value[3:]
    raise ValueError("event evidence currently requires a canonical six-letter FX symbol")


def bind_event_exclusions(
    rows: Iterable[RuntimeBar],
    evidence: ResearchEventEvidence,
    *,
    exclusion_minutes: int,
    expected_symbol: str | None = None,
) -> tuple[RuntimeBar, ...]:
    runtime = tuple(rows)
    minutes = int(exclusion_minutes)
    if minutes < 0:
        raise ValueError("event exclusion cannot be negative")
    if not runtime or minutes == 0:
        return runtime
    if tuple(sorted(runtime, key=lambda row: row.at)) != runtime:
        raise ValueError("runtime bars must be chronological before event binding")
    if expected_symbol is not None and evidence.symbol != expected_symbol.strip().upper():
        raise ValueError("event evidence symbol does not match frozen strategy symbol")

    first = _aware_utc(runtime[0].at, "runtime start")
    last = _aware_utc(runtime[-1].at, "runtime end")
    radius = timedelta(minutes=minutes)
    # Symmetric exclusion requires evidence beyond both dataset edges; otherwise
    # an event just outside the frozen range could still block an edge decision.
    if evidence.coverage_start > first - radius or evidence.coverage_end <= last + radius:
        raise ValueError("event evidence does not fully cover the exclusion-adjusted frozen runtime range")

    relevant = set(symbol_currencies(evidence.symbol))
    events = tuple(row for row in evidence.events if relevant.intersection(row.currencies))
    return tuple(
        replace(
            bar,
            event_blocked=any(
                event.known_at <= _aware_utc(bar.at, "runtime bar")
                and abs(_aware_utc(bar.at, "runtime bar") - event.scheduled_at) <= radius
                for event in events
            ),
        )
        for bar in runtime
    )


def event_evidence_from_json(payload: object) -> ResearchEventEvidence:
    if not isinstance(payload, dict) or payload.get("protocol") != EVENT_EVIDENCE_PROTOCOL:
        raise ValueError("unsupported research event evidence protocol")
    raw_events = payload.get("events")
    if not isinstance(raw_events, list):
        raise ValueError("event evidence events must be a list")
    events = []
    for raw in raw_events:
        if not isinstance(raw, dict):
            raise ValueError("event evidence row must be an object")
        currencies = raw.get("currencies")
        if not isinstance(currencies, list):
            raise ValueError("event currencies must be a list")
        events.append(
            ResearchScheduledEvent(
                event_id=str(raw.get("event_id", "")),
                scheduled_at=datetime.fromisoformat(str(raw.get("scheduled_at", ""))),
                known_at=datetime.fromisoformat(str(raw.get("known_at", ""))),
                currencies=tuple(str(item) for item in currencies),
                source_id=str(raw.get("source_id", "")),
                source_fingerprint=str(raw.get("source_fingerprint", "")),
            )
        )
    return ResearchEventEvidence(
        symbol=str(payload.get("symbol", "")),
        coverage_start=datetime.fromisoformat(str(payload.get("coverage_start", ""))),
        coverage_end=datetime.fromisoformat(str(payload.get("coverage_end", ""))),
        events=tuple(events),
    )
