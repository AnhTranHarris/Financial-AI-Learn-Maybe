from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from statistics import fmean
from typing import Iterable, Mapping

from .experience import TradeSide


Scalar = bool | int | float | str


class RuleOp(StrEnum):
    GT = "gt"
    GE = "ge"
    LT = "lt"
    LE = "le"
    EQ = "eq"
    NE = "ne"


@dataclass(frozen=True, slots=True)
class Clause:
    feature: str
    op: RuleOp
    value: Scalar

    def __post_init__(self) -> None:
        if not self.feature:
            raise ValueError("clause feature is required")

    def evaluate(self, features: Mapping[str, Scalar]) -> bool:
        if self.feature not in features:
            return False
        actual = features[self.feature]
        if self.op is RuleOp.GT:
            return actual > self.value
        if self.op is RuleOp.GE:
            return actual >= self.value
        if self.op is RuleOp.LT:
            return actual < self.value
        if self.op is RuleOp.LE:
            return actual <= self.value
        if self.op is RuleOp.EQ:
            return actual == self.value
        return actual != self.value


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_id: str
    direction: TradeSide
    clauses: tuple[Clause, ...]
    horizon_steps: int = 1
    cost_bps: float = 0.0

    def __post_init__(self) -> None:
        if not self.strategy_id:
            raise ValueError("strategy_id is required")
        if not self.clauses:
            raise ValueError("at least one clause is required")
        if self.horizon_steps < 1:
            raise ValueError("horizon_steps must be positive")
        if self.cost_bps < 0:
            raise ValueError("cost_bps cannot be negative")

    @property
    def strategy_hash(self) -> str:
        clauses = sorted(
            (
                {"feature": clause.feature, "op": clause.op.value, "value": clause.value}
                for clause in self.clauses
            ),
            key=lambda item: (item["feature"], item["op"], repr(item["value"])),
        )
        payload = {
            "direction": self.direction.value,
            "clauses": clauses,
            "horizon_steps": self.horizon_steps,
            "cost_bps": self.cost_bps,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class FeatureRow:
    at: datetime
    features: tuple[tuple[str, Scalar], ...]
    forward_return: float

    @classmethod
    def of(
        cls,
        at: datetime,
        features: Mapping[str, Scalar],
        forward_return: float,
    ) -> "FeatureRow":
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("feature timestamps must be timezone-aware")
        return cls(at, tuple(sorted(features.items())), float(forward_return))

    def feature_map(self) -> dict[str, Scalar]:
        return dict(self.features)


@dataclass(frozen=True, slots=True)
class ExperimentResult:
    strategy_hash: str
    sample_count: int
    mean_return: float
    total_return: float
    hit_rate: float
    max_loss: float
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ExperimentGate:
    min_samples: int = 20
    min_mean_return: float = 0.0
    min_hit_rate: float = 0.0
    max_single_loss: float = 1.0


@dataclass(frozen=True, slots=True)
class ScreeningResult:
    passed: bool
    reasons: tuple[str, ...]


class CandidateStatus(StrEnum):
    CHALLENGER = "challenger"
    REJECTED = "rejected"
    PROMOTABLE = "promotable"
    CHAMPION = "champion"


@dataclass(frozen=True, slots=True)
class MemoryEntry:
    strategy_hash: str
    strategy_id: str
    status: CandidateStatus
    sample_count: int
    mean_return: float
    hit_rate: float
    reasons: tuple[str, ...]


def run_experiment(spec: StrategySpec, rows: Iterable[FeatureRow]) -> ExperimentResult:
    """Run a deterministic, execution-free historical hypothesis screen."""
    signed_direction = 1.0 if spec.direction is TradeSide.LONG else -1.0
    cost = spec.cost_bps / 10_000.0
    selected: list[tuple[str, float]] = []
    for row in rows:
        features = row.feature_map()
        if all(clause.evaluate(features) for clause in spec.clauses):
            selected.append((row.at.isoformat(), signed_direction * row.forward_return - cost))

    returns = [value for _, value in selected]
    mean_return = fmean(returns) if returns else 0.0
    total_return = sum(returns)
    hit_rate = sum(value > 0 for value in returns) / len(returns) if returns else 0.0
    max_loss = min(returns, default=0.0)
    if max_loss > 0:
        max_loss = 0.0

    digest = sha256(spec.strategy_hash.encode("utf-8"))
    for timestamp, value in selected:
        digest.update(f"{timestamp}|{value:.17g}".encode("utf-8"))
    return ExperimentResult(
        strategy_hash=spec.strategy_hash,
        sample_count=len(returns),
        mean_return=mean_return,
        total_return=total_return,
        hit_rate=hit_rate,
        max_loss=max_loss,
        fingerprint=digest.hexdigest(),
    )


def screen(result: ExperimentResult, gate: ExperimentGate) -> ScreeningResult:
    reasons: list[str] = []
    if result.sample_count < gate.min_samples:
        reasons.append("insufficient_samples")
    if result.mean_return <= gate.min_mean_return:
        reasons.append("mean_return_failed")
    if result.hit_rate < gate.min_hit_rate:
        reasons.append("hit_rate_failed")
    if result.max_loss < -abs(gate.max_single_loss):
        reasons.append("single_loss_failed")
    return ScreeningResult(not reasons, tuple(reasons))


class SQLiteStrategyMemory:
    """Append-only strategy memory for deduplication, graveyard, and promotions."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS research_memory("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "strategy_hash TEXT NOT NULL,"
            "strategy_id TEXT NOT NULL,"
            "status TEXT NOT NULL,"
            "payload TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_research_hash ON research_memory(strategy_hash)"
        )
        self._db.commit()

    def remember(
        self,
        spec: StrategySpec,
        result: ExperimentResult,
        status: CandidateStatus,
        reasons: Iterable[str] = (),
    ) -> None:
        if result.strategy_hash != spec.strategy_hash:
            raise ValueError("experiment result does not belong to strategy")
        payload = json.dumps(
            {
                "sample_count": result.sample_count,
                "mean_return": result.mean_return,
                "hit_rate": result.hit_rate,
                "reasons": list(reasons),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._db:
            self._db.execute(
                "INSERT INTO research_memory(strategy_hash,strategy_id,status,payload) "
                "VALUES(?,?,?,?)",
                (spec.strategy_hash, spec.strategy_id, status.value, payload),
            )

    def seen(self, strategy_hash: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM research_memory WHERE strategy_hash=? LIMIT 1",
            (strategy_hash,),
        ).fetchone()
        return row is not None

    def latest(self, strategy_hash: str) -> MemoryEntry | None:
        row = self._db.execute(
            "SELECT strategy_hash,strategy_id,status,payload FROM research_memory "
            "WHERE strategy_hash=? ORDER BY seq DESC LIMIT 1",
            (strategy_hash,),
        ).fetchone()
        return self._entry(row) if row else None

    def history(self, strategy_hash: str | None = None) -> tuple[MemoryEntry, ...]:
        if strategy_hash is None:
            rows = self._db.execute(
                "SELECT strategy_hash,strategy_id,status,payload FROM research_memory ORDER BY seq"
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT strategy_hash,strategy_id,status,payload FROM research_memory "
                "WHERE strategy_hash=? ORDER BY seq",
                (strategy_hash,),
            ).fetchall()
        return tuple(self._entry(row) for row in rows)

    def graveyard(self) -> tuple[str, ...]:
        rows = self._db.execute(
            "SELECT DISTINCT strategy_hash FROM research_memory WHERE status=? ORDER BY strategy_hash",
            (CandidateStatus.REJECTED.value,),
        ).fetchall()
        return tuple(row[0] for row in rows)

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _entry(row: tuple[str, str, str, str]) -> MemoryEntry:
        strategy_hash, strategy_id, status, payload = row
        data = json.loads(payload)
        return MemoryEntry(
            strategy_hash=strategy_hash,
            strategy_id=strategy_id,
            status=CandidateStatus(status),
            sample_count=int(data["sample_count"]),
            mean_return=float(data["mean_return"]),
            hit_rate=float(data["hit_rate"]),
            reasons=tuple(data.get("reasons", ())),
        )
