from __future__ import annotations

"""M196 deterministic correlation and concentration guard.

M196 is an admission layer immediately before the M195 master risk book. It
uses certified M173 dependency evidence and existing portfolio factor limits to
prevent apparently distinct strategies from stacking the same economic risk.

Required order::

    M196 reserve -> M195 reserve -> M196 bind_master

The M195 and M196 ledgers share one SQLite database and serialize writers with
``BEGIN IMMEDIATE``. M196 can only remove capacity; it has no broker, live,
Guardian, promotion, risk-override, optimizer, or LLM authority.
"""

from dataclasses import dataclass, fields
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from itertools import combinations
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable

from .experience import TradeSide
from .order_intent import OrderIntent
from .portfolio import QuantPortfolioPolicy
from .portfolio_risk_governor import PortfolioCapitalSnapshot, PortfolioRiskGovernorPolicy
from .strategy_dependency import DependencyPolicy, StrategyDependencyMatrix


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


def _text(value: str, label: str, *, maximum: int = 96) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _evidence(values: Iterable[str], label: str) -> tuple[str, ...]:
    rows = tuple(sorted(_sha(value, label) for value in values))
    if not rows or len(rows) != len(set(rows)):
        raise ValueError(f"{label} must be unique and nonempty")
    return rows


def _policy_payload(value: object) -> tuple[tuple[str, object], ...]:
    return tuple((field.name, getattr(value, field.name)) for field in fields(value))


def _factors(values: Iterable[tuple[str, float]]) -> tuple[tuple[str, float], ...]:
    rows: list[tuple[str, float]] = []
    seen: set[str] = set()
    for raw_name, raw_value in values:
        name = _text(raw_name, "factor").upper()
        coefficient = float(raw_value)
        if name in seen:
            raise ValueError("factor names must be unique")
        if not math.isfinite(coefficient) or coefficient == 0.0 or abs(coefficient) > 1.0:
            raise ValueError("factor coefficients must be finite, nonzero, and in [-1,1]")
        seen.add(name)
        rows.append((name, coefficient))
    if not rows:
        raise ValueError("at least one concentration factor is required")
    return tuple(sorted(rows))


class ConcentrationReservationState(StrEnum):
    RESERVED = "reserved"
    BOUND = "bound"
    RELEASED = "released"


class ConcentrationDecision(StrEnum):
    APPROVED = "approved"
    EXISTING = "existing"
    DENIED = "denied"


class _MasterRequirement(StrEnum):
    ABSENT = "absent"
    ACTIVE_EXACT = "active_exact"
    RELEASED_EXACT = "released_exact"


@dataclass(frozen=True, slots=True)
class InstrumentFactorEvidence:
    symbol: str
    source_commit: str
    captured_at: datetime
    expires_at: datetime
    long_factor_exposures: tuple[tuple[str, float], ...]
    metadata_fingerprint: str
    evidence_fingerprints: tuple[str, ...]
    complete: bool = True
    margin_currency: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol", maximum=64).upper())
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "instrument source commit"))
        captured = _aware(self.captured_at, "instrument captured_at")
        expires = _aware(self.expires_at, "instrument expires_at")
        if expires <= captured:
            raise ValueError("instrument evidence expiry must follow capture")
        object.__setattr__(self, "captured_at", captured)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(self, "long_factor_exposures", _factors(self.long_factor_exposures))
        object.__setattr__(self, "metadata_fingerprint", _sha(self.metadata_fingerprint, "instrument metadata"))
        object.__setattr__(self, "evidence_fingerprints", _evidence(self.evidence_fingerprints, "instrument evidence"))
        if not isinstance(self.complete, bool):
            raise ValueError("instrument complete must be boolean")
        if self.margin_currency is not None:
            object.__setattr__(self, "margin_currency", _text(self.margin_currency, "margin currency", maximum=32).upper())

    @classmethod
    def from_metaquotes_metadata(
        cls,
        *,
        symbol: str,
        source_commit: str,
        captured_at: datetime,
        expires_at: datetime,
        metadata_fingerprint: str,
        evidence_fingerprints: Iterable[str],
        currency_base: str | None,
        currency_profit: str | None,
        currency_margin: str | None = None,
        additional_directional_factors: Iterable[tuple[str, float]] = (),
        complete: bool = True,
    ) -> InstrumentFactorEvidence:
        rendered_symbol = _text(symbol, "symbol", maximum=64).upper()
        base = "" if currency_base is None else str(currency_base).strip().upper()
        profit = "" if currency_profit is None else str(currency_profit).strip().upper()
        factors: list[tuple[str, float]] = []
        if base and profit and base != profit:
            factors.extend(((f"CCY:{base}", 1.0), (f"CCY:{profit}", -1.0)))
        else:
            factors.append((f"SYMBOL:{rendered_symbol}", 1.0))
        factors.extend(additional_directional_factors)
        return cls(
            rendered_symbol,
            source_commit,
            captured_at,
            expires_at,
            tuple(factors),
            metadata_fingerprint,
            tuple(evidence_fingerprints),
            complete,
            currency_margin,
        )

    def exposures_for(self, side: TradeSide) -> tuple[tuple[str, float], ...]:
        multiplier = 1.0 if side is TradeSide.LONG else -1.0 if side is TradeSide.SHORT else None
        if multiplier is None:  # pragma: no cover - fail closed on future enum expansion
            raise ValueError(f"unsupported trade side: {side}")
        return tuple((name, value * multiplier) for name, value in self.long_factor_exposures)

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m196-instrument-factor-v1",
                "symbol": self.symbol,
                "source_commit": self.source_commit,
                "captured_at": self.captured_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "long_factor_exposures": list(self.long_factor_exposures),
                "metadata_fingerprint": self.metadata_fingerprint,
                "evidence_fingerprints": list(self.evidence_fingerprints),
                "complete": self.complete,
                "margin_currency": self.margin_currency,
            }
        )


