from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from .growth import ResearchCycle, ResearchCyclePolicy, assess_research_cycle, next_cycle_capital
from .resource import ResourceState


class SupervisorPriority(IntEnum):
    EMERGENCY = 0
    POSITION_SUPERVISION = 1
    BROKER_RECONCILIATION = 2
    JOURNAL = 3
    EVIDENCE = 4
    FORECAST = 5
    RESEARCH = 6
    BACKTEST = 7
    TRAINING = 8


@dataclass(frozen=True, slots=True)
class SupervisorAdmission:
    admitted: bool
    reason: str


def admit_supervisor_job(priority: SupervisorPriority, state: ResourceState) -> SupervisorAdmission:
    """Open-position safety outranks all research work under host pressure."""
    ceiling = {
        ResourceState.GREEN: SupervisorPriority.TRAINING,
        ResourceState.YELLOW: SupervisorPriority.BACKTEST,
        ResourceState.ORANGE: SupervisorPriority.FORECAST,
        ResourceState.RED: SupervisorPriority.JOURNAL,
    }[state]
    admitted = priority <= ceiling
    return SupervisorAdmission(admitted, "admitted" if admitted else f"resource_{state.value}_throttle")


@dataclass(frozen=True, slots=True)
class TerminalLease:
    terminal_id: str
    owner_id: str
    acquired_at: datetime
    lease_until: datetime

    def __post_init__(self) -> None:
        if not self.terminal_id.strip() or not self.owner_id.strip():
            raise ValueError("terminal lease requires terminal and owner")
        if self.acquired_at.tzinfo is None or self.acquired_at.utcoffset() is None or self.lease_until.tzinfo is None or self.lease_until.utcoffset() is None:
            raise ValueError("lease timestamps must be timezone-aware")
        if self.lease_until <= self.acquired_at:
            raise ValueError("lease must expire after acquisition")


@dataclass(frozen=True, slots=True)
class SupervisorCheckpoint:
    supervisor_id: str
    step: str
    payload_hash: str
    completed_at: datetime


class SQLiteSupervisorState:
    """Durable terminal leases and completed-step checkpoints for restart-safe desk orchestration."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS terminal_leases("
            "terminal_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,acquired_at REAL NOT NULL,lease_until REAL NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS supervisor_checkpoints("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,supervisor_id TEXT NOT NULL,step TEXT NOT NULL,"
            "payload_hash TEXT NOT NULL,completed_at REAL NOT NULL,UNIQUE(supervisor_id,step,payload_hash))"
        )
        self._db.commit()

    def acquire_lease(
        self,
        terminal_id: str,
        owner_id: str,
        *,
        at: datetime,
        duration: timedelta,
    ) -> TerminalLease | None:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("lease time must be timezone-aware")
        if duration.total_seconds() <= 0:
            raise ValueError("lease duration must be positive")
        if not terminal_id.strip() or not owner_id.strip():
            raise ValueError("terminal and owner are required")
        now = at.timestamp()
        until = (at + duration).timestamp()
        with self._db:
            cursor = self._db.execute(
                "INSERT INTO terminal_leases(terminal_id,owner_id,acquired_at,lease_until) VALUES(?,?,?,?) "
                "ON CONFLICT(terminal_id) DO UPDATE SET owner_id=excluded.owner_id,acquired_at=excluded.acquired_at,lease_until=excluded.lease_until "
                "WHERE terminal_leases.lease_until<=? OR terminal_leases.owner_id=excluded.owner_id",
                (terminal_id, owner_id, now, until, now),
            )
        if cursor.rowcount != 1:
            return None
        return TerminalLease(terminal_id, owner_id, at, at + duration)

    def release_lease(self, terminal_id: str, owner_id: str) -> bool:
        with self._db:
            cursor = self._db.execute(
                "DELETE FROM terminal_leases WHERE terminal_id=? AND owner_id=?",
                (terminal_id, owner_id),
            )
        return cursor.rowcount == 1

    def checkpoint(self, supervisor_id: str, step: str, payload: object, *, at: datetime) -> SupervisorCheckpoint:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("checkpoint time must be timezone-aware")
        if not supervisor_id.strip() or not step.strip():
            raise ValueError("checkpoint requires supervisor and step")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = sha256(canonical.encode("utf-8")).hexdigest()
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO supervisor_checkpoints(supervisor_id,step,payload_hash,completed_at) VALUES(?,?,?,?)",
                (supervisor_id, step, digest, at.timestamp()),
            )
        return SupervisorCheckpoint(supervisor_id, step, digest, at)

    def completed(self, supervisor_id: str, step: str, payload_hash: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM supervisor_checkpoints WHERE supervisor_id=? AND step=? AND payload_hash=? LIMIT 1",
            (supervisor_id, step, payload_hash),
        ).fetchone()
        return row is not None

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()


@dataclass(frozen=True, slots=True)
class DeskAssignment:
    desk_id: str
    terminal_id: str


def assign_desks(
    desk_ids: Iterable[str],
    terminal_ids: Iterable[str],
    state: SQLiteSupervisorState,
    *,
    at: datetime,
    lease_duration: timedelta = timedelta(minutes=5),
) -> tuple[DeskAssignment, ...]:
    desks = tuple(sorted({item for item in desk_ids if item}))
    terminals = tuple(sorted({item for item in terminal_ids if item}))
    result = []
    for desk_id, terminal_id in zip(desks, terminals):
        lease = state.acquire_lease(terminal_id, desk_id, at=at, duration=lease_duration)
        if lease is not None:
            result.append(DeskAssignment(desk_id, terminal_id))
    return tuple(result)


@dataclass(frozen=True, slots=True)
class CapitalCycleDecision:
    passed: bool
    current_starting_capital: float
    proposed_next_capital: float
    next_starting_capital: float
    reasons: tuple[str, ...]


def decide_next_capital_cycle(
    cycle: ResearchCycle,
    *,
    proposed_next_capital: float,
    policy: ResearchCyclePolicy = ResearchCyclePolicy(),
) -> CapitalCycleDecision:
    assessment = assess_research_cycle(cycle, policy)
    next_capital = next_cycle_capital(
        cycle.starting_capital,
        proposed_next_capital,
        cycle_passed=assessment.passed,
    )
    return CapitalCycleDecision(
        assessment.passed,
        cycle.starting_capital,
        proposed_next_capital,
        next_capital,
        assessment.reasons,
    )
