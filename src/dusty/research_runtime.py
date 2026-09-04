from __future__ import annotations

"""Durable research heartbeat and content-addressed blackboard.

This is an autonomous research runtime only. Live broker write authority is
intentionally absent.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum, StrEnum
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


class ResearchStage(IntEnum):
    ACQUIRE = 0
    FORECAST = 1
    SCORE = 2
    INTAKE = 3
    SCREEN = 4
    EXPERIMENT = 5
    ATTRIBUTE = 6
    REMEMBER = 7
    CHECKPOINT = 8
    COMPLETE = 9


class BlackboardKind(StrEnum):
    SOURCE = "source"
    FORECAST = "forecast"
    SCORECARD = "scorecard"
    STRATEGY = "strategy"
    EXPERIMENT = "experiment"
    ATTRIBUTION = "attribution"
    LESSON = "lesson"


@dataclass(frozen=True, slots=True)
class BlackboardItem:
    kind: BlackboardKind
    identity: str
    payload_sha256: str
    parents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.identity.strip() or len(self.payload_sha256) != 64:
            raise ValueError("blackboard item identity/hash invalid")
        if any(len(value) != 64 for value in self.parents):
            raise ValueError("blackboard parents require SHA-256 identities")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "kind": self.kind.value,
                "identity": self.identity,
                "payload_sha256": self.payload_sha256,
                "parents": self.parents,
            }
        )


@dataclass(frozen=True, slots=True)
class ResearchBlackboard:
    cycle_id: str
    as_of: datetime
    items: tuple[BlackboardItem, ...]
    live_write_authorized: bool = False

    def __post_init__(self) -> None:
        if not self.cycle_id.strip():
            raise ValueError("blackboard cycle identity required")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("blackboard time must be timezone-aware")
        if self.live_write_authorized:
            raise ValueError("research blackboard cannot authorize live trading")
        fingerprints = tuple(item.fingerprint for item in self.items)
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("blackboard items must be unique")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "cycle_id": self.cycle_id,
                "as_of": self.as_of.isoformat(),
                "items": tuple(item.fingerprint for item in self.items),
                "live_write_authorized": self.live_write_authorized,
            }
        )

    def add(self, item: BlackboardItem) -> "ResearchBlackboard":
        if item.fingerprint in {value.fingerprint for value in self.items}:
            return self
        return ResearchBlackboard(
            self.cycle_id,
            self.as_of,
            self.items + (item,),
            self.live_write_authorized,
        )


@dataclass(frozen=True, slots=True)
class CycleCheckpoint:
    cycle_id: str
    stage: ResearchStage
    blackboard_fingerprint: str
    completed_job_fingerprints: tuple[str, ...]
    created_at: datetime
    live_write_authorized: bool = False

    def __post_init__(self) -> None:
        if not self.cycle_id.strip() or len(self.blackboard_fingerprint) != 64:
            raise ValueError("cycle checkpoint identity invalid")
        if any(len(value) != 64 for value in self.completed_job_fingerprints):
            raise ValueError("cycle checkpoint jobs require SHA-256 identity")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("cycle checkpoint time must be timezone-aware")
        if self.live_write_authorized:
            raise ValueError("research checkpoint cannot authorize live trading")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "cycle_id": self.cycle_id,
                "stage": int(self.stage),
                "blackboard_fingerprint": self.blackboard_fingerprint,
                "completed_job_fingerprints": self.completed_job_fingerprints,
                "created_at": self.created_at.isoformat(),
                "live_write_authorized": self.live_write_authorized,
            }
        )


def next_stage(stage: ResearchStage) -> ResearchStage:
    if stage is ResearchStage.COMPLETE:
        return stage
    return ResearchStage(int(stage) + 1)


def make_checkpoint(
    board: ResearchBlackboard,
    *,
    stage: ResearchStage,
    completed_job_fingerprints: Iterable[str],
    created_at: datetime,
) -> CycleCheckpoint:
    return CycleCheckpoint(
        cycle_id=board.cycle_id,
        stage=stage,
        blackboard_fingerprint=board.fingerprint,
        completed_job_fingerprints=tuple(sorted(set(completed_job_fingerprints))),
        created_at=created_at,
    )


class SQLiteResearchCycleStore:
    """Append-only restart state. A later record never rewrites prior proof."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS research_cycles("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "cycle_id TEXT NOT NULL,"
            "stage INTEGER NOT NULL,"
            "fingerprint TEXT NOT NULL,"
            "payload TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_cycle_id ON research_cycles(cycle_id,seq)"
        )
        self._db.commit()

    def append(self, checkpoint: CycleCheckpoint) -> None:
        payload = _canonical(
            {
                "blackboard_fingerprint": checkpoint.blackboard_fingerprint,
                "completed_job_fingerprints": checkpoint.completed_job_fingerprints,
                "created_at": checkpoint.created_at.isoformat(),
                "live_write_authorized": checkpoint.live_write_authorized,
            }
        )
        with self._db:
            self._db.execute(
                "INSERT INTO research_cycles(cycle_id,stage,fingerprint,payload) VALUES(?,?,?,?)",
                (checkpoint.cycle_id, int(checkpoint.stage), checkpoint.fingerprint, payload),
            )

    def latest(self, cycle_id: str) -> CycleCheckpoint | None:
        row = self._db.execute(
            "SELECT cycle_id,stage,payload FROM research_cycles "
            "WHERE cycle_id=? ORDER BY seq DESC LIMIT 1",
            (cycle_id,),
        ).fetchone()
        return self._row(row) if row else None

    def iter_history(self, cycle_id: str, *, batch_size: int = 256) -> Iterator[CycleCheckpoint]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        cursor = self._db.execute(
            "SELECT cycle_id,stage,payload FROM research_cycles "
            "WHERE cycle_id=? ORDER BY seq",
            (cycle_id,),
        )
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                yield self._row(row)

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _row(row: tuple[str, int, str]) -> CycleCheckpoint:
        cycle_id, stage, payload = row
        data = json.loads(payload)
        return CycleCheckpoint(
            cycle_id=cycle_id,
            stage=ResearchStage(stage),
            blackboard_fingerprint=data["blackboard_fingerprint"],
            completed_job_fingerprints=tuple(data["completed_job_fingerprints"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            live_write_authorized=bool(data["live_write_authorized"]),
        )


@dataclass(frozen=True, slots=True)
class ResearchHeartbeat:
    checkpoint: CycleCheckpoint
    resumed: bool
    next_stage: ResearchStage
    broker_write_authority: bool = False

    def __post_init__(self) -> None:
        if self.broker_write_authority:
            raise ValueError("research heartbeat cannot receive broker authority")


def heartbeat(
    store: SQLiteResearchCycleStore,
    board: ResearchBlackboard,
    *,
    now: datetime,
    completed_job_fingerprints: Iterable[str] = (),
) -> ResearchHeartbeat:
    """Advance one durable research stage; caller supplies pure stage work."""

    previous = store.latest(board.cycle_id)
    stage = ResearchStage.ACQUIRE if previous is None else next_stage(previous.stage)
    checkpoint = make_checkpoint(
        board,
        stage=stage,
        completed_job_fingerprints=completed_job_fingerprints,
        created_at=now,
    )
    store.append(checkpoint)
    return ResearchHeartbeat(
        checkpoint=checkpoint,
        resumed=previous is not None,
        next_stage=next_stage(stage),
    )


def graveyard_research_allowed(
    *,
    previously_rejected: bool,
    context_changed: bool,
    explicit_reason: str,
) -> bool:
    """Do not burn CPU rediscovering known failures without new evidence."""

    if not previously_rejected:
        return True
    return context_changed and bool(explicit_reason.strip())


def research_job_fingerprint(
    *,
    proposal_fingerprint: str,
    school: str,
    fidelity: str,
    context_fingerprint: str,
) -> str:
    for value in (proposal_fingerprint, context_fingerprint):
        if len(value) != 64:
            raise ValueError("research job requires SHA-256 proposal/context")
    return _digest(
        {
            "proposal": proposal_fingerprint,
            "school": school,
            "fidelity": fidelity,
            "context": context_fingerprint,
        }
    )
