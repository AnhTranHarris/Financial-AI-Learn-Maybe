from __future__ import annotations

"""M195 crash-safe master portfolio risk governor.

The governor turns Dusty's existing per-trade risk constitution into one shared,
atomic capital-risk budget across strategies and symbols.  It does not allocate
alpha, estimate correlation, size broker lots, send orders, or grant execution
or Guardian authority.  Reserved and committed planned loss remain charged
until explicit evidence releases them.
"""

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable

from .order_intent import BrokerPreflight, OrderIntent
from .risk import AccountRiskSnapshot, RiskConstitution, RiskState, TradeRiskRequest, assess_trade_risk


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires 40- or 64-character hexadecimal identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite_nonnegative(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return rendered


def _nonnegative_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return int(value)


def _text(value: str, label: str, *, maximum: int = 128) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _evidence(values: Iterable[str], label: str) -> tuple[str, ...]:
    rows = tuple(sorted(_sha(value, label) for value in values))
    if not rows or len(rows) != len(set(rows)):
        raise ValueError(f"{label} must be unique and nonempty")
    return rows


def _constitution_payload(constitution: RiskConstitution) -> tuple[tuple[str, float], ...]:
    return tuple((field.name, float(getattr(constitution, field.name))) for field in fields(constitution))


def _preflight_fingerprint(preflight: BrokerPreflight) -> str:
    return _digest((
        "dusty-m195-broker-preflight-snapshot-v1",
        preflight.intent.intent_hash,
        preflight.passed,
        float(preflight.loss_at_stop),
        float(preflight.required_margin),
        float(preflight.checked_price),
        preflight.request,
        preflight.reasons,
    ))


class RiskReservationState(StrEnum):
    RESERVED = "reserved"
    COMMITTED = "committed"
    RELEASED = "released"


class RiskReservationDecision(StrEnum):
    APPROVED = "approved"
    EXISTING = "existing"
    DENIED = "denied"


@dataclass(frozen=True, slots=True)
class PortfolioRiskGovernorPolicy:
    constitution: RiskConstitution = RiskConstitution()
    max_snapshot_age_seconds: int = 30
    busy_timeout_ms: int = 5000

    def __post_init__(self) -> None:
        if isinstance(self.max_snapshot_age_seconds, bool) or int(self.max_snapshot_age_seconds) < 1:
            raise ValueError("max_snapshot_age_seconds must be a positive integer")
        if isinstance(self.busy_timeout_ms, bool) or not 1 <= int(self.busy_timeout_ms) <= 60000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000")
        object.__setattr__(self, "max_snapshot_age_seconds", int(self.max_snapshot_age_seconds))
        object.__setattr__(self, "busy_timeout_ms", int(self.busy_timeout_ms))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m195-portfolio-risk-governor-policy-v1",
            _constitution_payload(self.constitution),
            self.max_snapshot_age_seconds,
            self.busy_timeout_ms,
        ))


@dataclass(frozen=True, slots=True)
class PortfolioCapitalSnapshot:
    account_fingerprint: str
    session_fingerprint: str
    source_commit: str
    captured_at: datetime
    equity: float
    balance: float
    high_water_mark: float
    day_start_equity: float
    week_start_equity: float
    margin_used: float
    free_margin: float
    broker_positions_fingerprint: str
    broker_orders_fingerprint: str
    complete_exposure_data: bool
    unexplained_position_count: int = 0
    unexplained_order_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_fingerprint", _sha(self.account_fingerprint, "capital account"))
        object.__setattr__(self, "session_fingerprint", _sha(self.session_fingerprint, "capital session"))
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "capital source commit"))
        object.__setattr__(self, "captured_at", _aware(self.captured_at, "capital captured_at"))
        for name in ("equity", "balance", "margin_used", "free_margin"):
            object.__setattr__(self, name, _finite_nonnegative(getattr(self, name), name))
        for name in ("high_water_mark", "day_start_equity", "week_start_equity"):
            value = _finite_nonnegative(getattr(self, name), name)
            if value <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, value)
        if self.high_water_mark + 1e-12 < self.equity:
            raise ValueError("high_water_mark cannot be below equity")
        object.__setattr__(self, "broker_positions_fingerprint", _sha(self.broker_positions_fingerprint, "broker positions"))
        object.__setattr__(self, "broker_orders_fingerprint", _sha(self.broker_orders_fingerprint, "broker orders"))
        if not isinstance(self.complete_exposure_data, bool):
            raise ValueError("complete_exposure_data must be boolean")
        object.__setattr__(self, "unexplained_position_count", _nonnegative_int(self.unexplained_position_count, "unexplained positions"))
        object.__setattr__(self, "unexplained_order_count", _nonnegative_int(self.unexplained_order_count, "unexplained orders"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m195-portfolio-capital-snapshot-v1",
            self.account_fingerprint,
            self.session_fingerprint,
            self.source_commit,
            self.captured_at.isoformat(),
            self.equity,
            self.balance,
            self.high_water_mark,
            self.day_start_equity,
            self.week_start_equity,
            self.margin_used,
            self.free_margin,
            self.broker_positions_fingerprint,
            self.broker_orders_fingerprint,
            self.complete_exposure_data,
            self.unexplained_position_count,
            self.unexplained_order_count,
        ))


