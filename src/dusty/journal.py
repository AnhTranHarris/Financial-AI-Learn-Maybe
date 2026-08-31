from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from .core import (
    AnalystState,
    CoherenceState,
    Decision,
    ExceptionLevel,
    GuardianState,
    PatienceState,
    ReasoningEvent,
    ReasoningPhase,
    SkepticState,
    advance,
)


@dataclass(frozen=True, slots=True)
class JournalRecord:
    timestamp: str
    person_id: str
    symbol: str
    strategy_id: str
    snapshot_id: str
    analyst: AnalystState
    skeptic: SkepticState
    patience: PatienceState
    guardian: GuardianState
    coherence: CoherenceState
    exception: ExceptionLevel
    hypothesis_id: str
    decision: Decision
    event: ReasoningEvent
    previous_phase: ReasoningPhase
    new_phase: ReasoningPhase
    reason_codes: tuple[str, ...] = ()

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @classmethod
    def from_json(cls, payload: str) -> "JournalRecord":
        data = json.loads(payload)
        data["analyst"] = AnalystState(data["analyst"])
        data["skeptic"] = SkepticState(data["skeptic"])
        data["patience"] = PatienceState(data["patience"])
        data["guardian"] = GuardianState(data["guardian"])
        data["coherence"] = CoherenceState(data["coherence"])
        data["exception"] = ExceptionLevel(data["exception"])
        data["decision"] = Decision(data["decision"])
        data["event"] = ReasoningEvent(data["event"])
        data["previous_phase"] = ReasoningPhase(data["previous_phase"])
        data["new_phase"] = ReasoningPhase(data["new_phase"])
        data["reason_codes"] = tuple(data.get("reason_codes", ()))
        return cls(**data)


class SQLiteJournal:
    """Tiny append-only semantic journal. SQLite is the only persistence dependency."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS journal("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "person_id TEXT NOT NULL,"
            "payload TEXT NOT NULL)"
        )
        self._db.commit()

    def append(self, record: JournalRecord) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO journal(person_id,payload) VALUES(?,?)",
                (record.person_id, record.to_json()),
            )

    def records(self, person_id: str | None = None) -> list[JournalRecord]:
        if person_id is None:
            rows = self._db.execute("SELECT payload FROM journal ORDER BY seq").fetchall()
        else:
            rows = self._db.execute(
                "SELECT payload FROM journal WHERE person_id=? ORDER BY seq",
                (person_id,),
            ).fetchall()
        return [JournalRecord.from_json(row[0]) for row in rows]

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()


def replay(records: list[JournalRecord]) -> tuple[Decision, ...]:
    """Validate recorded transitions and reproduce the semantic decision trace."""
    if not records:
        return ()
    phase = records[0].previous_phase
    decisions: list[Decision] = []
    for record in records:
        if phase is not record.previous_phase:
            raise ValueError("journal phase discontinuity")
        expected = (
            ReasoningPhase.STAND_DOWN
            if record.event is ReasoningEvent.STAND_DOWN
            else advance(phase, record.event)
        )
        if expected is not record.new_phase:
            raise ValueError("journal transition mismatch")
        phase = record.new_phase
        decisions.append(record.decision)
    return tuple(decisions)
