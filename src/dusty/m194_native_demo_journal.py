from __future__ import annotations

"""Durable append-only evidence journal for one real M194 Demo desk run."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: object, label: str, *, maximum: int = 256) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _sha256(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires a full Git SHA")
    return rendered


class M194NativeEventKind(StrEnum):
    RUN_STARTED = "run_started"
    HEARTBEAT = "heartbeat"
    COMPLETED_CYCLE = "completed_cycle"
    RECONCILED_EXECUTION = "reconciled_execution"
    EXECUTION_COST_SAMPLE = "execution_cost_sample"
    RECOVERY_CHECKPOINT = "recovery_checkpoint"
    OPERATIONAL_EXERCISE = "operational_exercise"
    RUN_ENDED = "run_ended"


@dataclass(frozen=True, slots=True)
class M194NativeEvidenceEvent:
    run_id: str
    kind: M194NativeEventKind
    occurred_at: datetime
    source_commit: str
    champion_fingerprint: str
    evidence_fingerprints: tuple[str, ...]
    payload: dict[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", maximum=128))
        if not isinstance(self.kind, M194NativeEventKind):
            raise ValueError("kind must use M194NativeEventKind")
        object.__setattr__(self, "occurred_at", _aware(self.occurred_at, "occurred_at"))
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "source commit"))
        object.__setattr__(self, "champion_fingerprint", _sha256(self.champion_fingerprint, "Champion"))
        evidence = tuple(sorted(_sha256(value, "event evidence") for value in self.evidence_fingerprints))
        if not evidence or len(evidence) != len(set(evidence)):
            raise ValueError("event evidence must be unique and nonempty")
        object.__setattr__(self, "evidence_fingerprints", evidence)
        if not isinstance(self.payload, dict):
            raise ValueError("event payload must be a mapping")
        # Canonicalization is also a validation step: NaN/Inf and non-serializable
        # values fail before they can enter the evidence journal.
        _canonical(self.payload)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m1941-native-evidence-event-v1",
            self.run_id,
            self.kind.value,
            self.occurred_at.isoformat(),
            self.source_commit,
            self.champion_fingerprint,
            self.evidence_fingerprints,
            self.payload,
        ))


class SQLiteM194NativeEvidenceJournal:
    """Append-only SQLite journal with per-event fingerprint verification."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS m194_native_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_fingerprint TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    source_commit TEXT NOT NULL,
                    champion_fingerprint TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            db.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30.0)

    def append(self, event: M194NativeEvidenceEvent) -> str:
        with self._connect() as db:
            if event.kind is not M194NativeEventKind.RUN_STARTED:
                start = db.execute(
                    "SELECT source_commit, champion_fingerprint FROM m194_native_events WHERE run_id=? AND kind=? ORDER BY sequence LIMIT 1",
                    (event.run_id, M194NativeEventKind.RUN_STARTED.value),
                ).fetchone()
                if start is None:
                    raise ValueError("M194 native run must start before subsequent evidence")
                if start[0] != event.source_commit or start[1] != event.champion_fingerprint:
                    raise ValueError("M194 native run identity drift")
            else:
                prior = db.execute(
                    "SELECT 1 FROM m194_native_events WHERE run_id=? LIMIT 1",
                    (event.run_id,),
                ).fetchone()
                if prior is not None:
                    raise ValueError("M194 native run_id already exists")

            if db.execute(
                "SELECT 1 FROM m194_native_events WHERE run_id=? AND kind=? LIMIT 1",
                (event.run_id, M194NativeEventKind.RUN_ENDED.value),
            ).fetchone() is not None:
                raise ValueError("M194 native run is already closed")

            try:
                db.execute(
                    """
                    INSERT INTO m194_native_events (
                        event_fingerprint, run_id, kind, occurred_at, source_commit,
                        champion_fingerprint, evidence_json, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.fingerprint,
                        event.run_id,
                        event.kind.value,
                        event.occurred_at.isoformat(),
                        event.source_commit,
                        event.champion_fingerprint,
                        _canonical(event.evidence_fingerprints),
                        _canonical(event.payload),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("duplicate M194 native evidence event") from exc
            db.commit()
        return event.fingerprint

    def events(self, run_id: str) -> tuple[M194NativeEvidenceEvent, ...]:
        run = _text(run_id, "run_id", maximum=128)
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT event_fingerprint, kind, occurred_at, source_commit,
                       champion_fingerprint, evidence_json, payload_json
                FROM m194_native_events
                WHERE run_id=?
                ORDER BY sequence
                """,
                (run,),
            ).fetchall()
        events: list[M194NativeEvidenceEvent] = []
        for stored_fp, kind, occurred_at, source_commit, champion_fp, evidence_json, payload_json in rows:
            event = M194NativeEvidenceEvent(
                run,
                M194NativeEventKind(kind),
                datetime.fromisoformat(occurred_at),
                source_commit,
                champion_fp,
                tuple(json.loads(evidence_json)),
                dict(json.loads(payload_json)),
            )
            if event.fingerprint != stored_fp:
                raise RuntimeError("M194 native journal integrity failure")
            events.append(event)
        return tuple(events)

    def summary(self, run_id: str) -> dict[str, int | bool]:
        rows = self.events(run_id)
        counts = {kind: 0 for kind in M194NativeEventKind}
        for row in rows:
            counts[row.kind] += 1
        return {
            "started": counts[M194NativeEventKind.RUN_STARTED] == 1,
            "ended": counts[M194NativeEventKind.RUN_ENDED] == 1,
            "heartbeats": counts[M194NativeEventKind.HEARTBEAT],
            "completed_cycles": counts[M194NativeEventKind.COMPLETED_CYCLE],
            "reconciled_executions": counts[M194NativeEventKind.RECONCILED_EXECUTION],
            "execution_cost_samples": counts[M194NativeEventKind.EXECUTION_COST_SAMPLE],
            "recovery_checkpoints": counts[M194NativeEventKind.RECOVERY_CHECKPOINT],
            "operational_exercises": counts[M194NativeEventKind.OPERATIONAL_EXERCISE],
        }
