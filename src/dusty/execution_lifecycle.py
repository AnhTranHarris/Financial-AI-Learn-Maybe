from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from .experience import TradeSide


class ExecutionState(StrEnum):
    AUTHORIZED = "authorized"
    SENT_UNKNOWN = "sent_unknown"
    ACCEPTED = "accepted"
    PARTIAL = "partial"
    FILLED = "filled"
    PROTECTED = "protected"
    CLOSING = "closing"
    CLOSED = "closed"
    REJECTED = "rejected"
    FAULT = "fault"


_ALLOWED: dict[ExecutionState, set[ExecutionState]] = {
    ExecutionState.AUTHORIZED: {ExecutionState.SENT_UNKNOWN},
    ExecutionState.SENT_UNKNOWN: {ExecutionState.ACCEPTED, ExecutionState.PARTIAL, ExecutionState.FILLED, ExecutionState.PROTECTED, ExecutionState.CLOSED, ExecutionState.REJECTED, ExecutionState.FAULT},
    ExecutionState.ACCEPTED: {ExecutionState.PARTIAL, ExecutionState.FILLED, ExecutionState.REJECTED, ExecutionState.FAULT},
    ExecutionState.PARTIAL: {ExecutionState.FILLED, ExecutionState.PROTECTED, ExecutionState.CLOSING, ExecutionState.FAULT},
    ExecutionState.FILLED: {ExecutionState.PROTECTED, ExecutionState.CLOSING, ExecutionState.CLOSED, ExecutionState.FAULT},
    ExecutionState.PROTECTED: {ExecutionState.CLOSING, ExecutionState.CLOSED, ExecutionState.FAULT},
    ExecutionState.CLOSING: {ExecutionState.CLOSED, ExecutionState.FAULT},
    ExecutionState.CLOSED: set(),
    ExecutionState.REJECTED: set(),
    ExecutionState.FAULT: set(),
}


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    intent_hash: str
    client_tag: str
    state: ExecutionState
    order_ticket: int = 0
    deal_ticket: int = 0
    position_ticket: int = 0
    updated_at: datetime | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class BrokerExecutionSnapshot:
    client_tag: str
    order_ticket: int = 0
    deal_ticket: int = 0
    position_ticket: int = 0
    closed: bool = False