@dataclass(frozen=True, slots=True)
class TerminalRiskReleaseEvidence:
    intent_hash: str
    account_fingerprint: str
    captured_at: datetime
    reconciliation_fingerprint: str
    broker_positions_fingerprint: str
    broker_orders_fingerprint: str
    position_absent_or_closed: bool
    pending_order_absent: bool
    evidence_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_hash", _sha(self.intent_hash, "release intent"))
        object.__setattr__(self, "account_fingerprint", _sha(self.account_fingerprint, "release account"))
        object.__setattr__(self, "captured_at", _aware(self.captured_at, "release captured_at"))
        object.__setattr__(self, "reconciliation_fingerprint", _sha(self.reconciliation_fingerprint, "release reconciliation"))
        object.__setattr__(self, "broker_positions_fingerprint", _sha(self.broker_positions_fingerprint, "release positions"))
        object.__setattr__(self, "broker_orders_fingerprint", _sha(self.broker_orders_fingerprint, "release orders"))
        if not isinstance(self.position_absent_or_closed, bool) or not isinstance(self.pending_order_absent, bool):
            raise ValueError("release terminal-state flags must be boolean")
        object.__setattr__(self, "evidence_fingerprints", _evidence(self.evidence_fingerprints, "release evidence"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m195-terminal-risk-release-v1",
            self.intent_hash,
            self.account_fingerprint,
            self.captured_at.isoformat(),
            self.reconciliation_fingerprint,
            self.broker_positions_fingerprint,
            self.broker_orders_fingerprint,
            self.position_absent_or_closed,
            self.pending_order_absent,
            self.evidence_fingerprints,
        ))


@dataclass(frozen=True, slots=True)
class RiskReservationRecord:
    record_fingerprint: str
    intent_hash: str
    strategy_hash: str
    session_fingerprint: str
    symbol: str
    account_fingerprint: str
    source_commit: str
    capital_snapshot_fingerprint: str
    preflight_fingerprint: str
    policy_fingerprint: str
    reserved_loss: float
    required_margin: float
    created_at: datetime
    evidence_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_hash", _sha(self.intent_hash, "reservation intent"))
        object.__setattr__(self, "strategy_hash", _sha(self.strategy_hash, "reservation strategy"))
        object.__setattr__(self, "session_fingerprint", _sha(self.session_fingerprint, "reservation session"))
        object.__setattr__(self, "symbol", _text(self.symbol, "reservation symbol", maximum=64).upper())
        object.__setattr__(self, "account_fingerprint", _sha(self.account_fingerprint, "reservation account"))
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "reservation source commit"))
        object.__setattr__(self, "capital_snapshot_fingerprint", _sha(self.capital_snapshot_fingerprint, "reservation capital snapshot"))
        object.__setattr__(self, "preflight_fingerprint", _sha(self.preflight_fingerprint, "reservation preflight"))
        object.__setattr__(self, "policy_fingerprint", _sha(self.policy_fingerprint, "reservation policy"))
        object.__setattr__(self, "reserved_loss", _finite_nonnegative(self.reserved_loss, "reserved_loss"))
        if self.reserved_loss <= 0:
            raise ValueError("reserved_loss must be positive")
        object.__setattr__(self, "required_margin", _finite_nonnegative(self.required_margin, "required_margin"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "reservation created_at"))
        object.__setattr__(self, "evidence_fingerprints", _evidence(self.evidence_fingerprints, "reservation evidence"))
        object.__setattr__(self, "record_fingerprint", _sha(self.record_fingerprint, "reservation record"))
        if self.record_fingerprint != _digest(self.payload):
            raise ValueError("reservation record fingerprint mismatch")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m195-risk-reservation-record-v1",
            "intent_hash": self.intent_hash,
            "strategy_hash": self.strategy_hash,
            "session_fingerprint": self.session_fingerprint,
            "symbol": self.symbol,
            "account_fingerprint": self.account_fingerprint,
            "source_commit": self.source_commit,
            "capital_snapshot_fingerprint": self.capital_snapshot_fingerprint,
            "preflight_fingerprint": self.preflight_fingerprint,
            "policy_fingerprint": self.policy_fingerprint,
            "reserved_loss": self.reserved_loss,
            "required_margin": self.required_margin,
            "created_at": self.created_at.isoformat(),
            "evidence_fingerprints": list(self.evidence_fingerprints),
        }


