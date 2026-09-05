from __future__ import annotations

"""M163 content-addressed deterministic replay cache.

The cache accelerates exact research replays; it is deliberately not an evidence
vault and never grants authority.  Every lookup is bound to a canonical execution
identity and every returned byte is re-hashed before use.  Corrupt or missing
blobs are invalidated and become cache misses on the next lookup.
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


def _sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _commit(value: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError("software_commit requires a 40-character Git SHA")
    return rendered


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("cache timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReplayCacheKey:
    execution_fingerprint: str
    dataset_fingerprint: str
    engine_fingerprint: str
    software_commit: str
    input_fingerprints: tuple[str, ...]
    deterministic_seed: int | None = None
    protocol: str = "dusty-m163-replay-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "execution_fingerprint", _sha(self.execution_fingerprint, "replay execution"))
        object.__setattr__(self, "dataset_fingerprint", _sha(self.dataset_fingerprint, "replay dataset"))
        object.__setattr__(self, "engine_fingerprint", _sha(self.engine_fingerprint, "replay engine"))
        object.__setattr__(self, "software_commit", _commit(self.software_commit))
        object.__setattr__(
            self,
            "input_fingerprints",
            tuple(_sha(value, "replay input") for value in self.input_fingerprints),
        )
        if self.deterministic_seed is not None:
            seed = self.deterministic_seed
            if isinstance(seed, bool) or int(seed) != seed or not 0 <= int(seed) <= 2**63 - 1:
                raise ValueError("deterministic_seed must be a nonnegative integer")
            object.__setattr__(self, "deterministic_seed", int(seed))
        protocol = str(self.protocol).strip()
        if not protocol:
            raise ValueError("replay protocol required")
        object.__setattr__(self, "protocol", protocol)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "execution_fingerprint": self.execution_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "engine_fingerprint": self.engine_fingerprint,
            "software_commit": self.software_commit,
            "input_fingerprints": list(self.input_fingerprints),
            "deterministic_seed": self.deterministic_seed,
        }

    @property
    def fingerprint(self) -> str:
        return _sha256_bytes(_canonical(self.payload).encode("utf-8"))


class ReplayLookupStatus(StrEnum):
    HIT = "hit"
    MISS = "miss"
    CORRUPT = "corrupt"


@dataclass(frozen=True, slots=True)
class ReplayCacheRecord:
    key_fingerprint: str
    blob_sha256: str
    content_type: str
    size_bytes: int
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "key_fingerprint", _sha(self.key_fingerprint, "cache key"))
        object.__setattr__(self, "blob_sha256", _sha(self.blob_sha256, "cache blob"))
        content_type = str(self.content_type).strip()
        if not content_type or "\n" in content_type or "\r" in content_type:
            raise ValueError("cache content_type must be one line")
        object.__setattr__(self, "content_type", content_type)
        if isinstance(self.size_bytes, bool) or int(self.size_bytes) != self.size_bytes or int(self.size_bytes) < 0:
            raise ValueError("cache size must be nonnegative")
        object.__setattr__(self, "size_bytes", int(self.size_bytes))
        object.__setattr__(self, "created_at", _aware(self.created_at))


@dataclass(frozen=True, slots=True)
class ReplayLookup:
    status: ReplayLookupStatus
    record: ReplayCacheRecord | None = None
    data: bytes | None = None

    def __post_init__(self) -> None:
        if self.status is ReplayLookupStatus.HIT:
            if self.record is None or self.data is None:
                raise ValueError("cache HIT requires record and bytes")
        elif self.record is not None or self.data is not None:
            raise ValueError("cache MISS/CORRUPT cannot return replay bytes")


class DeterministicReplayCache:
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
            str(self.root / "replay-cache.sqlite3"),
            timeout=busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS replay_entries("
            "key_fingerprint TEXT PRIMARY KEY,"
            "key_payload TEXT NOT NULL,"
            "key_payload_sha256 TEXT NOT NULL,"
            "blob_sha256 TEXT NOT NULL,"
            "content_type TEXT NOT NULL,"
            "size_bytes INTEGER NOT NULL,"
            "created_at TEXT NOT NULL)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_replay_blob ON replay_entries(blob_sha256)")

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
        digest = _sha(blob_sha256, "cache blob path")
        return self.blob_root / digest[:2] / digest[2:4] / f"{digest}.bin"

    def _atomic_blob_write(self, data: bytes, digest: str) -> Path:
        target = self._blob_path(digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            existing = target.read_bytes()
            if _sha256_bytes(existing) != digest:
                raise RuntimeError("existing replay blob is corrupt")
            return target
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
        return target

    def put(
        self,
        key: ReplayCacheKey,
        data: bytes,
        *,
        content_type: str,
        now: datetime,
    ) -> ReplayCacheRecord:
        if not isinstance(data, bytes):
            raise TypeError("replay cache stores immutable bytes")
        content_type = str(content_type).strip()
        if not content_type or "\n" in content_type or "\r" in content_type:
            raise ValueError("content_type must be one line")
        created = _aware(now)
        key_payload = _canonical(key.payload)
        key_payload_sha = _sha256_bytes(key_payload.encode("utf-8"))
        blob_sha = _sha256_bytes(data)
        self._atomic_blob_write(data, blob_sha)

        with self._write():
            existing = self._db.execute(
                "SELECT key_payload_sha256,blob_sha256,content_type,size_bytes,created_at "
                "FROM replay_entries WHERE key_fingerprint=?",
                (key.fingerprint,),
            ).fetchone()
            if existing is not None:
                if existing[0] != key_payload_sha or existing[1] != blob_sha or existing[2] != content_type or int(existing[3]) != len(data):
                    raise RuntimeError("replay key collision or non-deterministic output detected")
                return ReplayCacheRecord(
                    key.fingerprint,
                    existing[1],
                    existing[2],
                    int(existing[3]),
                    datetime.fromisoformat(existing[4]),
                )
            self._db.execute(
                "INSERT INTO replay_entries(key_fingerprint,key_payload,key_payload_sha256,blob_sha256,content_type,size_bytes,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    key.fingerprint,
                    key_payload,
                    key_payload_sha,
                    blob_sha,
                    content_type,
                    len(data),
                    created.isoformat(),
                ),
            )
        return ReplayCacheRecord(key.fingerprint, blob_sha, content_type, len(data), created)

    def get(self, key: ReplayCacheKey) -> ReplayLookup:
        row = self._db.execute(
            "SELECT key_payload,key_payload_sha256,blob_sha256,content_type,size_bytes,created_at "
            "FROM replay_entries WHERE key_fingerprint=?",
            (key.fingerprint,),
        ).fetchone()
        if row is None:
            return ReplayLookup(ReplayLookupStatus.MISS)

        expected_key_payload = _canonical(key.payload)
        valid_metadata = (
            _sha256_bytes(str(row[0]).encode("utf-8")) == row[1]
            and str(row[0]) == expected_key_payload
            and row[1] == _sha256_bytes(expected_key_payload.encode("utf-8"))
        )
        blob_sha = str(row[2])
        path = self._blob_path(blob_sha)
        try:
            data = path.read_bytes()
        except OSError:
            data = b""
            valid_blob = False
        else:
            valid_blob = _sha256_bytes(data) == blob_sha and len(data) == int(row[4])

        if not valid_metadata or not valid_blob:
            self._invalidate(key.fingerprint, blob_sha)
            return ReplayLookup(ReplayLookupStatus.CORRUPT)

        record = ReplayCacheRecord(
            key.fingerprint,
            blob_sha,
            str(row[3]),
            int(row[4]),
            datetime.fromisoformat(str(row[5])),
        )
        return ReplayLookup(ReplayLookupStatus.HIT, record=record, data=data)

    def _invalidate(self, key_fingerprint: str, blob_sha256: str) -> None:
        with self._write():
            self._db.execute("DELETE FROM replay_entries WHERE key_fingerprint=?", (key_fingerprint,))
            remaining = self._db.execute(
                "SELECT COUNT(*) FROM replay_entries WHERE blob_sha256=?",
                (blob_sha256,),
            ).fetchone()[0]
        if int(remaining) == 0:
            try:
                self._blob_path(blob_sha256).unlink(missing_ok=True)
            except OSError:
                pass

    def integrity_check(self) -> tuple[bool, tuple[str, ...]]:
        errors: list[str] = []
        db_result = str(self._db.execute("PRAGMA integrity_check").fetchone()[0])
        if db_result.lower() != "ok":
            errors.append(f"sqlite:{db_result}")
        rows = self._db.execute(
            "SELECT key_fingerprint,key_payload,key_payload_sha256,blob_sha256,size_bytes FROM replay_entries ORDER BY key_fingerprint"
        ).fetchall()
        for key_fp, key_payload, key_payload_sha, blob_sha, size_bytes in rows:
            if _sha256_bytes(str(key_payload).encode("utf-8")) != str(key_payload_sha):
                errors.append(f"metadata:{key_fp}")
                continue
            path = self._blob_path(str(blob_sha))
            try:
                data = path.read_bytes()
            except OSError:
                errors.append(f"missing:{key_fp}")
                continue
            if _sha256_bytes(data) != str(blob_sha) or len(data) != int(size_bytes):
                errors.append(f"blob:{key_fp}")
        return (not errors, tuple(errors))

    def close(self) -> None:
        self._db.close()
