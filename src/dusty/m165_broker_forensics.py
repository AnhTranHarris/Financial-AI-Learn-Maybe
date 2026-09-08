from __future__ import annotations

"""Read-only exact-ticket broker forensics for ambiguous M165/M187 sends.

This module deliberately has no MetaTrader5 import and no broker-write surface.
The caller supplies an already initialized MT5-like module.  It queries every
native identity MetaQuotes exposes for an order ticket, then follows any
position ids discovered in broker history.  Empty results remain evidence of
ambiguity; they never authorize a retry.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Any, Callable


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _rows(value: object) -> tuple[object, ...]:
    return tuple(value or ())


def _last_error(module: Any) -> tuple[int, str]:
    try:
        raw = module.last_error()
    except Exception as exc:  # pragma: no cover - defensive provider boundary
        return (-1, f"last_error_failed:{type(exc).__name__}")
    if isinstance(raw, tuple) and len(raw) >= 2:
        return (int(raw[0]), str(raw[1]))
    return (0, str(raw))


def _safe(call: Callable[[], object], module: Any) -> tuple[tuple[object, ...], tuple[int, str]]:
    try:
        result = call()
    except Exception as exc:
        return (), (-2, f"query_exception:{type(exc).__name__}:{exc}")
    error = _last_error(module)
    if result is None:
        return (), error
    return _rows(result), error


def _order(row: object) -> dict[str, object]:
    return {
        "ticket": int(getattr(row, "ticket", 0) or 0),
        "position_id": int(getattr(row, "position_id", 0) or 0),
        "time_setup_msc": int(getattr(row, "time_setup_msc", 0) or 0),
        "time_done_msc": int(getattr(row, "time_done_msc", 0) or 0),
        "type": int(getattr(row, "type", -1)),
        "state": int(getattr(row, "state", -1)),
        "volume_initial": float(getattr(row, "volume_initial", 0.0) or 0.0),
        "volume_current": float(getattr(row, "volume_current", 0.0) or 0.0),
        "price_open": float(getattr(row, "price_open", 0.0) or 0.0),
        "price_current": float(getattr(row, "price_current", 0.0) or 0.0),
        "sl": float(getattr(row, "sl", 0.0) or 0.0),
        "symbol": str(getattr(row, "symbol", "")),
        "comment": str(getattr(row, "comment", ""))[:128],
    }


def _deal(row: object) -> dict[str, object]:
    return {
        "ticket": int(getattr(row, "ticket", 0) or 0),
        "order": int(getattr(row, "order", 0) or 0),
        "position_id": int(getattr(row, "position_id", 0) or 0),
        "time_msc": int(getattr(row, "time_msc", 0) or 0),
        "type": int(getattr(row, "type", -1)),
        "entry": int(getattr(row, "entry", -1)),
        "reason": int(getattr(row, "reason", -1)),
        "volume": float(getattr(row, "volume", 0.0) or 0.0),
        "price": float(getattr(row, "price", 0.0) or 0.0),
        "commission": float(getattr(row, "commission", 0.0) or 0.0),
        "fee": float(getattr(row, "fee", 0.0) or 0.0),
        "swap": float(getattr(row, "swap", 0.0) or 0.0),
        "symbol": str(getattr(row, "symbol", "")),
        "comment": str(getattr(row, "comment", ""))[:128],
    }


def _position(row: object) -> dict[str, object]:
    return {
        "ticket": int(getattr(row, "ticket", 0) or 0),
        "identifier": int(getattr(row, "identifier", 0) or 0),
        "time_msc": int(getattr(row, "time_msc", 0) or 0),
        "type": int(getattr(row, "type", -1)),
        "volume": float(getattr(row, "volume", 0.0) or 0.0),
        "price_open": float(getattr(row, "price_open", 0.0) or 0.0),
        "price_current": float(getattr(row, "price_current", 0.0) or 0.0),
        "sl": float(getattr(row, "sl", 0.0) or 0.0),
        "profit": float(getattr(row, "profit", 0.0) or 0.0),
        "symbol": str(getattr(row, "symbol", "")),
        "comment": str(getattr(row, "comment", ""))[:128],
    }


@dataclass(frozen=True, slots=True)
class BrokerForensicSnapshot:
    order_ticket: int
    captured_at: datetime
    payload: dict[str, object]

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m165-broker-forensics-v1", self.payload))

    broker_write_authority = False
    retry_authority = False
    live_write_authority = False


def capture_broker_forensics(
    module: Any,
    *,
    order_ticket: int,
    symbol: str,
    sent_at: datetime,
    captured_at: datetime,
) -> BrokerForensicSnapshot:
    if isinstance(order_ticket, bool) or int(order_ticket) <= 0:
        raise ValueError("positive order ticket required")
    if sent_at.tzinfo is None or captured_at.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    ticket = int(order_ticket)
    symbol = str(symbol).strip().upper()
    sent = sent_at.astimezone(timezone.utc)
    captured = captured_at.astimezone(timezone.utc)
    if captured < sent:
        raise ValueError("capture cannot precede send")

    queries: dict[str, object] = {}

    def record(name: str, call: Callable[[], object], serializer: Callable[[object], dict[str, object]]) -> tuple[object, ...]:
        rows, error = _safe(call, module)
        queries[name] = {"rows": [serializer(row) for row in rows], "last_error": [error[0], error[1]]}
        return rows

    history_order_rows = record("history_orders_by_ticket", lambda: module.history_orders_get(ticket=ticket), _order)
    deal_by_order_rows = record("history_deals_by_order_ticket", lambda: module.history_deals_get(ticket=ticket), _deal)
    record("active_orders_by_ticket", lambda: module.orders_get(ticket=ticket), _order)
    record("open_positions_by_ticket", lambda: module.positions_get(ticket=ticket), _position)

    position_ids = {
        int(getattr(row, "position_id", 0) or 0)
        for row in (*history_order_rows, *deal_by_order_rows)
        if int(getattr(row, "position_id", 0) or 0) > 0
    }
    for position_id in sorted(position_ids):
        record(f"history_orders_by_position_{position_id}", lambda pid=position_id: module.history_orders_get(position=pid), _order)
        record(f"history_deals_by_position_{position_id}", lambda pid=position_id: module.history_deals_get(position=pid), _deal)
        record(f"open_position_{position_id}", lambda pid=position_id: module.positions_get(ticket=pid), _position)

    start = sent - timedelta(minutes=10)
    end = captured + timedelta(minutes=1)
    record("history_orders_window_all_symbols", lambda: module.history_orders_get(start, end), _order)
    record("history_deals_window_all_symbols", lambda: module.history_deals_get(start, end), _deal)
    record("active_orders_all_symbols", lambda: module.orders_get(), _order)
    record("open_positions_all_symbols", lambda: module.positions_get(), _position)

    exact_history_order = bool(queries["history_orders_by_ticket"]["rows"])
    exact_deal = bool(queries["history_deals_by_order_ticket"]["rows"])
    active_order = bool(queries["active_orders_by_ticket"]["rows"])
    open_position = bool(queries["open_positions_by_ticket"]["rows"])
    discovered_position_evidence = any(
        bool(value["rows"])
        for key, value in queries.items()
        if key.startswith(("history_deals_by_position_", "open_position_"))
    )

    if exact_deal or discovered_position_evidence:
        assessment = "broker_execution_evidence_found"
    elif exact_history_order or active_order:
        assessment = "broker_order_evidence_found_without_deal"
    else:
        assessment = "no_broker_evidence_found_ambiguous_send"

    payload: dict[str, object] = {
        "protocol": "dusty-m165-broker-forensics-v1",
        "order_ticket": ticket,
        "symbol": symbol,
        "sent_at": sent.isoformat(),
        "captured_at": captured.isoformat(),
        "assessment": assessment,
        "queries": queries,
        "authority": {"broker_write": False, "live_write": False, "retry": False},
    }
    return BrokerForensicSnapshot(ticket, captured, payload)