@dataclass(frozen=True, slots=True)
class RiskReservationEvent:
    event_fingerprint: str
    intent_hash: str
    state: RiskReservationState
    created_at: datetime
    previous_event_fingerprint: str | None
    evidence_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_fingerprint", _sha(self.event_fingerprint, "reservation event"))
        object.__setattr__(self, "intent_hash", _sha(self.intent_hash, "event intent"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "event created_at"))
        if self.previous_event_fingerprint is not None:
            object.__setattr__(self, "previous_event_fingerprint", _sha(self.previous_event_fingerprint, "previous event"))
        object.__setattr__(self, "evidence_fingerprints", _evidence(self.evidence_fingerprints, "event evidence"))
        if self.event_fingerprint != _digest(self.payload):
            raise ValueError("reservation event fingerprint mismatch")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m195-risk-reservation-event-v1",
            "intent_hash": self.intent_hash,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "previous_event_fingerprint": self.previous_event_fingerprint,
            "evidence_fingerprints": list(self.evidence_fingerprints),
        }


@dataclass(frozen=True, slots=True)
class PortfolioRiskDecision:
    decision: RiskReservationDecision
    intent_hash: str
    account_fingerprint: str
    reserved_loss: float
    active_loss_before: float
    current_equity: float
    post_trade_portfolio_heat: float
    post_trade_same_symbol_heat: float
    post_trade_margin_fraction: float
    risk_state: RiskState
    reasons: tuple[str, ...]
    record_fingerprint: str | None = None
    event_fingerprint: str | None = None

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


