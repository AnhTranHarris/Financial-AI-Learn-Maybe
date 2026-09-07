from __future__ import annotations

"""Durable evidence journal for the M200 long-running Demo soak.

This module records observations only. It has no broker/provider mutation
capability and deliberately accepts already-classified evidence from the earlier
M188/M190/M191/market-clock boundaries.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3

from .long_running_soak import (
    SoakDisturbanceEvidence,
    SoakDisturbanceKind,
    SoakEvidenceMode,
    SoakRecoveryStatus,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _sha256(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha1(value: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError("source commit requires full SHA-1 Git identity")
    return rendered


@dataclass(frozen=True, slots=True)
class SoakRunIdentity:
    run_id: str
    source_commit: str
    terminal_fingerprint: str
    account_fingerprint: str
    started_at: datetime
    baseline_artifact_fingerprint: str

    def __post_init__(self) -> None:
        if not self.run_id.strip() or len(self.run_id) > 128:
            raise ValueError("soak run id is invalid")
        object.__setattr__(self, "source_commit", _git_sha1(self.source_commit))
        object.__setattr__(self, "terminal_fingerprint", _sha256(self.terminal_fingerprint, "terminal fingerprint"))
        object.__setattr__(self, "account_fingerprint", _sha256(self.account_fingerprint, "account fingerprint"))
        object.__setattr__(self, "started_at", _utc(self.started_at, "soak start"))
        object.__setattr__(
            self,
            "baseline_artifact_fingerprint",
            _sha256(self.baseline_artifact_fingerprint, "baseline artifact fingerprint"),
        )

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m200-run-identity-v1",
            self.run_id,
            self.source_commit,
            self.terminal_fingerprint,
            self.account_fingerprint,
            self.started_at.isoformat(),
            self.baseline_artifact_fingerprint,
        ))


@dataclass(frozen=True, slots=True)
class SoakHeartbeatEvidence:
    observed_at: datetime
    process_id: int
    terminal_connected: bool
    terminal_fingerprint: str
    account_fingerprint: str
    positions_count: int
    orders_count: int
    state_fingerprint: str
    artifact_fingerprint: str
    previous_record_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _utc(self.observed_at, "heartbeat time"))
        if type(self.process_id) is not int or self.process_id <= 0:
            raise ValueError("process_id must be a positive integer")
        for field in ("positions_count", "orders_count"):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field} must be a nonnegative integer")
        for field in (
            "terminal_fingerprint",
            "account_fingerprint",
            "state_fingerprint",
            "artifact_fingerprint",
        ):
            object.__setattr__(self, field, _sha256(getattr(self, field), field))
        if self.previous_record_fingerprint is not None:
            object.__setattr__(
                self,
                "previous_record_fingerprint",
                _sha256(self.previous_record_fingerprint, "previous record fingerprint"),
            )

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m200-heartbeat-v1",
            self.observed_at.isoformat(),
            self.process_id,
            self.terminal_connected,
            self.terminal_fingerprint,
            self.account_fingerprint,
            self.positions_count,
            self.orders_count,
            self.state_fingerprint,
            self.artifact_fingerprint,
            self.previous_record_fingerprint,
        ))


class SQLiteSoakEvidenceStore:
    """Append-only, hash-chained M200 evidence store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self.path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS run_identity("
            "singleton INTEGER PRIMARY KEY CHECK(singleton=1),payload TEXT NOT NULL,fingerprint TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS heartbeats("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT NOT NULL,fingerprint TEXT NOT NULL UNIQUE)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS disturbances("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,payload TEXT NOT NULL,fingerprint TEXT NOT NULL UNIQUE)"
        )
        self._db.commit()

    def initialize(self, identity: SoakRunIdentity) -> None:
        payload = _canonical({
            "run_id": identity.run_id,
            "source_commit": identity.source_commit,
            "terminal_fingerprint": identity.terminal_fingerprint,
            "account_fingerprint": identity.account_fingerprint,
            "started_at": identity.started_at.isoformat(),
            "baseline_artifact_fingerprint": identity.baseline_artifact_fingerprint,
        })
        existing = self._db.execute("SELECT payload,fingerprint FROM run_identity WHERE singleton=1").fetchone()
        if existing is not None:
            if existing != (payload, identity.fingerprint):
                raise ValueError("M200 soak store already belongs to a different run identity")
            return
        with self._db:
            self._db.execute(
                "INSERT INTO run_identity(singleton,payload,fingerprint) VALUES(1,?,?)",
                (payload, identity.fingerprint),
            )

    def identity(self) -> SoakRunIdentity:
        row = self._db.execute("SELECT payload FROM run_identity WHERE singleton=1").fetchone()
        if row is None:
            raise ValueError("M200 soak store is not initialized")
        data = json.loads(row[0])
        return SoakRunIdentity(
            run_id=data["run_id"],
            source_commit=data["source_commit"],
            terminal_fingerprint=data["terminal_fingerprint"],
            account_fingerprint=data["account_fingerprint"],
            started_at=datetime.fromisoformat(data["started_at"]),
            baseline_artifact_fingerprint=data["baseline_artifact_fingerprint"],
        )

    def append_heartbeat(self, heartbeat: SoakHeartbeatEvidence) -> None:
        identity = self.identity()
        if heartbeat.terminal_fingerprint != identity.terminal_fingerprint:
            raise ValueError("M200 heartbeat terminal identity drift")
        if heartbeat.account_fingerprint != identity.account_fingerprint:
            raise ValueError("M200 heartbeat account identity drift")
        previous = self.latest_heartbeat()
        expected_previous = None if previous is None else previous.fingerprint
        if heartbeat.previous_record_fingerprint != expected_previous:
            raise ValueError("M200 heartbeat hash chain mismatch")
        if previous is not None and heartbeat.observed_at <= previous.observed_at:
            raise ValueError("M200 heartbeats must advance in real time")
        payload = _canonical({
            "observed_at": heartbeat.observed_at.isoformat(),
            "process_id": heartbeat.process_id,
            "terminal_connected": heartbeat.terminal_connected,
            "terminal_fingerprint": heartbeat.terminal_fingerprint,
            "account_fingerprint": heartbeat.account_fingerprint,
            "positions_count": heartbeat.positions_count,
            "orders_count": heartbeat.orders_count,
            "state_fingerprint": heartbeat.state_fingerprint,
            "artifact_fingerprint": heartbeat.artifact_fingerprint,
            "previous_record_fingerprint": heartbeat.previous_record_fingerprint,
        })
        with self._db:
            self._db.execute(
                "INSERT INTO heartbeats(payload,fingerprint) VALUES(?,?)",
                (payload, heartbeat.fingerprint),
            )

    def append_disturbance(self, evidence: SoakDisturbanceEvidence) -> None:
        payload = _canonical({
            "kind": evidence.kind.value,
            "occurred_at": evidence.occurred_at.astimezone(timezone.utc).isoformat(),
            "recovery_status": evidence.recovery_status.value,
            "evidence_fingerprint": evidence.evidence_fingerprint,
            "mode": evidence.mode.value,
        })
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO disturbances(payload,fingerprint) VALUES(?,?)",
                (payload, evidence.fingerprint),
            )

    def latest_heartbeat(self) -> SoakHeartbeatEvidence | None:
        row = self._db.execute("SELECT payload FROM heartbeats ORDER BY seq DESC LIMIT 1").fetchone()
        return self._heartbeat(row[0]) if row else None

    def heartbeats(self) -> tuple[SoakHeartbeatEvidence, ...]:
        return tuple(self._heartbeat(row[0]) for row in self._db.execute("SELECT payload FROM heartbeats ORDER BY seq"))

    def disturbances(self) -> tuple[SoakDisturbanceEvidence, ...]:
        rows = []
        for (payload,) in self._db.execute("SELECT payload FROM disturbances ORDER BY seq"):
            data = json.loads(payload)
            rows.append(SoakDisturbanceEvidence(
                kind=SoakDisturbanceKind(data["kind"]),
                occurred_at=datetime.fromisoformat(data["occurred_at"]),
                recovery_status=SoakRecoveryStatus(data["recovery_status"]),
                evidence_fingerprint=data["evidence_fingerprint"],
                mode=SoakEvidenceMode(data["mode"]),
            ))
        return tuple(rows)

    def process_restart_observed(self) -> bool:
        rows = self.heartbeats()
        return any(left.process_id != right.process_id for left, right in zip(rows, rows[1:]))

    def hash_chain_ok(self) -> bool:
        previous: str | None = None
        last_time: datetime | None = None
        for row in self.heartbeats():
            if row.previous_record_fingerprint != previous:
                return False
            if last_time is not None and row.observed_at <= last_time:
                return False
            previous = row.fingerprint
            last_time = row.observed_at
        return True

    def integrity_ok(self) -> bool:
        row = self._db.execute("PRAGMA integrity_check").fetchone()
        return bool(row and row[0] == "ok") and self.hash_chain_ok()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _heartbeat(payload: str) -> SoakHeartbeatEvidence:
        data = json.loads(payload)
        return SoakHeartbeatEvidence(
            observed_at=datetime.fromisoformat(data["observed_at"]),
            process_id=int(data["process_id"]),
            terminal_connected=bool(data["terminal_connected"]),
            terminal_fingerprint=data["terminal_fingerprint"],
            account_fingerprint=data["account_fingerprint"],
            positions_count=int(data["positions_count"]),
            orders_count=int(data["orders_count"]),
            state_fingerprint=data["state_fingerprint"],
            artifact_fingerprint=data["artifact_fingerprint"],
            previous_record_fingerprint=data["previous_record_fingerprint"],
        )
