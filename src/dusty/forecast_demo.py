from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Iterable

from .forecast_council import ForecastTradeAction
from .market_clock import MarketClockState


@dataclass(frozen=True, slots=True)
class FrozenForecastChampion:
    champion_id: str
    model_fingerprint: str
    strategy_hash: str
    evaluation_hash: str
    promoted_at: datetime

    def __post_init__(self) -> None:
        if not self.champion_id.strip() or any(len(value) != 64 for value in (self.model_fingerprint, self.strategy_hash, self.evaluation_hash)):
            raise ValueError("forecast champion identity is incomplete")
        _aware(self.promoted_at, "forecast champion promotion time")

    @property
    def fingerprint(self) -> str:
        return _digest((self.champion_id, self.model_fingerprint, self.strategy_hash, self.evaluation_hash, self.promoted_at.isoformat()))


@dataclass(frozen=True, slots=True)
class DemoForecastObservation:
    observation_id: str
    desk_id: str
    observed_at: datetime
    champion_fingerprint: str
    forecast_fingerprint: str
    decision_fingerprint: str
    market_state: MarketClockState
    action: ForecastTradeAction

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or not self.desk_id.strip():
            raise ValueError("demo forecast observation identity is incomplete")
        if any(len(value) != 64 for value in (self.champion_fingerprint, self.forecast_fingerprint, self.decision_fingerprint)):
            raise ValueError("demo forecast observation hashes are invalid")
        _aware(self.observed_at, "demo forecast observation time")


@dataclass(frozen=True, slots=True)
class DemoForecastOutcome:
    observation_id: str
    realized_at: datetime
    realized_return: float
    net_pnl: float
    execution_cost: float

    def __post_init__(self) -> None:
        if not self.observation_id.strip() or any(not math.isfinite(value) for value in (self.realized_return, self.net_pnl, self.execution_cost)):
            raise ValueError("demo forecast outcome is invalid")
        if self.execution_cost < 0:
            raise ValueError("demo execution cost cannot be negative")
        _aware(self.realized_at, "demo forecast outcome time")


