from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True, slots=True)
class TemporalKnowledge:
    record_id: str
    kind: str
    text: str
    tags: tuple[str, ...]
    known_at: datetime
    effective_at: datetime
    expires_at: datetime | None = None
    source_id: str | None = None

    def __post_init__(self) -> None:
        if not self.record_id.strip() or not self.kind.strip() or not self.text.strip() or not self.tags:
            raise ValueError("temporal knowledge requires identity, kind, text and tags")
        for label, value in (("known_at", self.known_at), ("effective_at", self.effective_at)):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{label} must be timezone-aware")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
                raise ValueError("expires_at must be timezone-aware")
            if self.expires_at < self.known_at:
                raise ValueError("knowledge cannot expire before it was known")

    @classmethod
    def of(
        cls,
        *,
        record_id: str,
        kind: str,
        text: str,
        tags: Iterable[str],
        known_at: datetime,
        effective_at: datetime,
        expires_at: datetime | None = None,
        source_id: str | None = None,
    ) -> "TemporalKnowledge":
        normalized = tuple(sorted({tag.strip().lower() for tag in tags if tag.strip()}))
        return cls(record_id, kind, text, normalized, known_at, effective_at, expires_at, source_id)


class SQLitePITKnowledge:
    """Small point-in-time-safe knowledge store; retrieval itself enforces known-time semantics."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS pit_knowledge("
            "record_id TEXT PRIMARY KEY,kind TEXT NOT NULL,text TEXT NOT NULL,tags TEXT NOT NULL,"
            "known_at REAL NOT NULL,effective_at REAL NOT NULL,expires_at REAL,source_id TEXT)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_pit_known ON pit_knowledge(known_at,record_id)")
        self._db.commit()

    def remember(self, record: TemporalKnowledge) -> None:
        tags = "\x1f".join(record.tags)
        with self._db:
            self._db.execute(
                "INSERT INTO pit_knowledge(record_id,kind,text,tags,known_at,effective_at,expires_at,source_id) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    record.record_id,
                    record.kind,
                    record.text,
                    tags,
                    record.known_at.timestamp(),
                    record.effective_at.timestamp(),
                    record.expires_at.timestamp() if record.expires_at else None,
                    record.source_id,
                ),
            )

    def retrieve_as_of(self, tags: Iterable[str], *, as_of: datetime, limit: int = 50) -> tuple[TemporalKnowledge, ...]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must be timezone-aware")
        if limit < 1:
            raise ValueError("limit must be positive")
        wanted = {tag.strip().lower() for tag in tags if tag.strip()}
        if not wanted:
            return ()
        instant = as_of.timestamp()
        rows = self._db.execute(
            "SELECT record_id,kind,text,tags,known_at,effective_at,expires_at,source_id "
            "FROM pit_knowledge WHERE known_at<=? AND (expires_at IS NULL OR expires_at>=?) "
            "ORDER BY known_at DESC,record_id",
            (instant, instant),
        ).fetchall()
        result = []
        for row in rows:
            record_tags = tuple(filter(None, row[3].split("\x1f")))
            if not wanted.intersection(record_tags):
                continue
            result.append(
                TemporalKnowledge(
                    record_id=row[0],
                    kind=row[1],
                    text=row[2],
                    tags=record_tags,
                    known_at=datetime.fromtimestamp(float(row[4]), tz=timezone.utc),
                    effective_at=datetime.fromtimestamp(float(row[5]), tz=timezone.utc),
                    expires_at=datetime.fromtimestamp(float(row[6]), tz=timezone.utc) if row[6] is not None else None,
                    source_id=row[7],
                )
            )
            if len(result) >= limit:
                break
        return tuple(result)

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()