@dataclass(frozen=True, slots=True)
class DependencyEvidence:
    matrix: StrategyDependencyMatrix
    source_commit: str
    captured_at: datetime
    expires_at: datetime
    evidence_fingerprints: tuple[str, ...]
    complete: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "dependency source commit"))
        captured = _aware(self.captured_at, "dependency captured_at")
        expires = _aware(self.expires_at, "dependency expires_at")
        if expires <= captured:
            raise ValueError("dependency evidence expiry must follow capture")
        object.__setattr__(self, "captured_at", captured)
        object.__setattr__(self, "expires_at", expires)
        object.__setattr__(self, "evidence_fingerprints", _evidence(self.evidence_fingerprints, "dependency evidence"))
        if not isinstance(self.complete, bool):
            raise ValueError("dependency complete must be boolean")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m196-dependency-evidence-v1",
                "matrix_fingerprint": self.matrix.fingerprint,
                "source_commit": self.source_commit,
                "captured_at": self.captured_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "evidence_fingerprints": list(self.evidence_fingerprints),
                "complete": self.complete,
            }
        )


@dataclass(frozen=True, slots=True)
class CorrelationConcentrationPolicy:
    dependency: DependencyPolicy = DependencyPolicy()
    portfolio: QuantPortfolioPolicy = QuantPortfolioPolicy()
    master_risk: PortfolioRiskGovernorPolicy = PortfolioRiskGovernorPolicy()

    @property
    def dependency_cluster_heat_hard(self) -> float:
        return float(self.master_risk.constitution.same_symbol_hard_risk)

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m196-policy-v1",
                _policy_payload(self.dependency),
                _policy_payload(self.portfolio),
                self.master_risk.fingerprint,
                self.dependency_cluster_heat_hard,
            )
        )


@dataclass(frozen=True, slots=True)
class ConcentrationDecisionRecord:
    decision: ConcentrationDecision
    intent_hash: str
    account_fingerprint: str
    reserved_loss: float
    portfolio_loss_after: float
    maximum_dependency_cluster_heat: float
    maximum_factor_net_heat: float
    maximum_factor_gross_heat: float
    risk_hhi: float
    effective_risk_count: float
    dependency_breaches: tuple[tuple[str, str], ...]
    factor_net_heat: tuple[tuple[str, float], ...]
    factor_gross_heat: tuple[tuple[str, float], ...]
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

    @property
    def llm_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class ConcentrationLifecycleReceipt:
    intent_hash: str
    state: ConcentrationReservationState
    event_fingerprint: str
    created_at: datetime
    master_record_fingerprint: str | None = None