class SQLiteForecastDemoLedger:
    """Append-only forecast/decision/outcome evidence; observations cannot be rewritten after results."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS forecast_observations("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,observation_id TEXT UNIQUE NOT NULL,desk_id TEXT NOT NULL,"
            "observed_at REAL NOT NULL,champion_hash TEXT NOT NULL,forecast_hash TEXT NOT NULL,"
            "decision_hash TEXT NOT NULL,market_state TEXT NOT NULL,action TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS forecast_outcomes("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,observation_id TEXT UNIQUE NOT NULL,realized_at REAL NOT NULL,"
            "realized_return REAL NOT NULL,net_pnl REAL NOT NULL,execution_cost REAL NOT NULL,"
            "FOREIGN KEY(observation_id) REFERENCES forecast_observations(observation_id))"
        )
        for table in ("forecast_observations", "forecast_outcomes"):
            self._db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table} BEGIN SELECT RAISE(ABORT,'append_only'); END")
            self._db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table} BEGIN SELECT RAISE(ABORT,'append_only'); END")
        self._db.commit()

    def append_observation(self, row: DemoForecastObservation) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO forecast_observations(observation_id,desk_id,observed_at,champion_hash,forecast_hash,decision_hash,market_state,action) VALUES(?,?,?,?,?,?,?,?)",
                (row.observation_id, row.desk_id, row.observed_at.timestamp(), row.champion_fingerprint, row.forecast_fingerprint, row.decision_fingerprint, row.market_state.value, row.action.value),
            )

    def append_outcome(self, row: DemoForecastOutcome) -> None:
        observation = self._db.execute(
            "SELECT observed_at FROM forecast_observations WHERE observation_id=?", (row.observation_id,)
        ).fetchone()
        if observation is None:
            raise ValueError("demo forecast outcome has no prior observation")
        if row.realized_at.timestamp() <= observation[0]:
            raise ValueError("demo outcome must follow its forecast observation")
        with self._db:
            self._db.execute(
                "INSERT INTO forecast_outcomes(observation_id,realized_at,realized_return,net_pnl,execution_cost) VALUES(?,?,?,?,?)",
                (row.observation_id, row.realized_at.timestamp(), row.realized_return, row.net_pnl, row.execution_cost),
            )

    @property
    def evidence_hash(self) -> str:
        observations = tuple(self._db.execute("SELECT observation_id,desk_id,observed_at,champion_hash,forecast_hash,decision_hash,market_state,action FROM forecast_observations ORDER BY seq"))
        outcomes = tuple(self._db.execute("SELECT observation_id,realized_at,realized_return,net_pnl,execution_cost FROM forecast_outcomes ORDER BY seq"))
        return _digest((observations, outcomes))

    def counts(self) -> tuple[int, int]:
        observations = self._db.execute("SELECT COUNT(*) FROM forecast_observations").fetchone()[0]
        outcomes = self._db.execute("SELECT COUNT(*) FROM forecast_outcomes").fetchone()[0]
        return observations, outcomes

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()


@dataclass(frozen=True, slots=True)
class ForecastDeskEvidence:
    desk_id: str
    champion_fingerprint: str
    session_fingerprint: str
    completed_forecasts: int
    calibration_error: float
    net_pnl_after_costs: float
    maximum_drawdown_fraction: float
    unexpected_clock_faults: int = 0
    scheduled_closed_observations: int = 0

    def __post_init__(self) -> None:
        if not self.desk_id.strip() or len(self.champion_fingerprint) != 64 or not self.session_fingerprint.strip():
            raise ValueError("forecast desk evidence identity is incomplete")
        numeric = (self.calibration_error, self.net_pnl_after_costs, self.maximum_drawdown_fraction)
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("forecast desk evidence values must be finite")
        if self.completed_forecasts < 0 or not 0 <= self.calibration_error <= 1 or self.maximum_drawdown_fraction < 0:
            raise ValueError("forecast desk evidence metrics are invalid")
        if self.unexpected_clock_faults < 0 or self.scheduled_closed_observations < 0:
            raise ValueError("forecast desk observation counts cannot be negative")


@dataclass(frozen=True, slots=True)
class ForecastDemoCertification:
    certified: bool
    live_write_authorized: bool
    passing_desks: int
    reasons: tuple[str, ...]
    evidence_hash: str


def certify_forecast_demo_campaign(
    evidence: Iterable[ForecastDeskEvidence],
    *,
    required_desks: int = 6,
    minimum_completed_forecasts: int = 30,
    maximum_calibration_error: float = 0.10,
    maximum_drawdown_fraction: float = 0.10,
) -> ForecastDemoCertification:
    rows = tuple(evidence)
    if required_desks < 1 or minimum_completed_forecasts < 1:
        raise ValueError("demo forecast certification thresholds are invalid")
    reasons: list[str] = []
    if len({row.desk_id for row in rows}) != len(rows):
        reasons.append("duplicate_forecast_desk")
    passing = 0
    champion_hashes = {row.champion_fingerprint for row in rows}
    if len(champion_hashes) > 1:
        reasons.append("forecast_champion_drift_across_desks")
    session_hashes = {row.session_fingerprint for row in rows}
    if len(session_hashes) != len(rows):
        reasons.append("demo_sessions_not_independent")
    for row in rows:
        row_reasons = []
        if row.completed_forecasts < minimum_completed_forecasts:
            row_reasons.append("insufficient_forecasts")
        if row.calibration_error > maximum_calibration_error:
            row_reasons.append("miscalibrated")
        if row.net_pnl_after_costs <= 0:
            row_reasons.append("not_profitable_after_costs")
        if row.maximum_drawdown_fraction > maximum_drawdown_fraction:
            row_reasons.append("drawdown_exceeded")
        if row.unexpected_clock_faults:
            row_reasons.append("unexpected_clock_fault")
        if row_reasons:
            reasons.extend(f"desk:{row.desk_id}:{reason}" for reason in row_reasons)
        else:
            passing += 1
    if passing < required_desks:
        reasons.append("insufficient_passing_forecast_desks")
    # Scheduled closure observations are evidence that the clock waited normally, not failures.
    payload = tuple(
        (row.desk_id, row.champion_fingerprint, row.session_fingerprint, row.completed_forecasts, row.calibration_error, row.net_pnl_after_costs, row.maximum_drawdown_fraction, row.unexpected_clock_faults, row.scheduled_closed_observations)
        for row in sorted(rows, key=lambda item: item.desk_id)
    )
    return ForecastDemoCertification(not reasons, False, passing, tuple(reasons), _digest(payload))


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _digest(payload: object) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
