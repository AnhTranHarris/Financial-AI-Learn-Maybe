from __future__ import annotations

"""M195 crash-safe master portfolio risk governor.

M195 turns Dusty's existing per-trade RiskConstitution into one shared,
transactional risk book across strategies and symbols.  It does not allocate
alpha, estimate correlation, size broker lots, send orders, or grant execution,
Guardian, promotion, or risk-override authority.

Planned loss is charged when reserved and remains charged through ambiguous or
committed execution until explicit evidence releases it.  Broker account/margin
state is an additional constraint; it never expands Dusty's internal risk budget.
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
from .risk import (
    AccountRiskSnapshot,
    RiskConstitution,
    RiskState,
    TradeRiskRequest,
    assess_trade_risk,
    risk_state,
)


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


def _constitution_payload(value: RiskConstitution) -> tuple[tuple[str, float], ...]:
    return tuple((field.name, float(getattr(value, field.name))) for field in fields(value))


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
            rendered = _finite_nonnegative(getattr(self, name), name)
            if rendered <= 0:
                raise ValueError(f"{name} must be positive")
            object.__setattr__(self, name, rendered)
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


@dataclass(frozen=True, slots=True)
class RiskLifecycleReceipt:
    intent_hash: str
    state: RiskReservationState
    event_fingerprint: str
    created_at: datetime

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False


class SQLitePortfolioRiskGovernor:
    """Durable account-level risk book with atomic compare-and-reserve semantics."""

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

    @property
    def guardian_override_authorized(self) -> bool:
        return False

    def close(self) -> None:
        self._db.close()

    def _begin(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._db.execute("COMMIT")

    def _rollback(self) -> None:
        self._db.execute("ROLLBACK")

    def _record_row(self, intent_hash: str) -> tuple[object, ...] | None:
        intent = _sha(intent_hash, "record lookup intent")
        return self._db.execute(
            "SELECT intent_hash,record_fingerprint,strategy_hash,session_fingerprint,symbol,account_fingerprint,"
            "source_commit,capital_snapshot_fingerprint,preflight_fingerprint,policy_fingerprint,reserved_loss,"
            "required_margin,created_at,evidence_fingerprints FROM portfolio_risk_records WHERE intent_hash=?",
            (intent,),
        ).fetchone()

    def _event_rows(self, intent_hash: str) -> tuple[tuple[object, ...], ...]:
        intent = _sha(intent_hash, "event lookup intent")
        return tuple(self._db.execute(
            "SELECT event_fingerprint,state,created_at,previous_event_fingerprint,evidence_fingerprints "
            "FROM portfolio_risk_events WHERE intent_hash=? ORDER BY seq",
            (intent,),
        ).fetchall())

    @staticmethod
    def _decode_evidence(raw: object, label: str) -> tuple[str, ...]:
        try:
            values = json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} evidence JSON is corrupt") from exc
        if not isinstance(values, list):
            raise RuntimeError(f"{label} evidence JSON is corrupt")
        return tuple(str(value) for value in values)

    def _record_payload_from_row(self, row: tuple[object, ...]) -> dict[str, object]:
        evidence = self._decode_evidence(row[13], "record")
        return {
            "protocol": "dusty-m195-risk-reservation-record-v1",
            "intent_hash": str(row[0]),
            "strategy_hash": str(row[2]),
            "session_fingerprint": str(row[3]),
            "symbol": str(row[4]),
            "account_fingerprint": str(row[5]),
            "source_commit": str(row[6]),
            "capital_snapshot_fingerprint": str(row[7]),
            "preflight_fingerprint": str(row[8]),
            "policy_fingerprint": str(row[9]),
            "reserved_loss": float(row[10]),
            "required_margin": float(row[11]),
            "created_at": str(row[12]),
            "evidence_fingerprints": list(evidence),
        }

    def _event_payload_from_row(self, intent_hash: str, row: tuple[object, ...]) -> dict[str, object]:
        evidence = self._decode_evidence(row[4], "event")
        return {
            "protocol": "dusty-m195-risk-reservation-event-v1",
            "intent_hash": intent_hash,
            "state": str(row[1]),
            "created_at": str(row[2]),
            "previous_event_fingerprint": None if row[3] is None else str(row[3]),
            "evidence_fingerprints": list(evidence),
        }

    def _integrity_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        try:
            db_result = str(self._db.execute("PRAGMA integrity_check").fetchone()[0])
        except sqlite3.DatabaseError as exc:
            return (f"sqlite:{type(exc).__name__}",)
        if db_result.lower() != "ok":
            errors.append(f"sqlite:{db_result}")
        rows = self._db.execute("SELECT intent_hash FROM portfolio_risk_records ORDER BY intent_hash").fetchall()
        for (intent_hash_raw,) in rows:
            intent_hash = str(intent_hash_raw)
            try:
                record = self._record_row(intent_hash)
                if record is None:
                    errors.append(f"missing_record:{intent_hash}")
                    continue
                if _sha(intent_hash, "stored intent") != intent_hash:
                    errors.append(f"invalid_intent_identity:{intent_hash}")
                payload = self._record_payload_from_row(record)
                if _digest(payload) != str(record[1]):
                    errors.append(f"record_fingerprint:{intent_hash}")
                events = self._event_rows(intent_hash)
                if not events:
                    errors.append(f"missing_event:{intent_hash}")
                    continue
                previous_fingerprint: str | None = None
                previous_state: RiskReservationState | None = None
                for index, event in enumerate(events):
                    payload = self._event_payload_from_row(intent_hash, event)
                    if _digest(payload) != str(event[0]):
                        errors.append(f"event_fingerprint:{intent_hash}:{index}")
                    state = RiskReservationState(str(event[1]))
                    previous = None if event[3] is None else str(event[3])
                    if index == 0:
                        if state is not RiskReservationState.RESERVED or previous is not None:
                            errors.append(f"invalid_initial_event:{intent_hash}")
                    else:
                        if previous != previous_fingerprint:
                            errors.append(f"broken_event_chain:{intent_hash}:{index}")
                        allowed = {
                            RiskReservationState.RESERVED: {RiskReservationState.COMMITTED, RiskReservationState.RELEASED},
                            RiskReservationState.COMMITTED: {RiskReservationState.RELEASED},
                            RiskReservationState.RELEASED: set(),
                        }[previous_state]  # type: ignore[index]
                        if state not in allowed:
                            errors.append(f"illegal_event_transition:{intent_hash}:{index}")
                    previous_fingerprint = str(event[0])
                    previous_state = state
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

    def state(self, intent_hash: str) -> RiskReservationState:
        events = self._event_rows(intent_hash)
        if not events:
            raise KeyError(intent_hash)
        return RiskReservationState(str(events[-1][1]))

    def _active_rows(self, account_fingerprint: str) -> tuple[tuple[tuple[object, ...], RiskReservationState], ...]:
        account = _sha(account_fingerprint, "active account")
        intents = self._db.execute(
            "SELECT intent_hash FROM portfolio_risk_records WHERE account_fingerprint=? ORDER BY intent_hash",
            (account,),
        ).fetchall()
        result: list[tuple[tuple[object, ...], RiskReservationState]] = []
        for (intent_hash_raw,) in intents:
            intent_hash = str(intent_hash_raw)
            record = self._record_row(intent_hash)
            events = self._event_rows(intent_hash)
            if record is None or not events:
                raise RuntimeError("portfolio risk lifecycle is incomplete")
            current = RiskReservationState(str(events[-1][1]))
            if current in {RiskReservationState.RESERVED, RiskReservationState.COMMITTED}:
                result.append((record, current))
        return tuple(result)

    def active_reserved_loss(self, account_fingerprint: str) -> float:
        return sum(float(record[10]) for record, _ in self._active_rows(account_fingerprint))

    def _account_snapshot(self, snapshot: PortfolioCapitalSnapshot, active_loss: float, same_loss: float) -> AccountRiskSnapshot:
        equity = snapshot.equity
        portfolio_heat = active_loss / equity if equity > 0 else active_loss
        same_heat = same_loss / equity if equity > 0 else same_loss
        return AccountRiskSnapshot(
            snapshot.equity,
            snapshot.balance,
            snapshot.high_water_mark,
            snapshot.day_start_equity,
            snapshot.week_start_equity,
            snapshot.margin_used,
            portfolio_heat,
            same_heat,
        )

    def _decision(
        self,
        decision: RiskReservationDecision,
        intent: OrderIntent,
        snapshot: PortfolioCapitalSnapshot,
        *,
        reserved_loss: float,
        active_loss: float,
        same_loss_after: float,
        post_margin: float,
        risk_state_value: RiskState,
        reasons: Iterable[str] = (),
        record_fingerprint: str | None = None,
        event_fingerprint: str | None = None,
    ) -> PortfolioRiskDecision:
        equity = snapshot.equity
        post_loss = active_loss + (reserved_loss if decision is not RiskReservationDecision.EXISTING else 0.0)
        post_heat = math.inf if equity == 0 and post_loss > 0 else (post_loss / equity if equity > 0 else 0.0)
        same_heat = math.inf if equity == 0 and same_loss_after > 0 else (same_loss_after / equity if equity > 0 else 0.0)
        margin_fraction = math.inf if equity == 0 and post_margin > 0 else (post_margin / equity if equity > 0 else 0.0)
        return PortfolioRiskDecision(
            decision,
            intent.intent_hash,
            snapshot.account_fingerprint,
            reserved_loss,
            active_loss,
            snapshot.equity,
            post_heat,
            same_heat,
            margin_fraction,
            risk_state_value,
            tuple(sorted(set(reasons))),
            record_fingerprint,
            event_fingerprint,
        )

    def _insert_record_and_initial_event(
        self,
        intent: OrderIntent,
        preflight: BrokerPreflight,
        snapshot: PortfolioCapitalSnapshot,
        *,
        now: datetime,
        evidence: tuple[str, ...],
    ) -> tuple[str, str]:
        strategy = _sha(intent.strategy_hash, "intent strategy")
        session = _sha(intent.session_fingerprint, "intent session")
        symbol = _text(intent.symbol, "intent symbol", maximum=64).upper()
        record_payload = {
            "protocol": "dusty-m195-risk-reservation-record-v1",
            "intent_hash": intent.intent_hash,
            "strategy_hash": strategy,
            "session_fingerprint": session,
            "symbol": symbol,
            "account_fingerprint": snapshot.account_fingerprint,
            "source_commit": self.source_commit,
            "capital_snapshot_fingerprint": snapshot.fingerprint,
            "preflight_fingerprint": _preflight_fingerprint(preflight),
            "policy_fingerprint": self.policy.fingerprint,
            "reserved_loss": float(intent.allowed_loss),
            "required_margin": float(preflight.required_margin),
            "created_at": now.isoformat(),
            "evidence_fingerprints": list(evidence),
        }
        record_fingerprint = _digest(record_payload)
        self._db.execute(
            "INSERT INTO portfolio_risk_records("
            "intent_hash,record_fingerprint,strategy_hash,session_fingerprint,symbol,account_fingerprint,"
            "source_commit,capital_snapshot_fingerprint,preflight_fingerprint,policy_fingerprint,reserved_loss,"
            "required_margin,created_at,evidence_fingerprints) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                intent.intent_hash,
                record_fingerprint,
                strategy,
                session,
                symbol,
                snapshot.account_fingerprint,
                self.source_commit,
                snapshot.fingerprint,
                _preflight_fingerprint(preflight),
                self.policy.fingerprint,
                intent.allowed_loss,
                preflight.required_margin,
                now.isoformat(),
                _canonical(list(evidence)),
            ),
        )
        event_evidence = tuple(sorted({record_fingerprint, *evidence}))
        event_payload = {
            "protocol": "dusty-m195-risk-reservation-event-v1",
            "intent_hash": intent.intent_hash,
            "state": RiskReservationState.RESERVED.value,
            "created_at": now.isoformat(),
            "previous_event_fingerprint": None,
            "evidence_fingerprints": list(event_evidence),
        }
        event_fingerprint = _digest(event_payload)
        self._db.execute(
            "INSERT INTO portfolio_risk_events("
            "event_fingerprint,intent_hash,state,created_at,previous_event_fingerprint,evidence_fingerprints) "
            "VALUES(?,?,?,?,?,?)",
            (event_fingerprint, intent.intent_hash, RiskReservationState.RESERVED.value, now.isoformat(), None, _canonical(list(event_evidence))),
        )
        return record_fingerprint, event_fingerprint

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
        self._begin()
        try:
            errors = self._integrity_errors()
            if errors:
                raise RuntimeError("portfolio risk ledger integrity failure: " + ",".join(errors))

            existing = self._record_row(intent.intent_hash)
            if existing is not None:
                events = self._event_rows(intent.intent_hash)
                if not events:
                    raise RuntimeError("existing reservation has no lifecycle event")
                if str(existing[5]) != snapshot.account_fingerprint:
                    raise ValueError("existing intent is bound to another account risk book")
                if str(existing[2]) != _sha(intent.strategy_hash, "intent strategy") or str(existing[4]) != intent.symbol.upper():
                    raise ValueError("existing intent reservation identity mismatch")
                if str(existing[3]) != _sha(intent.session_fingerprint, "intent session"):
                    raise ValueError("existing intent reservation session mismatch")
                current = RiskReservationState(str(events[-1][1]))
                if current is RiskReservationState.RELEASED:
                    raise ValueError("released intent reservation cannot be resurrected")
                active_rows = self._active_rows(snapshot.account_fingerprint)
                total = sum(float(row[10]) for row, _ in active_rows)
                same = sum(float(row[10]) for row, _ in active_rows if str(row[4]) == intent.symbol.upper())
                state_value = risk_state(self._account_snapshot(snapshot, total, same), self.policy.constitution)
                self._commit()
                equity = snapshot.equity
                margin_fraction = math.inf if equity == 0 and snapshot.margin_used > 0 else (snapshot.margin_used / equity if equity > 0 else 0.0)
                return PortfolioRiskDecision(
                    RiskReservationDecision.EXISTING,
                    intent.intent_hash,
                    snapshot.account_fingerprint,
                    float(existing[10]),
                    max(0.0, total - float(existing[10])),
                    equity,
                    math.inf if equity == 0 and total > 0 else (total / equity if equity > 0 else 0.0),
                    math.inf if equity == 0 and same > 0 else (same / equity if equity > 0 else 0.0),
                    margin_fraction,
                    state_value,
                    ("existing_active_reservation_reused",),
                    str(existing[1]),
                    str(events[-1][0]),
                )

            active_rows = self._active_rows(snapshot.account_fingerprint)
            active_loss = sum(float(row[10]) for row, _ in active_rows)
            same_loss = sum(float(row[10]) for row, _ in active_rows if str(row[4]) == intent.symbol.upper())
            reserved_loss = float(intent.allowed_loss)
            required_margin = float(preflight.required_margin)
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
            if not math.isfinite(required_margin) or required_margin < 0:
                reasons.append("required_margin_invalid")
            if not math.isfinite(preflight.loss_at_stop) or preflight.loss_at_stop < 0:
                reasons.append("broker_stop_loss_invalid")
            elif preflight.loss_at_stop > reserved_loss + 1e-9:
                reasons.append("broker_stop_loss_exceeds_intent_budget")

            account_snapshot = self._account_snapshot(snapshot, active_loss, same_loss)
            state_value = risk_state(account_snapshot, self.policy.constitution)
            equity = snapshot.equity
            safe_reserved = reserved_loss if math.isfinite(reserved_loss) and reserved_loss > 0 else 0.0
            safe_margin = required_margin if math.isfinite(required_margin) and required_margin >= 0 else 0.0
            post_loss = active_loss + safe_reserved
            post_same_loss = same_loss + safe_reserved
            post_margin = snapshot.margin_used + safe_margin
            post_heat = math.inf if equity == 0 and post_loss > 0 else (post_loss / equity if equity > 0 else 0.0)
            same_heat = math.inf if equity == 0 and post_same_loss > 0 else (post_same_loss / equity if equity > 0 else 0.0)

            if equity <= 0:
                reasons.extend(("account_state:failed", "trade_risk_ceiling", "portfolio_heat_ceiling"))
            else:
                assessment = assess_trade_risk(
                    account_snapshot,
                    TradeRiskRequest(
                        proposed_risk=safe_reserved / equity,
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
                state_value = assessment.state
                reasons.extend(assessment.reasons)
            if snapshot.free_margin + 1e-9 < safe_margin:
                reasons.append("broker_free_margin_insufficient")

            if reasons:
                self._commit()
                return self._decision(
                    RiskReservationDecision.DENIED,
                    intent,
                    snapshot,
                    reserved_loss=reserved_loss,
                    active_loss=active_loss,
                    same_loss_after=post_same_loss,
                    post_margin=post_margin,
                    risk_state_value=state_value,
                    reasons=reasons,
                )

            record_fingerprint, event_fingerprint = self._insert_record_and_initial_event(
                intent, preflight, snapshot, now=moment, evidence=evidence
            )
            self._commit()
            return self._decision(
                RiskReservationDecision.APPROVED,
                intent,
                snapshot,
                reserved_loss=reserved_loss,
                active_loss=active_loss,
                same_loss_after=post_same_loss,
                post_margin=post_margin,
                risk_state_value=state_value,
                record_fingerprint=record_fingerprint,
                event_fingerprint=event_fingerprint,
            )
        except Exception:
            try:
                self._rollback()
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
        required_current: RiskReservationState,
    ) -> RiskLifecycleReceipt:
        moment = _aware(now, "reservation transition timestamp")
        evidence = _evidence(evidence_fingerprints, "reservation transition evidence")
        self._begin()
        try:
            errors = self._integrity_errors()
            if errors:
                raise RuntimeError("portfolio risk ledger integrity failure: " + ",".join(errors))
            record = self._record_row(intent_hash)
            if record is None:
                raise KeyError(intent_hash)
            events = self._event_rows(intent_hash)
            if not events:
                raise RuntimeError("reservation missing lifecycle event")
            current = RiskReservationState(str(events[-1][1]))
            if current is target:
                self._commit()
                return RiskLifecycleReceipt(_sha(intent_hash, "receipt intent"), current, str(events[-1][0]), datetime.fromisoformat(str(events[-1][2])))
            if current is not required_current:
                raise ValueError(f"transition requires {required_current.value} state; found {current.value}")
            if moment < datetime.fromisoformat(str(events[-1][2])).astimezone(timezone.utc):
                raise ValueError("reservation transition cannot predate current lifecycle event")
            previous = str(events[-1][0])
            event_evidence = tuple(sorted({str(record[1]), *evidence}))
            event_payload = {
                "protocol": "dusty-m195-risk-reservation-event-v1",
                "intent_hash": str(record[0]),
                "state": target.value,
                "created_at": moment.isoformat(),
                "previous_event_fingerprint": previous,
                "evidence_fingerprints": list(event_evidence),
            }
            event_fingerprint = _digest(event_payload)
            self._db.execute(
                "INSERT INTO portfolio_risk_events("
                "event_fingerprint,intent_hash,state,created_at,previous_event_fingerprint,evidence_fingerprints) "
                "VALUES(?,?,?,?,?,?)",
                (event_fingerprint, str(record[0]), target.value, moment.isoformat(), previous, _canonical(list(event_evidence))),
            )
            self._commit()
            return RiskLifecycleReceipt(str(record[0]), target, event_fingerprint, moment)
        except Exception:
            try:
                self._rollback()
            except sqlite3.DatabaseError:
                pass
            raise

    def commit(
        self,
        intent_hash: str,
        *,
        now: datetime,
        evidence_fingerprints: Iterable[str],
    ) -> RiskLifecycleReceipt:
        """Record that broker exposure may now exist; this method never calls a broker."""
        return self._transition(
            intent_hash,
            RiskReservationState.COMMITTED,
            now=now,
            evidence_fingerprints=evidence_fingerprints,
            required_current=RiskReservationState.RESERVED,
        )

    def release_pre_send(
        self,
        intent_hash: str,
        *,
        now: datetime,
        no_send_evidence_fingerprints: Iterable[str],
    ) -> RiskLifecycleReceipt:
        """Release only if the same atomic transaction still sees RESERVED/no-send state."""
        return self._transition(
            intent_hash,
            RiskReservationState.RELEASED,
            now=now,
            evidence_fingerprints=no_send_evidence_fingerprints,
            required_current=RiskReservationState.RESERVED,
        )

    def release_terminal(self, release: TerminalRiskReleaseEvidence) -> RiskLifecycleReceipt:
        """Release committed heat only after explicit broker/reconciliation terminal evidence."""
        record = self._record_row(release.intent_hash)
        if record is None:
            raise KeyError(release.intent_hash)
        if str(record[5]) != release.account_fingerprint:
            raise ValueError("terminal release account identity mismatch")
        if not release.position_absent_or_closed or not release.pending_order_absent:
            raise ValueError("terminal release requires proof of no remaining position or pending order")
        return self._transition(
            release.intent_hash,
            RiskReservationState.RELEASED,
            now=release.captured_at,
            evidence_fingerprints=(release.fingerprint, *release.evidence_fingerprints),
            required_current=RiskReservationState.COMMITTED,
        )
