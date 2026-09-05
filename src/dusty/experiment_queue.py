from __future__ import annotations

"""M155 durable, research-only experiment queue.

The queue coordinates bounded research work on one workstation. It owns no broker
credentials, order API, risk override, entry veto, or Champion-promotion surface.
Workers receive immutable experiment specifications and must claim them through
short SQLite transactions before doing expensive work outside the database.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError(f"{label} requires SHA-256 identity")


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _utc(value: datetime) -> datetime:
    _aware(value, "timestamp")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _utc(value).isoformat()


class ExperimentResource(StrEnum):
    CPU_RESEARCH = "cpu_research"
    MT5_TESTER = "mt5_tester"
    FORECAST = "forecast"
    OLLAMA = "ollama"


class ExperimentState(StrEnum):
    QUEUED = "queued"
    LEASED = "leased"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ExperimentJobSpec:
    proposal_fingerprint: str
    genome_fingerprint: str
    variant_fingerprint: str
    context_fingerprint: str
    symbol: str
    timeframe: str
    school: str
    fidelity: str
    resource: ExperimentResource
    priority: int = 0
    max_attempts: int = 3

    def __post_init__(self) -> None:
        for value, label in (
            (self.proposal_fingerprint, "proposal"),
            (self.genome_fingerprint, "genome"),
            (self.variant_fingerprint, "variant"),
            (self.context_fingerprint, "context"),
        ):
            _sha(value, f"experiment {label}")
        if not self.symbol.strip() or not self.timeframe.strip() or not self.school.strip() or not self.fidelity.strip():
            raise ValueError("experiment identity fields are required")
        if not -100 <= self.priority <= 100:
            raise ValueError("experiment priority must be between -100 and 100")
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("experiment max_attempts must be between 1 and 10")

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "proposal_fingerprint": self.proposal_fingerprint.lower(),
            "genome_fingerprint": self.genome_fingerprint.lower(),
            "variant_fingerprint": self.variant_fingerprint.lower(),
            "context_fingerprint": self.context_fingerprint.lower(),
            "symbol": self.symbol.upper(),
            "timeframe": self.timeframe.upper(),
            "school": self.school.strip().lower(),
            "fidelity": self.fidelity.strip().lower(),
            "resource": self.resource.value,
            "priority": self.priority,
            "max_attempts": self.max_attempts,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, object]) -> "ExperimentJobSpec":
        return cls(
            proposal_fingerprint=str(payload["proposal_fingerprint"]),
            genome_fingerprint=str(payload["genome_fingerprint"]),
            variant_fingerprint=str(payload["variant_fingerprint"]),
            context_fingerprint=str(payload["context_fingerprint"]),
            symbol=str(payload["symbol"]),
            timeframe=str(payload["timeframe"]),
            school=str(payload["school"]),
            fidelity=str(payload["fidelity"]),
            resource=ExperimentResource(str(payload["resource"])),
            priority=int(payload["priority"]),
            max_attempts=int(payload["max_attempts"]),
        )


@dataclass(frozen=True, slots=True)
class ExperimentLease:
    job_fingerprint: str
    spec: ExperimentJobSpec
    worker_id: str
    attempt: int
    claimed_at: datetime
    lease_until: datetime
    broker_write_authority: bool = False

    def __post_init__(self) -> None:
        _sha(self.job_fingerprint, "leased job")
        _aware(self.claimed_at, "claim time")
        _aware(self.lease_until, "lease expiry")
        if not self.worker_id.strip() or self.attempt < 1 or self.lease_until <= self.claimed_at:
            raise ValueError("experiment lease identity/timing invalid")
        if self.broker_write_authority:
            raise ValueError("experiment lease cannot receive broker authority")


@dataclass(frozen=True, slots=True)
class ExperimentSnapshot:
    job_fingerprint: str
    spec: ExperimentJobSpec
    state: ExperimentState
    attempt_count: int
    available_at: datetime
    lease_owner: str | None
    lease_until: datetime | None
    result_fingerprint: str | None
    last_error: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ExperimentEvent:
    sequence: int
    job_fingerprint: str
    event_type: str
    event_at: datetime
    event_fingerprint: str
    details: dict[str, object]


class SQLiteExperimentQueue:
    """Single-workstation durable queue with atomic leases and bounded retries.

    Every process should open its own queue instance. Expensive research runs occur
    after claim() commits, so the SQLite writer lock is held only for short state
    transitions. WAL permits concurrent readers while SQLite serializes writers.
    """

    def __init__(self, path: str | Path = ":memory:", *, busy_timeout_ms: int = 5000) -> None:
        if not 1 <= busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000")
        self._db = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000.0, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS experiment_jobs("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "fingerprint TEXT NOT NULL UNIQUE,"
            "payload TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,"
            "resource TEXT NOT NULL,"
            "priority INTEGER NOT NULL,"
            "max_attempts INTEGER NOT NULL,"
            "state TEXT NOT NULL,"
            "attempt_count INTEGER NOT NULL,"
            "available_at TEXT NOT NULL,"
            "lease_owner TEXT,"
            "lease_until TEXT,"
            "result_fingerprint TEXT,"
            "last_error TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "updated_at TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiment_claim "
            "ON experiment_jobs(state,resource,available_at,priority,seq)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS experiment_events("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "job_fingerprint TEXT NOT NULL,"
            "event_type TEXT NOT NULL,"
            "event_at TEXT NOT NULL,"
            "event_fingerprint TEXT NOT NULL,"
            "details TEXT NOT NULL,"
            "FOREIGN KEY(job_fingerprint) REFERENCES experiment_jobs(fingerprint))"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_experiment_events_job "
            "ON experiment_events(job_fingerprint,seq)"
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

    def _event(self, fingerprint: str, event_type: str, event_at: datetime, details: dict[str, object]) -> None:
        rendered = _canonical(details)
        event_fp = _digest(
            {
                "job": fingerprint,
                "event_type": event_type,
                "event_at": _iso(event_at),
                "details": json.loads(rendered),
            }
        )
        self._db.execute(
            "INSERT INTO experiment_events(job_fingerprint,event_type,event_at,event_fingerprint,details) "
            "VALUES(?,?,?,?,?)",
            (fingerprint, event_type, _iso(event_at), event_fp, rendered),
        )

    def enqueue(self, spec: ExperimentJobSpec, *, now: datetime) -> bool:
        now_iso = _iso(now)
        payload = _canonical(spec.payload)
        payload_sha = sha256(payload.encode("utf-8")).hexdigest()
        with self._write():
            existing = self._db.execute(
                "SELECT payload_sha256 FROM experiment_jobs WHERE fingerprint=?",
                (spec.fingerprint,),
            ).fetchone()
            if existing is not None:
                if existing[0] != payload_sha:
                    raise RuntimeError("experiment fingerprint collision/corruption detected")
                return False
            self._db.execute(
                "INSERT INTO experiment_jobs("
                "fingerprint,payload,payload_sha256,resource,priority,max_attempts,state,attempt_count,"
                "available_at,lease_owner,lease_until,result_fingerprint,last_error,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    spec.fingerprint,
                    payload,
                    payload_sha,
                    spec.resource.value,
                    spec.priority,
                    spec.max_attempts,
                    ExperimentState.QUEUED.value,
                    0,
                    now_iso,
                    None,
                    None,
                    None,
                    "",
                    now_iso,
                    now_iso,
                ),
            )
            self._event(spec.fingerprint, "ENQUEUED", now, {"resource": spec.resource.value, "priority": spec.priority})
        return True

    def _expire_terminal_leases(self, *, now: datetime) -> None:
        now_iso = _iso(now)
        rows = self._db.execute(
            "SELECT fingerprint,attempt_count,max_attempts FROM experiment_jobs "
            "WHERE state=? AND lease_until IS NOT NULL AND lease_until<=? AND attempt_count>=max_attempts",
            (ExperimentState.LEASED.value, now_iso),
        ).fetchall()
        for fingerprint, attempts, max_attempts in rows:
            self._db.execute(
                "UPDATE experiment_jobs SET state=?,lease_owner=NULL,lease_until=NULL,last_error=?,updated_at=? "
                "WHERE fingerprint=?",
                (
                    ExperimentState.FAILED.value,
                    "lease_expired_after_final_attempt",
                    now_iso,
                    fingerprint,
                ),
            )
            self._event(
                fingerprint,
                "FAILED",
                now,
                {"reason": "lease_expired_after_final_attempt", "attempt": attempts, "max_attempts": max_attempts},
            )

    def claim(
        self,
        worker_id: str,
        *,
        resources: Iterable[ExperimentResource],
        now: datetime,
        lease_seconds: int = 300,
    ) -> ExperimentLease | None:
        if not worker_id.strip():
            raise ValueError("worker_id required")
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        resource_values = tuple(sorted({ExperimentResource(value).value for value in resources}))
        if not resource_values:
            raise ValueError("claim requires at least one resource class")
        now_utc = _utc(now)
        now_iso = now_utc.isoformat()
        lease_until = now_utc + timedelta(seconds=lease_seconds)
        placeholders = ",".join("?" for _ in resource_values)

        with self._write():
            self._expire_terminal_leases(now=now_utc)
            row = self._db.execute(
                "SELECT fingerprint,payload,state,attempt_count,max_attempts,lease_owner FROM experiment_jobs "
                f"WHERE resource IN ({placeholders}) AND attempt_count<max_attempts AND ("
                "(state=? AND available_at<=?) OR "
                "(state=? AND lease_until IS NOT NULL AND lease_until<=?)) "
                "ORDER BY priority DESC,seq ASC LIMIT 1",
                (*resource_values, ExperimentState.QUEUED.value, now_iso, ExperimentState.LEASED.value, now_iso),
            ).fetchone()
            if row is None:
                return None
            fingerprint, payload, prior_state, attempt_count, max_attempts, prior_owner = row
            if prior_state == ExperimentState.LEASED.value:
                self._event(
                    fingerprint,
                    "LEASE_EXPIRED",
                    now_utc,
                    {"prior_worker": prior_owner or "", "attempt": attempt_count},
                )
            attempt = int(attempt_count) + 1
            self._db.execute(
                "UPDATE experiment_jobs SET state=?,attempt_count=?,lease_owner=?,lease_until=?,last_error='',updated_at=? "
                "WHERE fingerprint=?",
                (
                    ExperimentState.LEASED.value,
                    attempt,
                    worker_id,
                    lease_until.isoformat(),
                    now_iso,
                    fingerprint,
                ),
            )
            self._event(
                fingerprint,
                "LEASED",
                now_utc,
                {
                    "worker_id": worker_id,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "lease_until": lease_until.isoformat(),
                },
            )
            spec = ExperimentJobSpec.from_payload(json.loads(payload))
        return ExperimentLease(fingerprint, spec, worker_id, attempt, now_utc, lease_until)

    def _active_lease_row(self, fingerprint: str, worker_id: str, now: datetime) -> tuple[int, int, datetime]:
        _sha(fingerprint, "experiment job")
        if not worker_id.strip():
            raise ValueError("worker_id required")
        row = self._db.execute(
            "SELECT state,attempt_count,max_attempts,lease_owner,lease_until FROM experiment_jobs WHERE fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if row is None:
            raise KeyError("experiment job not found")
        state, attempts, max_attempts, owner, lease_until = row
        if state != ExperimentState.LEASED.value or owner != worker_id or not lease_until:
            raise RuntimeError("worker does not own the active experiment lease")
        expiry = datetime.fromisoformat(lease_until)
        if expiry <= _utc(now):
            raise RuntimeError("experiment lease expired")
        return int(attempts), int(max_attempts), expiry

    def renew_lease(
        self,
        fingerprint: str,
        worker_id: str,
        *,
        now: datetime,
        lease_seconds: int = 300,
    ) -> datetime:
        if not 1 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        now_utc = _utc(now)
        new_until = now_utc + timedelta(seconds=lease_seconds)
        with self._write():
            attempt, _, current_until = self._active_lease_row(fingerprint, worker_id, now_utc)
            if new_until <= current_until:
                new_until = current_until + timedelta(seconds=lease_seconds)
            self._db.execute(
                "UPDATE experiment_jobs SET lease_until=?,updated_at=? WHERE fingerprint=?",
                (new_until.isoformat(), now_utc.isoformat(), fingerprint),
            )
            self._event(
                fingerprint,
                "LEASE_RENEWED",
                now_utc,
                {"worker_id": worker_id, "attempt": attempt, "lease_until": new_until.isoformat()},
            )
        return new_until

    def complete(
        self,
        fingerprint: str,
        worker_id: str,
        *,
        result_fingerprint: str,
        now: datetime,
    ) -> None:
        _sha(result_fingerprint, "experiment result")
        now_utc = _utc(now)
        with self._write():
            attempt, _, _ = self._active_lease_row(fingerprint, worker_id, now_utc)
            self._db.execute(
                "UPDATE experiment_jobs SET state=?,lease_owner=NULL,lease_until=NULL,result_fingerprint=?,"
                "last_error='',updated_at=? WHERE fingerprint=?",
                (
                    ExperimentState.SUCCEEDED.value,
                    result_fingerprint.lower(),
                    now_utc.isoformat(),
                    fingerprint,
                ),
            )
            self._event(
                fingerprint,
                "SUCCEEDED",
                now_utc,
                {"worker_id": worker_id, "attempt": attempt, "result_fingerprint": result_fingerprint.lower()},
            )

    def fail(
        self,
        fingerprint: str,
        worker_id: str,
        *,
        error: str,
        now: datetime,
        retryable: bool = True,
        retry_delay_seconds: int = 0,
    ) -> ExperimentState:
        if not 0 <= retry_delay_seconds <= 86400:
            raise ValueError("retry_delay_seconds must be between 0 and 86400")
        rendered_error = " ".join(error.strip().split())[:1000]
        if not rendered_error:
            raise ValueError("experiment failure requires an error")
        now_utc = _utc(now)
        with self._write():
            attempt, max_attempts, _ = self._active_lease_row(fingerprint, worker_id, now_utc)
            should_retry = retryable and attempt < max_attempts
            state = ExperimentState.QUEUED if should_retry else ExperimentState.FAILED
            available_at = now_utc + timedelta(seconds=retry_delay_seconds) if should_retry else now_utc
            self._db.execute(
                "UPDATE experiment_jobs SET state=?,available_at=?,lease_owner=NULL,lease_until=NULL,last_error=?,updated_at=? "
                "WHERE fingerprint=?",
                (
                    state.value,
                    available_at.isoformat(),
                    rendered_error,
                    now_utc.isoformat(),
                    fingerprint,
                ),
            )
            self._event(
                fingerprint,
                "RETRY_SCHEDULED" if should_retry else "FAILED",
                now_utc,
                {
                    "worker_id": worker_id,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    "retryable": retryable,
                    "available_at": available_at.isoformat(),
                    "error": rendered_error,
                },
            )
        return state

    def snapshot(self, fingerprint: str) -> ExperimentSnapshot | None:
        _sha(fingerprint, "experiment job")
        row = self._db.execute(
            "SELECT payload,state,attempt_count,available_at,lease_owner,lease_until,result_fingerprint,last_error,"
            "created_at,updated_at FROM experiment_jobs WHERE fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if row is None:
            return None
        payload, state, attempts, available_at, owner, lease_until, result, error, created, updated = row
        return ExperimentSnapshot(
            job_fingerprint=fingerprint,
            spec=ExperimentJobSpec.from_payload(json.loads(payload)),
            state=ExperimentState(state),
            attempt_count=int(attempts),
            available_at=datetime.fromisoformat(available_at),
            lease_owner=owner,
            lease_until=datetime.fromisoformat(lease_until) if lease_until else None,
            result_fingerprint=result,
            last_error=error,
            created_at=datetime.fromisoformat(created),
            updated_at=datetime.fromisoformat(updated),
        )

    def history(self, fingerprint: str) -> tuple[ExperimentEvent, ...]:
        _sha(fingerprint, "experiment job")
        rows = self._db.execute(
            "SELECT seq,event_type,event_at,event_fingerprint,details FROM experiment_events "
            "WHERE job_fingerprint=? ORDER BY seq",
            (fingerprint,),
        ).fetchall()
        return tuple(
            ExperimentEvent(
                sequence=int(seq),
                job_fingerprint=fingerprint,
                event_type=event_type,
                event_at=datetime.fromisoformat(event_at),
                event_fingerprint=event_fingerprint,
                details=json.loads(details),
            )
            for seq, event_type, event_at, event_fingerprint, details in rows
        )

    def counts(self) -> dict[ExperimentState, int]:
        result = {state: 0 for state in ExperimentState}
        for state, count in self._db.execute("SELECT state,COUNT(*) FROM experiment_jobs GROUP BY state"):
            result[ExperimentState(state)] = int(count)
        return result

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def checkpoint_wal(self) -> tuple[int, int, int]:
        row = self._db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return int(row[0]), int(row[1]), int(row[2])

    def close(self) -> None:
        self._db.close()
