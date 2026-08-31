from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Iterator

from .risk import OutcomeQuality, classify_outcome


@dataclass(frozen=True, slots=True)
class CapitalAttribution:
    attribution_id: str
    strategy_hash: str
    symbol: str
    at: datetime
    pnl: float
    capital_at_risk: float
    drawdown_fraction: float
    rules_followed: bool
    regime: str = ""
    session: str = ""
    event_class: str = ""
    broker: str = ""

    def __post_init__(self) -> None:
        if not self.attribution_id.strip() or not self.strategy_hash.strip() or not self.symbol.strip():
            raise ValueError("capital attribution requires identity, strategy and symbol")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("capital attribution timestamp must be timezone-aware")
        if any(not math.isfinite(value) for value in (self.pnl, self.capital_at_risk, self.drawdown_fraction)):
            raise ValueError("capital attribution economics must be finite")
        if self.capital_at_risk <= 0 or not 0.0 <= self.drawdown_fraction <= 1.0:
            raise ValueError("capital at risk must be positive and drawdown in [0,1]")

    @property
    def outcome(self) -> OutcomeQuality:
        return classify_outcome(pnl=self.pnl, rules_followed=self.rules_followed)

    @property
    def return_on_risk(self) -> float:
        return self.pnl / self.capital_at_risk


@dataclass(frozen=True, slots=True)
class CapitalReputation:
    strategy_hash: str
    sample_count: int
    governance_pass_rate: float
    compounded_risk_return: float
    mean_return_on_risk: float
    max_drawdown_fraction: float
    bad_outcomes: int
    investigation_priority: int

    @property
    def ranking_key(self) -> tuple[float, float, float, float, int, str]:
        """Lexicographic constitution: governance first, then growth and efficiency."""
        return (
            -self.governance_pass_rate,
            -self.compounded_risk_return,
            self.max_drawdown_fraction,
            -self.mean_return_on_risk,
            self.bad_outcomes,
            self.strategy_hash,
        )


def capital_reputation(rows: Iterable[CapitalAttribution]) -> CapitalReputation:
    collected = tuple(rows)
    if not collected:
        raise ValueError("capital reputation requires observations")
    hashes = {row.strategy_hash for row in collected}
    if len(hashes) != 1:
        raise ValueError("capital reputation must refer to one strategy")
    governance = sum(row.rules_followed for row in collected) / len(collected)
    risk_returns = tuple(row.return_on_risk for row in collected)
    wealth = 1.0
    for value in risk_returns:
        wealth *= max(0.0, 1.0 + value)
    compounded = wealth - 1.0
    bad = sum(row.outcome in {OutcomeQuality.BAD_WIN, OutcomeQuality.BAD_LOSS, OutcomeQuality.INVALID_FLAT} for row in collected)
    investigation = sum(row.pnl < 0 or not row.rules_followed for row in collected)
    return CapitalReputation(
        strategy_hash=collected[0].strategy_hash,
        sample_count=len(collected),
        governance_pass_rate=governance,
        compounded_risk_return=compounded,
        mean_return_on_risk=sum(risk_returns) / len(risk_returns),
        max_drawdown_fraction=max(row.drawdown_fraction for row in collected),
        bad_outcomes=bad,
        investigation_priority=investigation,
    )


def rank_capital_reputations(rows: Iterable[CapitalAttribution]) -> tuple[CapitalReputation, ...]:
    groups: dict[str, list[CapitalAttribution]] = {}
    for row in rows:
        groups.setdefault(row.strategy_hash, []).append(row)
    return tuple(sorted((capital_reputation(group) for group in groups.values()), key=lambda item: item.ranking_key))


class SQLiteCapitalAttribution:
    """Append-only economic memory. Profitable rule violations remain permanently visible."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS capital_attribution("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,attribution_id TEXT UNIQUE NOT NULL,"
            "strategy_hash TEXT NOT NULL,at TEXT NOT NULL,payload TEXT NOT NULL)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_capital_strategy ON capital_attribution(strategy_hash,seq)")
        self._db.commit()

    def append(self, row: CapitalAttribution) -> None:
        payload = json.dumps(
            {
                "symbol": row.symbol,
                "pnl": row.pnl,
                "capital_at_risk": row.capital_at_risk,
                "drawdown_fraction": row.drawdown_fraction,
                "rules_followed": row.rules_followed,
                "regime": row.regime,
                "session": row.session,
                "event_class": row.event_class,
                "broker": row.broker,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        with self._db:
            self._db.execute(
                "INSERT INTO capital_attribution(attribution_id,strategy_hash,at,payload) VALUES(?,?,?,?)",
                (row.attribution_id, row.strategy_hash, row.at.isoformat(), payload),
            )

    def iter_rows(self, strategy_hash: str | None = None) -> Iterator[CapitalAttribution]:
        if strategy_hash is None:
            cursor = self._db.execute(
                "SELECT attribution_id,strategy_hash,at,payload FROM capital_attribution ORDER BY seq"
            )
        else:
            cursor = self._db.execute(
                "SELECT attribution_id,strategy_hash,at,payload FROM capital_attribution WHERE strategy_hash=? ORDER BY seq",
                (strategy_hash,),
            )
        for attribution_id, strategy, at, payload in cursor:
            data = json.loads(payload)
            yield CapitalAttribution(
                attribution_id=attribution_id,
                strategy_hash=strategy,
                symbol=data["symbol"],
                at=datetime.fromisoformat(at),
                pnl=float(data["pnl"]),
                capital_at_risk=float(data["capital_at_risk"]),
                drawdown_fraction=float(data["drawdown_fraction"]),
                rules_followed=bool(data["rules_followed"]),
                regime=data["regime"],
                session=data["session"],
                event_class=data["event_class"],
                broker=data["broker"],
            )

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()