class SQLiteCorrelationConcentrationGuard:
    def __init__(
        self,
        path: str | Path,
        *,
        source_commit: str,
        policy: CorrelationConcentrationPolicy = CorrelationConcentrationPolicy(),
    ) -> None:
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("M196 requires a durable shared SQLite path")
        self.source_commit = _git_sha(source_commit, "M196 source commit")
        self.policy = policy
        self._db = sqlite3.connect(
            self.path,
            timeout=policy.master_risk.busy_timeout_ms / 1000.0,
            isolation_level=None,
        )
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(f"PRAGMA busy_timeout={policy.master_risk.busy_timeout_ms}")
        self._require_m195()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS m196_concentration_records("
            "intent_hash TEXT PRIMARY KEY,record_fingerprint TEXT NOT NULL UNIQUE,"
            "strategy_hash TEXT NOT NULL,symbol TEXT NOT NULL,account_fingerprint TEXT NOT NULL,"
            "source_commit TEXT NOT NULL,reserved_loss REAL NOT NULL,factor_exposures TEXT NOT NULL,"
            "instrument_fingerprint TEXT NOT NULL,dependency_fingerprint TEXT,capital_fingerprint TEXT NOT NULL,"
            "policy_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL,evidence_fingerprints TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS m196_concentration_events("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,event_fingerprint TEXT NOT NULL UNIQUE,"
            "intent_hash TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,"
            "previous_event_fingerprint TEXT,master_record_fingerprint TEXT,evidence_fingerprints TEXT NOT NULL,"
            "FOREIGN KEY(intent_hash) REFERENCES m196_concentration_records(intent_hash))"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_m196_account ON m196_concentration_records(account_fingerprint,intent_hash)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_m196_events ON m196_concentration_events(intent_hash,seq)"
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

    @property
    def llm_authorized(self) -> bool:
        return False

    def close(self) -> None:
        self._db.close()

    def _require_m195(self) -> None:
        found = {
            str(row[0])
            for row in self._db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('portfolio_risk_records','portfolio_risk_events')"
            )
        }
        if found != {"portfolio_risk_records", "portfolio_risk_events"}:
            raise RuntimeError("M196 requires initialized M195 tables in the same database")

    def _begin(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._db.execute("COMMIT")

    def _rollback(self) -> None:
        self._db.execute("ROLLBACK")

    @staticmethod
    def _load_json(raw: object, label: str) -> object:
        try:
            return json.loads(str(raw))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{label} JSON is corrupt") from exc

    def _record(self, intent_hash: str) -> tuple[object, ...] | None:
        return self._db.execute(
            "SELECT intent_hash,record_fingerprint,strategy_hash,symbol,account_fingerprint,source_commit,"
            "reserved_loss,factor_exposures,instrument_fingerprint,dependency_fingerprint,capital_fingerprint,"
            "policy_fingerprint,created_at,evidence_fingerprints FROM m196_concentration_records WHERE intent_hash=?",
            (_sha(intent_hash, "M196 intent"),),
        ).fetchone()

    def _events(self, intent_hash: str) -> tuple[tuple[object, ...], ...]:
        return tuple(
            self._db.execute(
                "SELECT event_fingerprint,state,created_at,previous_event_fingerprint,master_record_fingerprint,"
                "evidence_fingerprints FROM m196_concentration_events WHERE intent_hash=? ORDER BY seq",
                (_sha(intent_hash, "M196 event intent"),),
            ).fetchall()
        )

    def _master(self, intent_hash: str) -> tuple[object, ...] | None:
        return self._db.execute(
            "SELECT intent_hash,record_fingerprint,strategy_hash,symbol,account_fingerprint,reserved_loss "
            "FROM portfolio_risk_records WHERE intent_hash=?",
            (_sha(intent_hash, "M195 intent"),),
        ).fetchone()

    def _master_state(self, intent_hash: str) -> str | None:
        row = self._db.execute(
            "SELECT state FROM portfolio_risk_events WHERE intent_hash=? ORDER BY seq DESC LIMIT 1",
            (_sha(intent_hash, "M195 event intent"),),
        ).fetchone()
        return None if row is None else str(row[0])

    def _record_payload_from_row(self, row: tuple[object, ...]) -> dict[str, object]:
        factors = self._load_json(row[7], "M196 factors")
        evidence = self._load_json(row[13], "M196 record evidence")
        if not isinstance(factors, list) or not isinstance(evidence, list):
            raise RuntimeError("M196 record JSON has invalid shape")
        return {
            "protocol": "dusty-m196-record-v1",
            "intent_hash": str(row[0]),
            "strategy_hash": str(row[2]),
            "symbol": str(row[3]),
            "account_fingerprint": str(row[4]),
            "source_commit": str(row[5]),
            "reserved_loss": float(row[6]),
            "factor_exposures": factors,
            "instrument_fingerprint": str(row[8]),
            "dependency_fingerprint": None if row[9] is None else str(row[9]),
            "capital_fingerprint": str(row[10]),
            "policy_fingerprint": str(row[11]),
            "created_at": str(row[12]),
            "evidence_fingerprints": evidence,
        }

    def _event_payload_from_row(self, intent_hash: str, row: tuple[object, ...]) -> dict[str, object]:
        evidence = self._load_json(row[5], "M196 event evidence")
        if not isinstance(evidence, list):
            raise RuntimeError("M196 event evidence has invalid shape")
        return {
            "protocol": "dusty-m196-event-v1",
            "intent_hash": intent_hash,
            "state": str(row[1]),
            "created_at": str(row[2]),
            "previous_event_fingerprint": None if row[3] is None else str(row[3]),
            "master_record_fingerprint": None if row[4] is None else str(row[4]),
            "evidence_fingerprints": evidence,
        }

    def _integrity_errors(self) -> tuple[str, ...]:
        errors: list[str] = []
        result = str(self._db.execute("PRAGMA integrity_check").fetchone()[0])
        if result.lower() != "ok":
            errors.append(f"sqlite:{result}")
        intents = [str(row[0]) for row in self._db.execute("SELECT intent_hash FROM m196_concentration_records")]
        for intent_hash in intents:
            try:
                record = self._record(intent_hash)
                if record is None:
                    errors.append(f"missing_record:{intent_hash}")
                    continue
                if _digest(self._record_payload_from_row(record)) != str(record[1]):
                    errors.append(f"record_fingerprint:{intent_hash}")
                events = self._events(intent_hash)
                if not events:
                    errors.append(f"missing_event:{intent_hash}")
                    continue
                previous_fp: str | None = None
                previous_state: ConcentrationReservationState | None = None
                bound_master: str | None = None
                for index, event in enumerate(events):
                    if _digest(self._event_payload_from_row(intent_hash, event)) != str(event[0]):
                        errors.append(f"event_fingerprint:{intent_hash}:{index}")
                    state = ConcentrationReservationState(str(event[1]))
                    prior = None if event[3] is None else str(event[3])
                    master_fp = None if event[4] is None else str(event[4])
                    if index == 0:
                        if state is not ConcentrationReservationState.RESERVED or prior is not None or master_fp is not None:
                            errors.append(f"invalid_initial_event:{intent_hash}")
                    else:
                        if prior != previous_fp:
                            errors.append(f"broken_event_chain:{intent_hash}:{index}")
                        allowed = {
                            ConcentrationReservationState.RESERVED: {
                                ConcentrationReservationState.BOUND,
                                ConcentrationReservationState.RELEASED,
                            },
                            ConcentrationReservationState.BOUND: {ConcentrationReservationState.RELEASED},
                            ConcentrationReservationState.RELEASED: set(),
                        }[previous_state]  # type: ignore[index]
                        if state not in allowed:
                            errors.append(f"illegal_event_transition:{intent_hash}:{index}")
                    if state is ConcentrationReservationState.BOUND:
                        bound_master = master_fp
                        if master_fp is None:
                            errors.append(f"bound_without_master:{intent_hash}")
                    if state is ConcentrationReservationState.RELEASED and previous_state is ConcentrationReservationState.BOUND:
                        if master_fp != bound_master:
                            errors.append(f"released_master_drift:{intent_hash}")
                    previous_fp = str(event[0])
                    previous_state = state
            except (ValueError, RuntimeError, sqlite3.DatabaseError) as exc:
                errors.append(f"record:{intent_hash}:{type(exc).__name__}")
        return tuple(errors)

    def integrity_check(self) -> tuple[bool, tuple[str, ...]]:
        errors = self._integrity_errors()
        return (not errors, errors)

    def state(self, intent_hash: str) -> ConcentrationReservationState:
        events = self._events(intent_hash)
        if not events:
            raise KeyError(intent_hash)
        return ConcentrationReservationState(str(events[-1][1]))

    def _active_records(self, account_fingerprint: str) -> tuple[tuple[object, ...], ...]:
        account = _sha(account_fingerprint, "M196 account")
        result: list[tuple[object, ...]] = []
        for (intent_hash,) in self._db.execute(
            "SELECT intent_hash FROM m196_concentration_records WHERE account_fingerprint=? ORDER BY intent_hash",
            (account,),
        ):
            record = self._record(str(intent_hash))
            events = self._events(str(intent_hash))
            if record is None or not events:
                raise RuntimeError("M196 lifecycle incomplete")
            if ConcentrationReservationState(str(events[-1][1])) in {
                ConcentrationReservationState.RESERVED,
                ConcentrationReservationState.BOUND,
            }:
                result.append(record)
        return tuple(result)

    def _unmapped_active_master(self, account_fingerprint: str) -> tuple[str, ...]:
        account = _sha(account_fingerprint, "M195 account")
        masters: set[str] = set()
        for (intent_hash,) in self._db.execute(
            "SELECT intent_hash FROM portfolio_risk_records WHERE account_fingerprint=?",
            (account,),
        ):
            if self._master_state(str(intent_hash)) in {"reserved", "committed"}:
                masters.add(str(intent_hash))
        mapped = {str(row[0]) for row in self._active_records(account)}
        return tuple(sorted(masters - mapped))

    def _validate_snapshot(self, snapshot: PortfolioCapitalSnapshot, now: datetime) -> tuple[str, ...]:
        reasons: list[str] = []
        if snapshot.source_commit != self.source_commit:
            reasons.append("capital_snapshot_source_commit_mismatch")
        if now < snapshot.captured_at:
            reasons.append("capital_snapshot_from_future")
        elif (now - snapshot.captured_at).total_seconds() > self.policy.master_risk.max_snapshot_age_seconds:
            reasons.append("capital_snapshot_stale")
        if snapshot.equity <= 0:
            reasons.append("account_equity_not_positive")
        if not snapshot.complete_exposure_data:
            reasons.append("incomplete_broker_exposure_data")
        if snapshot.unexplained_position_count:
            reasons.append("unexplained_broker_positions")
        if snapshot.unexplained_order_count:
            reasons.append("unexplained_broker_orders")
        return tuple(reasons)

    def _validate_instrument(
        self, evidence: InstrumentFactorEvidence, intent: OrderIntent, now: datetime
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        if evidence.source_commit != self.source_commit:
            reasons.append("instrument_evidence_source_commit_mismatch")
        if evidence.symbol != intent.symbol.upper():
            reasons.append("instrument_evidence_symbol_mismatch")
        if not evidence.complete:
            reasons.append("instrument_factor_evidence_incomplete")
        if now < evidence.captured_at:
            reasons.append("instrument_factor_evidence_from_future")
        if now > evidence.expires_at:
            reasons.append("instrument_factor_evidence_expired")
        return tuple(reasons)

    def _dependency_pairs(
        self,
        evidence: DependencyEvidence | None,
        strategies: tuple[str, ...],
        now: datetime,
    ) -> tuple[dict[tuple[str, str], tuple[float, float]], tuple[str, ...]]:
        if len(strategies) < 2:
            return {}, ()
        if evidence is None:
            return {}, ("dependency_evidence_missing",)
        reasons: list[str] = []
        if evidence.source_commit != self.source_commit:
            reasons.append("dependency_evidence_source_commit_mismatch")
        if not evidence.complete:
            reasons.append("dependency_evidence_incomplete")
        if now < evidence.captured_at:
            reasons.append("dependency_evidence_from_future")
        if now > evidence.expires_at:
            reasons.append("dependency_evidence_expired")
        expected = tuple(sorted(strategies))
        matrix = evidence.matrix
        if tuple(matrix.strategy_fingerprints) != expected:
            reasons.append("dependency_strategy_universe_mismatch")
        if matrix.observation_count < self.policy.dependency.minimum_observations:
            reasons.append("dependency_evidence_insufficient")
        pairs: dict[tuple[str, str], tuple[float, float]] = {}
        for row in matrix.pairs:
            key = (row.left_strategy_fingerprint, row.right_strategy_fingerprint)
            if key in pairs:
                reasons.append("dependency_pair_duplicate")
            pairs[key] = (float(row.correlation), float(row.co_loss_fraction))
        if set(pairs) != set(combinations(expected, 2)):
            reasons.append("dependency_pair_coverage_incomplete")
        return pairs, tuple(reasons)

    def _factor_rows(self, record: tuple[object, ...]) -> tuple[tuple[str, float], ...]:
        raw = self._load_json(record[7], "M196 factors")
        if not isinstance(raw, list):
            raise RuntimeError("M196 factors have invalid shape")
        return _factors((str(item[0]), float(item[1])) for item in raw)

    def _metrics(
        self,
        records: tuple[tuple[object, ...], ...],
        equity: float,
        pairs: dict[tuple[str, str], tuple[float, float]],
    ) -> tuple[float, float, float, float, tuple[tuple[str, str], ...], tuple[tuple[str, float], ...], tuple[tuple[str, float], ...], tuple[str, ...]]:
        if equity <= 0:
            return math.inf, math.inf, math.inf, 1.0, (), (), (), ("account_equity_not_positive",)
        total = sum(float(row[6]) for row in records)
        hhi = sum((float(row[6]) / total) ** 2 for row in records) if total > 0 else 0.0
        strategy_loss: dict[str, float] = {}
        net: dict[str, float] = {}
        gross: dict[str, float] = {}
        for row in records:
            loss = float(row[6])
            strategy = str(row[2])
            strategy_loss[strategy] = strategy_loss.get(strategy, 0.0) + loss
            for factor, coefficient in self._factor_rows(row):
                net[factor] = net.get(factor, 0.0) + loss * coefficient
                gross[factor] = gross.get(factor, 0.0) + loss * abs(coefficient)
        net_heat = {name: value / equity for name, value in net.items()}
        gross_heat = {name: value / equity for name, value in gross.items()}
        reasons = {
            f"factor_heat_ceiling:{name}"
            for name, value in net_heat.items()
            if abs(value) > self.policy.portfolio.max_factor_heat + 1e-12
        }

        strategies = tuple(sorted(strategy_loss))
        graph: dict[str, set[str]] = {name: set() for name in strategies}
        breaches: list[tuple[str, str]] = []
        for left, right in combinations(strategies, 2):
            metrics = pairs.get((left, right))
            if metrics is None:
                continue
            correlation, co_loss = metrics
            if (
                abs(correlation) > self.policy.dependency.maximum_absolute_correlation
                or co_loss > self.policy.dependency.maximum_co_loss_fraction
            ):
                graph[left].add(right)
                graph[right].add(left)
                breaches.append((left, right))

        max_cluster = 0.0
        for strategy, loss in strategy_loss.items():
            heat = loss / equity
            max_cluster = max(max_cluster, heat)
            if heat > self.policy.dependency_cluster_heat_hard + 1e-12:
                reasons.add(f"dependency_cluster_heat_ceiling:{strategy}")
        visited: set[str] = set()
        for root in strategies:
            if root in visited:
                continue
            stack = [root]
            component: set[str] = set()
            while stack:
                node = stack.pop()
                if node in component:
                    continue
                component.add(node)
                stack.extend(graph[node] - component)
            visited.update(component)
            if len(component) > 1:
                heat = sum(strategy_loss[name] for name in component) / equity
                max_cluster = max(max_cluster, heat)
                if heat > self.policy.dependency_cluster_heat_hard + 1e-12:
                    reasons.add("dependency_cluster_heat_ceiling:" + "+".join(sorted(component)))
        return (
            max_cluster,
            max((abs(value) for value in net_heat.values()), default=0.0),
            max(gross_heat.values(), default=0.0),
            hhi,
            tuple(sorted(breaches)),
            tuple(sorted(net_heat.items())),
            tuple(sorted(gross_heat.items())),
            tuple(sorted(reasons)),
        )

    def _new_record_payload(
        self,
        intent: OrderIntent,
        snapshot: PortfolioCapitalSnapshot,
        factor_exposures: tuple[tuple[str, float], ...],
        instrument: InstrumentFactorEvidence,
        dependency: DependencyEvidence | None,
        now: datetime,
        evidence: tuple[str, ...],
    ) -> dict[str, object]:
        return {
            "protocol": "dusty-m196-record-v1",
            "intent_hash": intent.intent_hash,
            "strategy_hash": _sha(intent.strategy_hash, "M196 strategy"),
            "symbol": intent.symbol.upper(),
            "account_fingerprint": snapshot.account_fingerprint,
            "source_commit": self.source_commit,
            "reserved_loss": float(intent.allowed_loss),
            "factor_exposures": [list(item) for item in factor_exposures],
            "instrument_fingerprint": instrument.fingerprint,
            "dependency_fingerprint": None if dependency is None else dependency.fingerprint,
            "capital_fingerprint": snapshot.fingerprint,
            "policy_fingerprint": self.policy.fingerprint,
            "created_at": now.isoformat(),
            "evidence_fingerprints": list(evidence),
        }

    def _insert_reservation(
        self,
        intent: OrderIntent,
        snapshot: PortfolioCapitalSnapshot,
        factor_exposures: tuple[tuple[str, float], ...],
        instrument: InstrumentFactorEvidence,
        dependency: DependencyEvidence | None,
        now: datetime,
        evidence: tuple[str, ...],
    ) -> tuple[str, str]:
        payload = self._new_record_payload(intent, snapshot, factor_exposures, instrument, dependency, now, evidence)
        record_fp = _digest(payload)
        self._db.execute(
            "INSERT INTO m196_concentration_records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                intent.intent_hash,
                record_fp,
                _sha(intent.strategy_hash, "M196 strategy"),
                intent.symbol.upper(),
                snapshot.account_fingerprint,
                self.source_commit,
                float(intent.allowed_loss),
                _canonical([list(item) for item in factor_exposures]),
                instrument.fingerprint,
                None if dependency is None else dependency.fingerprint,
                snapshot.fingerprint,
                self.policy.fingerprint,
                now.isoformat(),
                _canonical(list(evidence)),
            ),
        )
        event_evidence = tuple(sorted({record_fp, instrument.fingerprint, *evidence}))
        event_payload = {
            "protocol": "dusty-m196-event-v1",
            "intent_hash": intent.intent_hash,
            "state": ConcentrationReservationState.RESERVED.value,
            "created_at": now.isoformat(),
            "previous_event_fingerprint": None,
            "master_record_fingerprint": None,
            "evidence_fingerprints": list(event_evidence),
        }
        event_fp = _digest(event_payload)
        self._db.execute(
            "INSERT INTO m196_concentration_events(event_fingerprint,intent_hash,state,created_at,previous_event_fingerprint,master_record_fingerprint,evidence_fingerprints) VALUES(?,?,?,?,?,?,?)",
            (event_fp, intent.intent_hash, "reserved", now.isoformat(), None, None, _canonical(list(event_evidence))),
        )
        return record_fp, event_fp

    def reserve(
        self,
        intent: OrderIntent,
        snapshot: PortfolioCapitalSnapshot,
        instrument: InstrumentFactorEvidence,
        dependency: DependencyEvidence | None,
        *,
        now: datetime,
        evidence_fingerprints: Iterable[str],
    ) -> ConcentrationDecisionRecord:
        moment = _aware(now, "M196 reserve timestamp")
        evidence = _evidence(evidence_fingerprints, "M196 reserve evidence")
        self._begin()
        try:
            errors = self._integrity_errors()
            if errors:
                raise RuntimeError("M196 concentration ledger integrity failure: " + ",".join(errors))
            existing = self._record(intent.intent_hash)
            if existing is not None and self.state(intent.intent_hash) is ConcentrationReservationState.RELEASED:
                raise ValueError("released M196 concentration reservation cannot be resurrected")

            reasons = [*self._validate_snapshot(snapshot, moment), *self._validate_instrument(instrument, intent, moment)]
            if intent.session_fingerprint != snapshot.session_fingerprint:
                reasons.append("intent_session_mismatch")
            if not math.isfinite(intent.allowed_loss) or intent.allowed_loss <= 0:
                reasons.append("planned_loss_invalid")
            if self._unmapped_active_master(snapshot.account_fingerprint):
                reasons.append("unmapped_active_master_risk")

            active = self._active_records(snapshot.account_fingerprint)
            if existing is not None:
                strategies = tuple(sorted({str(row[2]) for row in active}))
                pair_map, dependency_reasons = self._dependency_pairs(dependency, strategies, moment)
                reasons.extend(dependency_reasons)
                metrics = self._metrics(active, snapshot.equity, pair_map)
                self._commit()
                return ConcentrationDecisionRecord(
                    ConcentrationDecision.EXISTING,
                    intent.intent_hash,
                    snapshot.account_fingerprint,
                    float(existing[6]),
                    sum(float(row[6]) for row in active),
                    metrics[0], metrics[1], metrics[2], metrics[3],
                    0.0 if metrics[3] <= 0 else 1.0 / metrics[3],
                    metrics[4], metrics[5], metrics[6],
                    tuple(sorted(set(reasons) | {"existing_active_concentration_reservation_reused"})),
                    str(existing[1]),
                    str(self._events(intent.intent_hash)[-1][0]),
                )

            factor_exposures = instrument.exposures_for(intent.side)
            pseudo = (
                intent.intent_hash, "0" * 64, _sha(intent.strategy_hash, "M196 strategy"), intent.symbol.upper(),
                snapshot.account_fingerprint, self.source_commit, float(intent.allowed_loss),
                _canonical([list(item) for item in factor_exposures]), instrument.fingerprint,
                None if dependency is None else dependency.fingerprint, snapshot.fingerprint,
                self.policy.fingerprint, moment.isoformat(), _canonical(list(evidence)),
            )
            post = (*active, pseudo)
            strategies = tuple(sorted({str(row[2]) for row in post}))
            pair_map, dependency_reasons = self._dependency_pairs(dependency, strategies, moment)
            reasons.extend(dependency_reasons)
            metrics = self._metrics(post, snapshot.equity, pair_map)
            reasons.extend(metrics[7])
            if reasons:
                self._commit()
                return ConcentrationDecisionRecord(
                    ConcentrationDecision.DENIED,
                    intent.intent_hash,
                    snapshot.account_fingerprint,
                    float(intent.allowed_loss),
                    sum(float(row[6]) for row in post),
                    metrics[0], metrics[1], metrics[2], metrics[3],
                    0.0 if metrics[3] <= 0 else 1.0 / metrics[3],
                    metrics[4], metrics[5], metrics[6], tuple(sorted(set(reasons))),
                )
            record_fp, event_fp = self._insert_reservation(
                intent, snapshot, factor_exposures, instrument, dependency, moment, evidence
            )
            self._commit()
            return ConcentrationDecisionRecord(
                ConcentrationDecision.APPROVED,
                intent.intent_hash,
                snapshot.account_fingerprint,
                float(intent.allowed_loss),
                sum(float(row[6]) for row in post),
                metrics[0], metrics[1], metrics[2], metrics[3],
                0.0 if metrics[3] <= 0 else 1.0 / metrics[3],
                metrics[4], metrics[5], metrics[6], (), record_fp, event_fp,
            )
        except Exception:
            try:
                self._rollback()
            except sqlite3.DatabaseError:
                pass
            raise

    def _validate_master(
        self,
        record: tuple[object, ...],
        events: tuple[tuple[object, ...], ...],
        requirement: _MasterRequirement,
        expected_fingerprint: str | None,
    ) -> str | None:
        master = self._master(str(record[0]))
        master_state = self._master_state(str(record[0])) if master is not None else None
        bound_master = None if events[-1][4] is None else str(events[-1][4])
        if requirement is _MasterRequirement.ABSENT:
            if master is not None:
                raise ValueError("pre-master release forbidden because an M195 record exists")
            return None
        if master is None or master_state is None:
            raise ValueError("required M195 master reservation does not exist")
        master_fp = str(master[1])
        if expected_fingerprint is not None and master_fp != expected_fingerprint:
            raise ValueError("M195 master record fingerprint mismatch")
        if requirement is _MasterRequirement.ACTIVE_EXACT:
            if master_state not in {"reserved", "committed"}:
                raise ValueError("M196 bind requires active M195 reservation")
            if (
                str(master[2]) != str(record[2])
                or str(master[3]) != str(record[3])
                or str(master[4]) != str(record[4])
                or abs(float(master[5]) - float(record[6])) > 1e-9
            ):
                raise ValueError("M196/M195 reservation identity or economics mismatch")
            return master_fp
        if master_state != "released":
            raise ValueError("M196 release requires RELEASED M195 reservation")
        if bound_master is None or master_fp != bound_master:
            raise ValueError("released M195 record does not match bound M196 master")
        return master_fp

    def _transition(
        self,
        intent_hash: str,
        target: ConcentrationReservationState,
        *,
        now: datetime,
        evidence_fingerprints: Iterable[str],
        required_current: ConcentrationReservationState,
        master_requirement: _MasterRequirement,
        expected_master_fingerprint: str | None = None,
    ) -> ConcentrationLifecycleReceipt:
        moment = _aware(now, "M196 transition timestamp")
        evidence = _evidence(evidence_fingerprints, "M196 transition evidence")
        expected = None if expected_master_fingerprint is None else _sha(expected_master_fingerprint, "M195 master")
        self._begin()
        try:
            errors = self._integrity_errors()
            if errors:
                raise RuntimeError("M196 concentration ledger integrity failure: " + ",".join(errors))
            record = self._record(intent_hash)
            if record is None:
                raise KeyError(intent_hash)
            events = self._events(intent_hash)
            if not events:
                raise RuntimeError("M196 reservation has no lifecycle event")
            current = ConcentrationReservationState(str(events[-1][1]))

            # Master provenance is revalidated even on idempotent repeated calls.
            master_fp = self._validate_master(record, events, master_requirement, expected)
            if current is target:
                current_master = None if events[-1][4] is None else str(events[-1][4])
                self._commit()
                return ConcentrationLifecycleReceipt(
                    str(record[0]), current, str(events[-1][0]), datetime.fromisoformat(str(events[-1][2])), current_master
                )
            if current is not required_current:
                raise ValueError(f"M196 transition requires {required_current.value}; found {current.value}")
            previous_time = datetime.fromisoformat(str(events[-1][2])).astimezone(timezone.utc)
            if moment < previous_time:
                raise ValueError("M196 transition cannot predate current event")
            if target is ConcentrationReservationState.BOUND and master_fp is None:
                raise ValueError("BOUND transition requires exact M195 master")
            stored_master = master_fp if target is ConcentrationReservationState.BOUND else (
                None if events[-1][4] is None else str(events[-1][4])
            )
            event_evidence = tuple(sorted({str(record[1]), *evidence, *(() if master_fp is None else (master_fp,))}))
            payload = {
                "protocol": "dusty-m196-event-v1",
                "intent_hash": str(record[0]),
                "state": target.value,
                "created_at": moment.isoformat(),
                "previous_event_fingerprint": str(events[-1][0]),
                "master_record_fingerprint": stored_master,
                "evidence_fingerprints": list(event_evidence),
            }
            event_fp = _digest(payload)
            self._db.execute(
                "INSERT INTO m196_concentration_events(event_fingerprint,intent_hash,state,created_at,previous_event_fingerprint,master_record_fingerprint,evidence_fingerprints) VALUES(?,?,?,?,?,?,?)",
                (event_fp, str(record[0]), target.value, moment.isoformat(), str(events[-1][0]), stored_master, _canonical(list(event_evidence))),
            )
            self._commit()
            return ConcentrationLifecycleReceipt(str(record[0]), target, event_fp, moment, stored_master)
        except Exception:
            try:
                self._rollback()
            except sqlite3.DatabaseError:
                pass
            raise

    def bind_master(
        self,
        intent_hash: str,
        master_record_fingerprint: str,
        *,
        now: datetime,
        evidence_fingerprints: Iterable[str],
    ) -> ConcentrationLifecycleReceipt:
        return self._transition(
            intent_hash,
            ConcentrationReservationState.BOUND,
            now=now,
            evidence_fingerprints=evidence_fingerprints,
            required_current=ConcentrationReservationState.RESERVED,
            master_requirement=_MasterRequirement.ACTIVE_EXACT,
            expected_master_fingerprint=master_record_fingerprint,
        )

    def release_pre_master(
        self,
        intent_hash: str,
        *,
        now: datetime,
        no_master_evidence_fingerprints: Iterable[str],
    ) -> ConcentrationLifecycleReceipt:
        return self._transition(
            intent_hash,
            ConcentrationReservationState.RELEASED,
            now=now,
            evidence_fingerprints=no_master_evidence_fingerprints,
            required_current=ConcentrationReservationState.RESERVED,
            master_requirement=_MasterRequirement.ABSENT,
        )

    def release_after_master(
        self,
        intent_hash: str,
        *,
        now: datetime,
        master_release_evidence_fingerprints: Iterable[str],
    ) -> ConcentrationLifecycleReceipt:
        return self._transition(
            intent_hash,
            ConcentrationReservationState.RELEASED,
            now=now,
            evidence_fingerprints=master_release_evidence_fingerprints,
            required_current=ConcentrationReservationState.BOUND,
            master_requirement=_MasterRequirement.RELEASED_EXACT,
        )

    def adopt_active_master(
        self,
        intent: OrderIntent,
        snapshot: PortfolioCapitalSnapshot,
        instrument: InstrumentFactorEvidence,
        dependency: DependencyEvidence | None,
        *,
        now: datetime,
        evidence_fingerprints: Iterable[str],
    ) -> ConcentrationLifecycleReceipt:
        moment = _aware(now, "M196 adoption timestamp")
        evidence = _evidence(evidence_fingerprints, "M196 adoption evidence")
        self._begin()
        try:
            errors = self._integrity_errors()
            if errors:
                raise RuntimeError("M196 concentration ledger integrity failure: " + ",".join(errors))
            if self._record(intent.intent_hash) is not None:
                raise ValueError("M196 reservation already exists")
            master = self._master(intent.intent_hash)
            master_state = self._master_state(intent.intent_hash) if master is not None else None
            if master is None or master_state not in {"reserved", "committed"}:
                raise ValueError("adoption requires active M195 reservation")
            reasons = [*self._validate_snapshot(snapshot, moment), *self._validate_instrument(instrument, intent, moment)]
            if reasons:
                raise ValueError("adoption evidence invalid: " + ",".join(sorted(set(reasons))))
            if (
                str(master[2]) != _sha(intent.strategy_hash, "M196 strategy")
                or str(master[3]) != intent.symbol.upper()
                or str(master[4]) != snapshot.account_fingerprint
                or abs(float(master[5]) - float(intent.allowed_loss)) > 1e-9
            ):
                raise ValueError("adopted M195 reservation does not match OrderIntent")
            record_fp, initial_event_fp = self._insert_reservation(
                intent, snapshot, instrument.exposures_for(intent.side), instrument, dependency, moment, evidence
            )
            bind_evidence = tuple(sorted({record_fp, str(master[1]), *evidence}))
            payload = {
                "protocol": "dusty-m196-event-v1",
                "intent_hash": intent.intent_hash,
                "state": ConcentrationReservationState.BOUND.value,
                "created_at": moment.isoformat(),
                "previous_event_fingerprint": initial_event_fp,
                "master_record_fingerprint": str(master[1]),
                "evidence_fingerprints": list(bind_evidence),
            }
            event_fp = _digest(payload)
            self._db.execute(
                "INSERT INTO m196_concentration_events(event_fingerprint,intent_hash,state,created_at,previous_event_fingerprint,master_record_fingerprint,evidence_fingerprints) VALUES(?,?,?,?,?,?,?)",
                (event_fp, intent.intent_hash, "bound", moment.isoformat(), initial_event_fp, str(master[1]), _canonical(list(bind_evidence))),
            )
            self._commit()
            return ConcentrationLifecycleReceipt(intent.intent_hash, ConcentrationReservationState.BOUND, event_fp, moment, str(master[1]))
        except Exception:
            try:
                self._rollback()
            except sqlite3.DatabaseError:
                pass
            raise