class SQLiteExecutionLedger:
    """Crash-safe lifecycle ledger. Ambiguous sends are never automatically retried."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS execution_ledger("
            "intent_hash TEXT PRIMARY KEY,client_tag TEXT UNIQUE NOT NULL,state TEXT NOT NULL,"
            "order_ticket INTEGER NOT NULL,deal_ticket INTEGER NOT NULL,position_ticket INTEGER NOT NULL,"
            "updated_at TEXT,note TEXT NOT NULL)"
        )
        self._db.commit()

    def authorize(self, intent_hash: str, client_tag: str, *, at: datetime) -> ExecutionRecord:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("execution timestamp must be timezone-aware")
        with self._db:
            self._db.execute(
                "INSERT INTO execution_ledger(intent_hash,client_tag,state,order_ticket,deal_ticket,position_ticket,updated_at,note) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (intent_hash, client_tag, ExecutionState.AUTHORIZED.value, 0, 0, 0, at.isoformat(), ""),
            )
        return self.get(intent_hash)

    def get(self, intent_hash: str) -> ExecutionRecord:
        row = self._db.execute(
            "SELECT intent_hash,client_tag,state,order_ticket,deal_ticket,position_ticket,updated_at,note "
            "FROM execution_ledger WHERE intent_hash=?",
            (intent_hash,),
        ).fetchone()
        if row is None:
            raise KeyError(intent_hash)
        return ExecutionRecord(
            row[0], row[1], ExecutionState(row[2]), int(row[3]), int(row[4]), int(row[5]),
            datetime.fromisoformat(row[6]) if row[6] else None, row[7]
        )

    def transition(
        self,
        intent_hash: str,
        target: ExecutionState,
        *,
        at: datetime,
        order_ticket: int | None = None,
        deal_ticket: int | None = None,
        position_ticket: int | None = None,
        note: str = "",
    ) -> ExecutionRecord:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("execution timestamp must be timezone-aware")
        current = self.get(intent_hash)
        if target not in _ALLOWED[current.state]:
            raise ValueError(f"illegal execution transition: {current.state.value}->{target.value}")
        values = (
            target.value,
            current.order_ticket if order_ticket is None else int(order_ticket),
            current.deal_ticket if deal_ticket is None else int(deal_ticket),
            current.position_ticket if position_ticket is None else int(position_ticket),
            at.isoformat(),
            note,
            intent_hash,
            current.state.value,
        )
        with self._db:
            cursor = self._db.execute(
                "UPDATE execution_ledger SET state=?,order_ticket=?,deal_ticket=?,position_ticket=?,updated_at=?,note=? "
                "WHERE intent_hash=? AND state=?",
                values,
            )
            if cursor.rowcount != 1:
                raise RuntimeError("execution state changed concurrently")
        return self.get(intent_hash)

    def reserve_send(self, intent_hash: str, *, at: datetime) -> ExecutionRecord:
        return self.transition(intent_hash, ExecutionState.SENT_UNKNOWN, at=at, note="send_reserved_before_broker_call")

    def reconcile_unknown(
        self,
        intent_hash: str,
        snapshots: Iterable[BrokerExecutionSnapshot],
        *,
        at: datetime,
    ) -> ExecutionRecord:
        current = self.get(intent_hash)
        if current.state is not ExecutionState.SENT_UNKNOWN:
            raise ValueError("only ambiguous sends may be reconciled")
        matches = tuple(row for row in snapshots if row.client_tag == current.client_tag)
        if not matches:
            return current
        if len(matches) > 1:
            return self.transition(
                intent_hash,
                ExecutionState.FAULT,
                at=at,
                note="ambiguous_send_has_multiple_broker_matches",
            )
        match = matches[0]
        target = ExecutionState.CLOSED if match.closed else (ExecutionState.FILLED if match.deal_ticket or match.position_ticket else ExecutionState.ACCEPTED)
        return self.transition(
            intent_hash,
            target,
            at=at,
            order_ticket=match.order_ticket,
            deal_ticket=match.deal_ticket,
            position_ticket=match.position_ticket,
            note="reconciled_from_broker_state",
        )

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    position_ticket: int
    symbol: str
    side: TradeSide
    volume: float
    stop_price: float
    target_price: float = 0.0

    def __post_init__(self) -> None:
        if self.position_ticket <= 0 or not self.symbol.strip() or self.volume <= 0:
            raise ValueError("position snapshot identity/volume invalid")


@dataclass(frozen=True, slots=True)
class ProtectionAssessment:
    protected: bool
    reasons: tuple[str, ...]


def assess_position_protection(position: PositionSnapshot) -> ProtectionAssessment:
    reasons = []
    if position.stop_price <= 0:
        reasons.append("protective_stop_missing")
    return ProtectionAssessment(not reasons, tuple(reasons))


def close_by_position_request(position: PositionSnapshot, *, market_price: float, filling_mode: int, magic: int, module: object) -> dict[str, object]:
    """Close by actual position ticket; never assume an opposite order will net a hedging account."""
    if market_price <= 0:
        raise ValueError("market price must be positive")
    order_type = getattr(module, "ORDER_TYPE_SELL") if position.side is TradeSide.LONG else getattr(module, "ORDER_TYPE_BUY")
    return {
        "action": getattr(module, "TRADE_ACTION_DEAL"),
        "position": position.position_ticket,
        "symbol": position.symbol,
        "volume": position.volume,
        "type": order_type,
        "price": market_price,
        "magic": magic,
        "type_time": getattr(module, "ORDER_TIME_GTC"),
        "type_filling": filling_mode,
    }