class SQLitePortfolioRiskGovernor:
    """One durable master risk book per account fingerprint.

    `BEGIN IMMEDIATE` makes the read-current-heat/insert-reservation sequence a
    single-writer operation across independent processes using the same DB file.
    """

    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        source_commit: str,
        policy: PortfolioRiskGovernorPolicy = PortfolioRiskGovernorPolicy(),
    ) -> None:
        self.source_commit = _git_sha(source_commit, "governor source commit")
        self.policy = policy
        self._db = sqlite3.connect(
            str(path),
            timeout=policy.busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(f"PRAGMA busy_timeout={policy.busy_timeout_ms}")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS portfolio_risk_records("
            "intent_hash TEXT PRIMARY KEY,record_fingerprint TEXT NOT NULL UNIQUE,"
            "strategy_hash TEXT NOT NULL,session_fingerprint TEXT NOT NULL,symbol TEXT NOT NULL,"
            "account_fingerprint TEXT NOT NULL,source_commit TEXT NOT NULL,capital_snapshot_fingerprint TEXT NOT NULL,"
            "preflight_fingerprint TEXT NOT NULL,policy_fingerprint TEXT NOT NULL,reserved_loss REAL NOT NULL,"
            "required_margin REAL NOT NULL,created_at TEXT NOT NULL,evidence_fingerprints TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS portfolio_risk_events("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,event_fingerprint TEXT NOT NULL UNIQUE,"
            "intent_hash TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,"
            "previous_event_fingerprint TEXT,evidence_fingerprints TEXT NOT NULL,"
            "FOREIGN KEY(intent_hash) REFERENCES portfolio_risk_records(intent_hash))"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_portfolio_risk_account ON portfolio_risk_records(account_fingerprint,intent_hash)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_portfolio_risk_events_intent ON portfolio_risk_events(intent_hash,seq)"
        )

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def live_write_authorized(self) -> bool:
        return False

    @property
    def risk_override_authorized(self) -> bool:
        return False

    def close(self) -> None:
        self._db.close()

    def _begin_write(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def _commit_write(self) -> None:
        self._db.execute("COMMIT")

    def _rollback_write(self) -> None:
        self._db.execute("ROLLBACK")

    def _make_record(
        self,
        intent: OrderIntent,
        preflight: BrokerPreflight,
        snapshot: PortfolioCapitalSnapshot,
        *,
        now: datetime,
        evidence_fingerprints: tuple[str, ...],
    ) -> RiskReservationRecord:
        payload = {
            "protocol": "dusty-m195-risk-reservation-record-v1",
            "intent_hash": intent.intent_hash,
            "strategy_hash": _sha(intent.strategy_hash, "intent strategy"),
            "session_fingerprint": _sha(intent.session_fingerprint, "intent session"),
            "symbol": _text(intent.symbol, "intent symbol", maximum=64).upper(),
            "account_fingerprint": snapshot.account_fingerprint,
            "source_commit": self.source_commit,
            "capital_snapshot_fingerprint": snapshot.fingerprint,
            "preflight_fingerprint": _preflight_fingerprint(preflight),
            "policy_fingerprint": self.policy.fingerprint,
            "reserved_loss": float(intent.allowed_loss),
            "required_margin": float(preflight.required_margin),
            "created_at": now.isoformat(),
            "evidence_fingerprints": list(evidence_fingerprints),
        }
        return RiskReservationRecord(
            _digest(payload),
            intent.intent_hash,
            _sha(intent.strategy_hash, "intent strategy"),
            _sha(intent.session_fingerprint, "intent session"),
            intent.symbol,
            snapshot.account_fingerprint,
            self.source_commit,
            snapshot.fingerprint,
            _preflight_fingerprint(preflight),
            self.policy.fingerprint,
            intent.allowed_loss,
            preflight.required_margin,
            now,
            evidence_fingerprints,
        )

    def _make_event(
        self,
        intent_hash: str,
        state: RiskReservationState,
        *,
        now: datetime,
        previous: str | None,
        evidence_fingerprints: tuple[str, ...],
    ) -> RiskReservationEvent:
        payload = {
            "protocol": "dusty-m195-risk-reservation-event-v1",
            "intent_hash": _sha(intent_hash, "event intent"),
            "state": state.value,
            "created_at": now.isoformat(),
            "previous_event_fingerprint": previous,
            "evidence_fingerprints": list(evidence_fingerprints),
        }
        return RiskReservationEvent(
            _digest(payload), intent_hash, state, now, previous, evidence_fingerprints
        )

    def _insert_record(self, record: RiskReservationRecord) -> None:
        self._db.execute(
            "INSERT INTO portfolio_risk_records("
            "intent_hash,record_fingerprint,strategy_hash,session_fingerprint,symbol,account_fingerprint,"
            "source_commit,capital_snapshot_fingerprint,preflight_fingerprint,policy_fingerprint,reserved_loss,"
            "required_margin,created_at,evidence_fingerprints) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.intent_hash,
                record.record_fingerprint,
                record.strategy_hash,
                record.session_fingerprint,
                record.symbol,
                record.account_fingerprint,
                record.source_commit,
                record.capital_snapshot_fingerprint,
                record.preflight_fingerprint,
                record.policy_fingerprint,
                record.reserved_loss,
                record.required_margin,
                record.created_at.isoformat(),
                _canonical(list(record.evidence_fingerprints)),
            ),
        )

    def _insert_event(self, event: RiskReservationEvent) -> None:
        self._db.execute(
            "INSERT INTO portfolio_risk_events("
            "event_fingerprint,intent_hash,state,created_at,previous_event_fingerprint,evidence_fingerprints) "
            "VALUES(?,?,?,?,?,?)",
            (
                event.event_fingerprint,
                event.intent_hash,
                event.state.value,
                event.created_at.isoformat(),
                event.previous_event_fingerprint,
                _canonical(list(event.evidence_fingerprints)),
            ),
        )

    def _record(self, intent_hash: str) -> RiskReservationRecord | None:
        intent = _sha(intent_hash, "record lookup intent")
        row = self._db.execute(
            "SELECT record_fingerprint,strategy_hash,session_fingerprint,symbol,account_fingerprint,source_commit,"
            "capital_snapshot_fingerprint,preflight_fingerprint,policy_fingerprint,reserved_loss,required_margin,"
            "created_at,evidence_fingerprints FROM portfolio_risk_records WHERE intent_hash=?",
            (intent,),
        ).fetchone()
        if row is None:
            return None
        try:
            evidence_raw = json.loads(str(row[12]))
        except json.JSONDecodeError as exc:
            raise RuntimeError("portfolio risk record evidence JSON is corrupt") from exc
        if not isinstance(evidence_raw, list):
            raise RuntimeError("portfolio risk record evidence is corrupt")
        return RiskReservationRecord(
            str(row[0]), intent, str(row[1]), str(row[2]), str(row[3]), str(row[4]), str(row[5]),
            str(row[6]), str(row[7]), str(row[8]), float(row[9]), float(row[10]),
            datetime.fromisoformat(str(row[11])), tuple(str(value) for value in evidence_raw),
        )

    def _events(self, intent_hash: str) -> tuple[RiskReservationEvent, ...]:
        intent = _sha(intent_hash, "event lookup intent")
        rows = self._db.execute(
            "SELECT event_fingerprint,state,created_at,previous_event_fingerprint,evidence_fingerprints "
            "FROM portfolio_risk_events WHERE intent_hash=? ORDER BY seq",
            (intent,),
        ).fetchall()
        result: list[RiskReservationEvent] = []
        for row in rows:
            try:
                evidence_raw = json.loads(str(row[4]))
            except json.JSONDecodeError as exc:
                raise RuntimeError("portfolio risk event evidence JSON is corrupt") from exc
            if not isinstance(evidence_raw, list):
                raise RuntimeError("portfolio risk event evidence is corrupt")
            result.append(
                RiskReservationEvent(
                    str(row[0]), intent, RiskReservationState(str(row[1])), datetime.fromisoformat(str(row[2])),
                    None if row[3] is None else str(row[3]), tuple(str(value) for value in evidence_raw),
                )
            )
        return tuple(result)

    def state(self, intent_hash: str) -> RiskReservationState:
        events = self._events(intent_hash)
        if not events:
            raise KeyError(intent_hash)
        return events[-1].state

    def get_record(self, intent_hash: str) -> RiskReservationRecord | None:
        return self._record(intent_hash)

    def _active_rows(self, account_fingerprint: str) -> tuple[tuple[RiskReservationRecord, RiskReservationState], ...]:
        account = _sha(account_fingerprint, "active account")
        intents = self._db.execute(
            "SELECT intent_hash FROM portfolio_risk_records WHERE account_fingerprint=? ORDER BY intent_hash",
            (account,),
        ).fetchall()
        rows: list[tuple[RiskReservationRecord, RiskReservationState]] = []
        for (intent_hash,) in intents:
            record = self._record(str(intent_hash))
            if record is None:
                raise RuntimeError("portfolio risk record disappeared during read")
            events = self._events(record.intent_hash)
            if not events:
                raise RuntimeError("portfolio risk record has no lifecycle event")
            state = events[-1].state
            if state in {RiskReservationState.RESERVED, RiskReservationState.COMMITTED}:
                rows.append((record, state))
        return tuple(rows)

    def active_reserved_loss(self, account_fingerprint: str) -> float:
        return sum(record.reserved_loss for record, _ in self._active_rows(account_fingerprint))

    def _integrity_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        try:
            db_result = str(self._db.execute("PRAGMA integrity_check").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            return (f"sqlite:{type(exc).__name__}",)
        if db_result.lower() != "ok":
            errors.append(f"sqlite:{db_result}")
        intent_rows = self._db.execute("SELECT intent_hash FROM portfolio_risk_records ORDER BY intent_hash").fetchall()
        for (intent_hash,) in intent_rows:
            try:
                record = self._record(str(intent_hash))
                if record is None:
                    errors.append(f"missing_record:{intent_hash}")
                    continue
                events = self._events(record.intent_hash)
                if not events:
                    errors.append(f"missing_event:{record.intent_hash}")
                    continue
                previous: RiskReservationEvent | None = None
                for index, event in enumerate(events):
                    if index == 0:
                        if event.state is not RiskReservationState.RESERVED or event.previous_event_fingerprint is not None:
                            errors.append(f"invalid_initial_event:{record.intent_hash}")
                    else:
                        if previous is None or event.previous_event_fingerprint != previous.event_fingerprint:
                            errors.append(f"broken_event_chain:{record.intent_hash}")
                        allowed = {
                            RiskReservationState.RESERVED: {RiskReservationState.COMMITTED, RiskReservationState.RELEASED},
                            RiskReservationState.COMMITTED: {RiskReservationState.RELEASED},
                            RiskReservationState.RELEASED: set(),
                        }[previous.state]
                        if event.state not in allowed:
                            errors.append(f"illegal_event_transition:{record.intent_hash}")
                    previous = event
            except (ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
                errors.append(f"record:{intent_hash}:{type(exc).__name__}")
        orphan_count = int(self._db.execute(
            "SELECT COUNT(*) FROM portfolio_risk_events e LEFT JOIN portfolio_risk_records r "
            "ON e.intent_hash=r.intent_hash WHERE r.intent_hash IS NULL"
        ).fetchone()[0])
        if orphan_count:
            errors.append(f"orphan_events:{orphan_count}")
        return tuple(errors)

    def integrity_check(self) -> tuple[bool, tuple[str, ...]]:
        errors = self._integrity_errors()
        return (not errors, errors)

    def _denied(
        self,
        intent: OrderIntent,
        snapshot: PortfolioCapitalSnapshot,
        *,
        reserved_loss: float,
        active_loss: float,
        post_heat: float,
        same_symbol_heat: float,
        margin_fraction: float,
        risk_state: RiskState,
        reasons: Iterable[str],
    ) -> PortfolioRiskDecision:
        return PortfolioRiskDecision(
            RiskReservationDecision.DENIED,
            intent.intent_hash,
            snapshot.account_fingerprint,
            reserved_loss,
            active_loss,
            snapshot.equity,
            post_heat,
            same_symbol_heat,
            margin_fraction,
            risk_state,
            tuple(sorted(set(reasons))),
        )

    def reserve(
        self,
        intent: OrderIntent,
        preflight: BrokerPreflight,
        snapshot: PortfolioCapitalSnapshot,
        *,
        now: datetime,
        evidence_fingerprints: Iterable[str],
    ) -> PortfolioRiskDecision:
        moment = _aware(now, "reservation timestamp")
        evidence = _evidence(evidence_fingerprints, "reservation evidence")
        self._begin_write()
        try:
            integrity_errors = self._integrity_errors()
            if integrity_errors:
                raise RuntimeError("portfolio risk ledger integrity failure: " + ",".join(integrity_errors))

            existing = self._record(intent.intent_hash)
            if existing is not None:
                events = self._events(existing.intent_hash)
                if not events:
                    raise RuntimeError("existing reservation has no lifecycle event")
                if existing.account_fingerprint != snapshot.account_fingerprint:
                    raise ValueError("existing intent is bound to another account risk book")
                if existing.strategy_hash != _sha(intent.strategy_hash, "intent strategy") or existing.symbol != intent.symbol.upper():
                    raise ValueError("existing intent reservation identity mismatch")
                if existing.session_fingerprint != _sha(intent.session_fingerprint, "intent session"):
                    raise ValueError("existing intent reservation session mismatch")
                if events[-1].state is RiskReservationState.RELEASED:
                    raise ValueError("released intent reservation cannot be resurrected")
                active_rows = self._active_rows(snapshot.account_fingerprint)
                active_loss = sum(record.reserved_loss for record, _ in active_rows)
                same_loss = sum(record.reserved_loss for record, _ in active_rows if record.symbol == intent.symbol.upper())
                equity = snapshot.equity
                current_heat = math.inf if equity == 0 and active_loss > 0 else (active_loss / equity if equity > 0 else 0.0)
                same_heat = math.inf if equity == 0 and same_loss > 0 else (same_loss / equity if equity > 0 else 0.0)
                margin_fraction = math.inf if equity == 0 and snapshot.margin_used > 0 else (snapshot.margin_used / equity if equity > 0 else 0.0)
                self._commit_write()
                return PortfolioRiskDecision(
                    RiskReservationDecision.EXISTING,
                    existing.intent_hash,
                    existing.account_fingerprint,
                    existing.reserved_loss,
                    max(0.0, active_loss - existing.reserved_loss),
                    equity,
                    current_heat,
                    same_heat,
                    margin_fraction,
                    RiskState.NORMAL,
                    ("existing_active_reservation_reused",),
                    existing.record_fingerprint,
                    events[-1].event_fingerprint,
                )

            active_rows = self._active_rows(snapshot.account_fingerprint)
            active_loss = sum(record.reserved_loss for record, _ in active_rows)
            same_loss = sum(record.reserved_loss for record, _ in active_rows if record.symbol == intent.symbol.upper())
            reserved_loss = float(intent.allowed_loss)
            reasons: list[str] = []

            if snapshot.source_commit != self.source_commit:
                reasons.append("capital_snapshot_source_commit_mismatch")
            if moment < snapshot.captured_at:
                reasons.append("capital_snapshot_from_future")
            elif (moment - snapshot.captured_at).total_seconds() > self.policy.max_snapshot_age_seconds:
                reasons.append("capital_snapshot_stale")
            if not snapshot.complete_exposure_data:
                reasons.append("incomplete_broker_exposure_data")
            if snapshot.unexplained_position_count:
                reasons.append("unexplained_broker_positions")
            if snapshot.unexplained_order_count:
                reasons.append("unexplained_broker_orders")
            if intent.session_fingerprint != snapshot.session_fingerprint:
                reasons.append("intent_session_mismatch")
            if preflight.intent.intent_hash != intent.intent_hash:
                reasons.append("broker_preflight_intent_mismatch")
            if not preflight.passed or preflight.reasons:
                reasons.append("broker_preflight_not_passed")
            if not all((intent.pm_approved, intent.risk_approved, intent.guardian_approved)) or intent.growth_multiplier <= 0:
                reasons.append("intent_governance_not_approved")
            if not math.isfinite(reserved_loss) or reserved_loss <= 0:
                reasons.append("planned_loss_invalid")
            if not math.isfinite(preflight.required_margin) or preflight.required_margin < 0:
                reasons.append("required_margin_invalid")
            if not math.isfinite(preflight.loss_at_stop) or preflight.loss_at_stop < 0:
                reasons.append("broker_stop_loss_invalid")
            elif preflight.loss_at_stop > reserved_loss + 1e-9:
                reasons.append("broker_stop_loss_exceeds_intent_budget")

            equity = snapshot.equity
            post_loss = active_loss + max(0.0, reserved_loss if math.isfinite(reserved_loss) else 0.0)
            post_same_loss = same_loss + max(0.0, reserved_loss if math.isfinite(reserved_loss) else 0.0)
            post_heat = math.inf if equity == 0 and post_loss > 0 else (post_loss / equity if equity > 0 else 0.0)
            same_heat = math.inf if equity == 0 and post_same_loss > 0 else (post_same_loss / equity if equity > 0 else 0.0)
            proposed_risk = math.inf if equity == 0 and reserved_loss > 0 else (reserved_loss / equity if equity > 0 else 0.0)
            post_margin = snapshot.margin_used + max(0.0, preflight.required_margin if math.isfinite(preflight.required_margin) else 0.0)
            margin_fraction = math.inf if equity == 0 and post_margin > 0 else (post_margin / equity if equity > 0 else 0.0)

            static_snapshot = AccountRiskSnapshot(
                snapshot.equity,
                snapshot.balance,
                snapshot.high_water_mark,
                snapshot.day_start_equity,
                snapshot.week_start_equity,
                snapshot.margin_used,
                math.inf if not math.isfinite(active_loss / equity) else (active_loss / equity if equity > 0 else 0.0),
                math.inf if not math.isfinite(same_loss / equity) else (same_loss / equity if equity > 0 else 0.0),
            ) if equity > 0 else AccountRiskSnapshot(
                snapshot.equity,
                snapshot.balance,
                snapshot.high_water_mark,
                snapshot.day_start_equity,
                snapshot.week_start_equity,
                snapshot.margin_used,
                active_loss,
                same_loss,
            )
            risk_assessment = assess_trade_risk(
                static_snapshot,
                TradeRiskRequest(
                    proposed_risk=proposed_risk,
                    post_trade_portfolio_heat=post_heat,
                    post_trade_same_symbol_heat=same_heat,
                    post_trade_margin_used=post_margin,
                    has_initial_stop=True,
                    complete_risk_data=(
                        snapshot.complete_exposure_data
                        and snapshot.unexplained_position_count == 0
                        and snapshot.unexplained_order_count == 0
                    ),
                ),
                self.policy.constitution,
            )
            reasons.extend(risk_assessment.reasons)
            if snapshot.free_margin + 1e-9 < preflight.required_margin:
                reasons.append("broker_free_margin_insufficient")

            if reasons:
                self._commit_write()
                return self._denied(
                    intent,
                    snapshot,
                    reserved_loss=reserved_loss,
                    active_loss=active_loss,
                    post_heat=post_heat,
                    same_symbol_heat=same_heat,
                    margin_fraction=margin_fraction,
                    risk_state=risk_assessment.state,
                    reasons=reasons,
                )

            record = self._make_record(intent, preflight, snapshot, now=moment, evidence_fingerprints=evidence)
            event = self._make_event(
                intent.intent_hash,
                RiskReservationState.RESERVED,
                now=moment,
                previous=None,
                evidence_fingerprints=(record.record_fingerprint, *evidence),
            )
            self._insert_record(record)
            self._insert_event(event)
            self._commit_write()
            return PortfolioRiskDecision(
                RiskReservationDecision.APPROVED,
                intent.intent_hash,
                snapshot.account_fingerprint,
                record.reserved_loss,
                active_loss,
                snapshot.equity,
                post_heat,
                same_heat,
                margin_fraction,
                risk_assessment.state,
                (),
                record.record_fingerprint,
                event.event_fingerprint,
            )
        except Exception:
            try:
                self._rollback_write()
            except sqlite3.DatabaseError:
                pass
            raise

    def _transition(
        self,
        intent_hash: str,
        target: RiskReservationState,
        *,
        now: datetime,
        evidence_fingerprints: Iterable[str],
    ) -> RiskReservationEvent:
        moment = _aware(now, "reservation transition timestamp")
        evidence = _evidence(evidence_fingerprints, "reservation transition evidence")
        self._begin_write()
        try:
            errors = self._integrity_errors()
            if errors:
                raise RuntimeError("portfolio risk ledger integrity failure: " + ",".join(errors))
            record = self._record(intent_hash)
            if record is None:
                raise KeyError(intent_hash)
            events = self._events(record.intent_hash)
            if not events:
                raise RuntimeError("reservation missing lifecycle event")
            current = events[-1]
            if target is current.state:
                self._commit_write()
                return current
            allowed = {
                RiskReservationState.RESERVED: {RiskReservationState.COMMITTED, RiskReservationState.RELEASED},
                RiskReservationState.COMMITTED: {RiskReservationState.RELEASED},
                RiskReservationState.RELEASED: set(),
            }[current.state]
            if target not in allowed:
                raise ValueError(f"illegal risk reservation transition: {current.state.value}->{target.value}")
            if moment < current.created_at:
                raise ValueError("reservation transition cannot predate current lifecycle event")
            event = self._make_event(
                record.intent_hash,
                target,
                now=moment,
                previous=current.event_fingerprint,
                evidence_fingerprints=(record.record_fingerprint, *evidence),
            )
            self._insert_event(event)
            self._commit_write()
            return event
        except Exception:
            try:
                self._rollback_write()
            except sqlite3.DatabaseError:
                pass
            raise

    def commit(self, intent_hash: str, *, now: datetime, evidence_fingerprints: Iterable[str]) -> RiskReservationEvent:
        """Mark broker exposure as possible/real; no broker call is performed here."""
        return self._transition(
            intent_hash,
            RiskReservationState.COMMITTED,
            now=now,
            evidence_fingerprints=evidence_fingerprints,
        )

    def release_pre_send(
        self,
        intent_hash: str,
        *,
        now: datetime,
        no_send_evidence_fingerprints: Iterable[str],
    ) -> RiskReservationEvent:
        """Release only a never-sent reservation with explicit no-send evidence."""
        if self.state(intent_hash) is not RiskReservationState.RESERVED:
            raise ValueError("pre-send release requires RESERVED state")
        return self._transition(
            intent_hash,
            RiskReservationState.RELEASED,
            now=now,
            evidence_fingerprints=no_send_evidence_fingerprints,
        )

    def release_terminal(self, release: TerminalRiskReleaseEvidence) -> RiskReservationEvent:
        """Release committed heat only after broker/reconciliation evidence proves no exposure remains."""
        record = self._record(release.intent_hash)
        if record is None:
            raise KeyError(release.intent_hash)
        if record.account_fingerprint != release.account_fingerprint:
            raise ValueError("terminal release account identity mismatch")
        state = self.state(release.intent_hash)
        if state is RiskReservationState.RELEASED:
            return self._events(release.intent_hash)[-1]
        if state is not RiskReservationState.COMMITTED:
            raise ValueError("terminal release requires COMMITTED state")
        if not release.position_absent_or_closed or not release.pending_order_absent:
            raise ValueError("terminal release requires broker proof of no remaining position or pending order")
        return self._transition(
            release.intent_hash,
            RiskReservationState.RELEASED,
            now=release.captured_at,
            evidence_fingerprints=(release.fingerprint, *release.evidence_fingerprints),
        )
