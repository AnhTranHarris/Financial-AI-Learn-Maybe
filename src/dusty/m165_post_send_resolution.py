from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Any


class PostSendStatus(StrEnum):
    AMBIGUOUS = "ambiguous"
    ORDER_ONLY = "order_only"
    POSITION_OPEN = "position_open"
    COMPLETED_PROTECTIVE_CLOSE = "completed_protective_close"
    FILLED_POSITION_MISSING = "filled_position_missing"


@dataclass(frozen=True, slots=True)
class PostSendResolution:
    status: PostSendStatus
    position_id: int
    entry_deal_ticket: int
    exit_deal_ticket: int
    reasons: tuple[str, ...]

    broker_write_authority = False
    retry_authority = False
    live_write_authority = False


def _rows(queries: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = queries.get(key, {})
    rows = value.get("rows", []) if isinstance(value, Mapping) else []
    return [dict(row) for row in rows if isinstance(row, Mapping)]


def resolve_post_send_forensics(payload: Mapping[str, Any]) -> PostSendResolution:
    """Interpret read-only broker forensics without granting retry/write authority."""
    queries = payload.get("queries", {})
    if not isinstance(queries, Mapping):
        raise ValueError("forensic queries must be a mapping")

    order_rows = _rows(queries, "history_orders_by_ticket")
    active_order_rows = _rows(queries, "active_orders_by_ticket")
    open_by_ticket = _rows(queries, "open_positions_by_ticket")

    position_ids = {
        int(row.get("position_id", 0) or 0)
        for row in order_rows
        if int(row.get("position_id", 0) or 0) > 0
    }
    position_ids.update(
        int(row.get("position_id", 0) or 0)
        for key, value in queries.items()
        if str(key).startswith("history_deals_by_position_") and isinstance(value, Mapping)
        for row in value.get("rows", [])
        if isinstance(row, Mapping) and int(row.get("position_id", 0) or 0) > 0
    )

    if len(position_ids) > 1:
        raise ValueError("forensics contain multiple position identities")
    position_id = next(iter(position_ids), 0)

    position_rows: list[dict[str, Any]] = list(open_by_ticket)
    deal_rows: list[dict[str, Any]] = []
    if position_id:
        position_rows.extend(_rows(queries, f"open_position_{position_id}"))
        deal_rows = _rows(queries, f"history_deals_by_position_{position_id}")

    entry = [row for row in deal_rows if int(row.get("entry", -1)) == 0]
    exits = [row for row in deal_rows if int(row.get("entry", -1)) in (1, 2, 3)]

    if entry and exits and not position_rows:
        return PostSendResolution(
            PostSendStatus.COMPLETED_PROTECTIVE_CLOSE,
            position_id,
            int(entry[0].get("ticket", 0) or 0),
            int(exits[-1].get("ticket", 0) or 0),
            ("entry and exit deals exist; no open position remains",),
        )
    if entry and position_rows:
        return PostSendResolution(
            PostSendStatus.POSITION_OPEN,
            position_id,
            int(entry[0].get("ticket", 0) or 0),
            0,
            ("entry deal exists and broker reports an open position",),
        )
    if entry:
        return PostSendResolution(
            PostSendStatus.FILLED_POSITION_MISSING,
            position_id,
            int(entry[0].get("ticket", 0) or 0),
            0,
            ("entry deal exists but neither exit evidence nor open position is available",),
        )
    if order_rows or active_order_rows:
        return PostSendResolution(PostSendStatus.ORDER_ONLY, position_id, 0, 0, ("order evidence exists without deal evidence",))
    return PostSendResolution(PostSendStatus.AMBIGUOUS, 0, 0, 0, ("no independently verifiable broker execution evidence",))
