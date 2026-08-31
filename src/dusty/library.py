from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Iterator, Mapping


class ArtifactClass(StrEnum):
    IRREPLACEABLE = "irreplaceable"
    DURABLE = "durable"
    RECONSTRUCTIBLE = "reconstructible"
    TEMPORARY = "temporary"


@dataclass(frozen=True, slots=True)
class SourceRecord:
    source_id: str
    platform: str
    source_url: str
    retrieved_at: datetime
    content_hash: str
    provenance: str

    def __post_init__(self) -> None:
        if not all((self.source_id, self.platform, self.source_url, self.content_hash, self.provenance)):
            raise ValueError("source identity, location, hash, and provenance are required")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("source retrieval time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    kind: str
    artifact_class: ArtifactClass
    byte_size: int
    created_at: datetime
    source_id: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.artifact_id or not self.kind:
            raise ValueError("artifact identity and kind are required")
        if self.byte_size < 0:
            raise ValueError("artifact byte size cannot be negative")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("artifact timestamp must be timezone-aware")

    @classmethod
    def of(
        cls,
        artifact_id: str,
        kind: str,
        artifact_class: ArtifactClass,
        byte_size: int,
        created_at: datetime,
        *,
        source_id: str | None = None,
        metadata: Mapping[str, str] | None = None,
    ) -> "ArtifactRecord":
        return cls(
            artifact_id,
            kind,
            artifact_class,
            byte_size,
            created_at,
            source_id,
            tuple(sorted((metadata or {}).items())),
        )


class SQLiteLearningLibrary:
    """Disk-first catalog. Raw market history remains owned by its upstream source (for example MT5)."""

    def __init__(self, path: str | Path, *, allow_memory: bool = False) -> None:
        if str(path) == ":memory:" and not allow_memory:
            raise ValueError("production learning library requires a persistent path")
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sources("
            "source_id TEXT PRIMARY KEY, platform TEXT NOT NULL, source_url TEXT NOT NULL,"
            "retrieved_at TEXT NOT NULL, content_hash TEXT NOT NULL, provenance TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS artifacts("
            "artifact_id TEXT PRIMARY KEY, kind TEXT NOT NULL, artifact_class TEXT NOT NULL,"
            "byte_size INTEGER NOT NULL, created_at TEXT NOT NULL, source_id TEXT, metadata TEXT NOT NULL,"
            "FOREIGN KEY(source_id) REFERENCES sources(source_id))"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_class ON artifacts(artifact_class,created_at)"
        )
        self._db.commit()

    def register_source(self, record: SourceRecord) -> None:
        existing = self._db.execute(
            "SELECT platform,source_url,retrieved_at,content_hash,provenance FROM sources WHERE source_id=?",
            (record.source_id,),
        ).fetchone()
        values = (
            record.platform,
            record.source_url,
            record.retrieved_at.isoformat(),
            record.content_hash,
            record.provenance,
        )
        if existing is not None:
            if tuple(existing) != values:
                raise ValueError("source_id already refers to different provenance")
            return
        with self._db:
            self._db.execute(
                "INSERT INTO sources(source_id,platform,source_url,retrieved_at,content_hash,provenance) "
                "VALUES(?,?,?,?,?,?)",
                (record.source_id, *values),
            )

    def remember_artifact(self, record: ArtifactRecord) -> None:
        payload = json.dumps(dict(record.metadata), sort_keys=True, separators=(",", ":"))
        with self._db:
            self._db.execute(
                "INSERT INTO artifacts(artifact_id,kind,artifact_class,byte_size,created_at,source_id,metadata) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    record.artifact_id,
                    record.kind,
                    record.artifact_class.value,
                    record.byte_size,
                    record.created_at.isoformat(),
                    record.source_id,
                    payload,
                ),
            )

    def iter_artifacts(self, *, batch_size: int = 256) -> Iterator[ArtifactRecord]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        cursor = self._db.execute(
            "SELECT artifact_id,kind,artifact_class,byte_size,created_at,source_id,metadata "
            "FROM artifacts ORDER BY created_at,artifact_id"
        )
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                metadata = tuple(sorted(json.loads(row[6]).items()))
                yield ArtifactRecord(
                    artifact_id=row[0],
                    kind=row[1],
                    artifact_class=ArtifactClass(row[2]),
                    byte_size=int(row[3]),
                    created_at=datetime.fromisoformat(row[4]),
                    source_id=row[5],
                    metadata=metadata,
                )

    def reclaim_candidates(self, *, limit: int = 100) -> tuple[str, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        rows = self._db.execute(
            "SELECT artifact_id FROM artifacts WHERE artifact_class IN (?,?) "
            "ORDER BY created_at,artifact_id LIMIT ?",
            (ArtifactClass.TEMPORARY.value, ArtifactClass.RECONSTRUCTIBLE.value, limit),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def bytes_by_class(self) -> dict[ArtifactClass, int]:
        rows = self._db.execute(
            "SELECT artifact_class,COALESCE(SUM(byte_size),0) FROM artifacts GROUP BY artifact_class"
        ).fetchall()
        return {ArtifactClass(name): int(total) for name, total in rows}

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()
