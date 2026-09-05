from __future__ import annotations

"""M164 append-only, content-addressed research artifact vault.

Unlike M163's regenerable replay cache, this vault is the durable evidence layer.
Records are append-only provenance envelopes over immutable SHA-256 blobs.  A
missing or damaged blob raises an integrity error; evidence is never silently
converted into a cache miss or rewritten in place.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
from typing import Iterator
from uuid import uuid4


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _digest(payload: object) -> str:
    return _digest_bytes(_canonical(payload).encode("utf-8"))


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: str, label: str) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered:
        raise ValueError(f"{label} must be non-empty and one line")
    return rendered


class ArtifactKind(StrEnum):
    DATASET = "dataset"
    MANIFEST = "manifest"
    FORECAST = "forecast"
    TESTER_REPORT = "tester_report"
    DEALS = "deals"
    EVALUATION = "evaluation"
    CALIBRATION = "calibration"
    LOG = "log"
    OTHER = "other"


class ArtifactIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ResearchArtifactRecord:
    record_fingerprint: str
    blob_sha256: str
    kind: ArtifactKind
    content_type: str
    producer_fingerprint: str
    subject_fingerprint: str
    source_fingerprints: tuple[str, ...]
    size_bytes: int
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_fingerprint", _sha(self.record_fingerprint, "artifact record"))
        object.__setattr__(self, "blob_sha256", _sha(self.blob_sha256, "artifact blob"))
        object.__setattr__(self, "content_type", _text(self.content_type, "artifact content_type"))
        object.__setattr__(self, "producer_fingerprint", _sha(self.producer_fingerprint, "artifact producer"))
        object.__setattr__(self, "subject_fingerprint", _sha(self.subject_fingerprint, "artifact subject"))
        object.__setattr__(
            self,
            "source_fingerprints",
            tuple(sorted({_sha(value, "artifact source") for value in self.source_fingerprints})),
        )
        if isinstance(self.size_bytes, bool) or int(self.size_bytes) != self.size_bytes or int(self.size_bytes) < 0:
            raise ValueError("artifact size must be nonnegative")
        object.__setattr__(self, "size_bytes", int(self.size_bytes))
        object.__setattr__(self, "created_at", _aware(self.created_at, "artifact created_at"))
        if self.record_fingerprint != _digest(self.payload):
            raise ArtifactIntegrityError("artifact record fingerprint does not match provenance payload")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m164-artifact-vault-v1",
            "blob_sha256": self.blob_sha256,
            "kind": self.kind.value,
            "content_type": self.content_type,
            "producer_fingerprint": self.producer_fingerprint,
            "subject_fingerprint": self.subject_fingerprint,
            "source_fingerprints": list(self.source_fingerprints),
            "size_bytes": self.size_bytes,
            "created_at": self.created_at.isoformat(),
        }

    @property
    def broker_write_authority(self) -> bool:
        return False


class ResearchArtifactVault:
    def __init__(self, root: str | Path, *, busy_timeout_ms: int = 5000) -> None:
        if not 1 <= busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000")
        self.root = Path(root)
        self.blob_root = self.root / "blobs"
        self.tmp_root = self.root / "tmp"
        self.root.mkdir(parents=True, exist_ok=True)
        self.blob_root.mkdir(parents=True, exist_ok=True)
        self.tmp_root.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            str(self.root / "artifact-vault.sqlite3"),
            timeout=busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS artifact_blobs("
            "blob_sha256 TEXT PRIMARY KEY,"
            "size_bytes INTEGER NOT NULL,"
            "first_seen_at TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS artifact_records("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "record_fingerprint TEXT NOT NULL UNIQUE,"
            "blob_sha256 TEXT NOT NULL,"
            "kind TEXT NOT NULL,"
            "content_type TEXT NOT NULL,"
            "producer_fingerprint TEXT NOT NULL,"
            "subject_fingerprint TEXT NOT NULL,"
            "source_fingerprints TEXT NOT NULL,"
            "size_bytes INTEGER NOT NULL,"
            "created_at TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,"
            "FOREIGN KEY(blob_sha256) REFERENCES artifact_blobs(blob_sha256))"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_subject ON artifact_records(subject_fingerprint,seq)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_artifact_blob ON artifact_records(blob_sha256,seq)"
        )

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def promotion_authorized(self) -> bool:
        return False

    @contextmanager
    def _write(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    def _blob_path(self, blob_sha256: str) -> Path:
        digest = _sha(blob_sha256, "vault blob path")
        return self.blob_root / digest[:2] / digest[2:4] / f"{digest}.bin"

    def _write_blob(self, data: bytes, digest: str) -> None:
        target = self._blob_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            try:
                existing = target.read_bytes()
            except OSError as exc:
                raise ArtifactIntegrityError("existing artifact blob cannot be read") from exc
            if _digest_bytes(existing) != digest:
                raise ArtifactIntegrityError("existing artifact blob is corrupt")
            return
        temporary = self.tmp_root / f"{digest}.{uuid4().hex}.tmp"
        with temporary.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()

    def store_bytes(
        self,
        data: bytes,
        *,
        kind: ArtifactKind,
        content_type: str,
        producer_fingerprint: str,
        subject_fingerprint: str,
        source_fingerprints: tuple[str, ...] = (),
        now: datetime,
    ) -> ResearchArtifactRecord:
        if not isinstance(data, bytes):
            raise TypeError("artifact vault stores immutable bytes")
        content_type = _text(content_type, "artifact content_type")
        producer = _sha(producer_fingerprint, "artifact producer")
        subject = _sha(subject_fingerprint, "artifact subject")
        sources = tuple(sorted({_sha(value, "artifact source") for value in source_fingerprints}))
        created = _aware(now, "artifact created_at")
        blob_sha = _digest_bytes(data)
        self._write_blob(data, blob_sha)
        payload = {
            "protocol": "dusty-m164-artifact-vault-v1",
            "blob_sha256": blob_sha,
            "kind": ArtifactKind(kind).value,
            "content_type": content_type,
            "producer_fingerprint": producer,
            "subject_fingerprint": subject,
            "source_fingerprints": list(sources),
            "size_bytes": len(data),
            "created_at": created.isoformat(),
        }
        record_fp = _digest(payload)
        payload_sha = _digest(payload)
        sources_json = _canonical(list(sources))

        with self._write():
            blob_row = self._db.execute(
                "SELECT size_bytes FROM artifact_blobs WHERE blob_sha256=?",
                (blob_sha,),
            ).fetchone()
            if blob_row is None:
                self._db.execute(
                    "INSERT INTO artifact_blobs(blob_sha256,size_bytes,first_seen_at) VALUES(?,?,?)",
                    (blob_sha, len(data), created.isoformat()),
                )
            elif int(blob_row[0]) != len(data):
                raise ArtifactIntegrityError("artifact blob metadata size mismatch")

            existing = self._db.execute(
                "SELECT payload_sha256 FROM artifact_records WHERE record_fingerprint=?",
                (record_fp,),
            ).fetchone()
            if existing is None:
                self._db.execute(
                    "INSERT INTO artifact_records("
                    "record_fingerprint,blob_sha256,kind,content_type,producer_fingerprint,subject_fingerprint,"
                    "source_fingerprints,size_bytes,created_at,payload_sha256) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        record_fp,
                        blob_sha,
                        ArtifactKind(kind).value,
                        content_type,
                        producer,
                        subject,
                        sources_json,
                        len(data),
                        created.isoformat(),
                        payload_sha,
                    ),
                )
            elif str(existing[0]) != payload_sha:
                raise ArtifactIntegrityError("artifact record collision/corruption detected")
        return ResearchArtifactRecord(
            record_fp,
            blob_sha,
            ArtifactKind(kind),
            content_type,
            producer,
            subject,
            sources,
            len(data),
            created,
        )

    def get_record(self, record_fingerprint: str) -> ResearchArtifactRecord | None:
        fingerprint = _sha(record_fingerprint, "artifact record lookup")
        row = self._db.execute(
            "SELECT blob_sha256,kind,content_type,producer_fingerprint,subject_fingerprint,source_fingerprints,"
            "size_bytes,created_at,payload_sha256 FROM artifact_records WHERE record_fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if row is None:
            return None
        try:
            sources_raw = json.loads(str(row[5]))
        except json.JSONDecodeError as exc:
            raise ArtifactIntegrityError("artifact provenance JSON is corrupt") from exc
        if not isinstance(sources_raw, list):
            raise ArtifactIntegrityError("artifact provenance sources are corrupt")
        record = ResearchArtifactRecord(
            fingerprint,
            str(row[0]),
            ArtifactKind(str(row[1])),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            tuple(str(value) for value in sources_raw),
            int(row[6]),
            datetime.fromisoformat(str(row[7])),
        )
        if _digest(record.payload) != str(row[8]):
            raise ArtifactIntegrityError("artifact ledger payload hash mismatch")
        return record

    def read_bytes(self, record_fingerprint: str) -> bytes:
        record = self.get_record(record_fingerprint)
        if record is None:
            raise KeyError(record_fingerprint)
        path = self._blob_path(record.blob_sha256)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ArtifactIntegrityError("artifact blob missing or unreadable") from exc
        if len(data) != record.size_bytes or _digest_bytes(data) != record.blob_sha256:
            raise ArtifactIntegrityError("artifact blob hash/size mismatch")
        return data

    def list_subject(self, subject_fingerprint: str) -> tuple[ResearchArtifactRecord, ...]:
        subject = _sha(subject_fingerprint, "artifact subject query")
        rows = self._db.execute(
            "SELECT record_fingerprint FROM artifact_records WHERE subject_fingerprint=? ORDER BY seq",
            (subject,),
        ).fetchall()
        return tuple(self.get_record(str(row[0])) for row in rows)  # type: ignore[arg-type]

    def integrity_check(self) -> tuple[bool, tuple[str, ...]]:
        errors: list[str] = []
        db_result = str(self._db.execute("PRAGMA integrity_check").fetchone()[0])
        if db_result.lower() != "ok":
            errors.append(f"sqlite:{db_result}")
        rows = self._db.execute("SELECT record_fingerprint FROM artifact_records ORDER BY seq").fetchall()
        for (record_fp,) in rows:
            try:
                self.read_bytes(str(record_fp))
            except (ArtifactIntegrityError, KeyError, ValueError) as exc:
                errors.append(f"artifact:{record_fp}:{type(exc).__name__}")
        return (not errors, tuple(errors))

    def close(self) -> None:
        self._db.close()
