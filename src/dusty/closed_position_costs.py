"""Conservative reconciliation of complete native IN/OUT position histories.

This is arithmetic evidence for observed positions, NOT a verified fee tariff,
slippage estimate, full statement reconciliation or permission to trade.
"""
from collections import Counter, defaultdict
from datetime import datetime
from hashlib import sha256
import math
from typing import Any, Sequence


def observe_closed_positions(mt5: Any, recent_rows: Sequence[Any], symbol: str,
                             start: datetime, end: datetime) -> dict[str, Any]:
    """Verify the full available native position history, not a balanced slice.

At most 32 position queries / 10,000 returned rows, inside the owning worker's
deadline. Positions crossing the observation window are deliberately excluded.
"""
    candidates = sorted({row.position_id for row in recent_rows
                         if getattr(row, "symbol", None) == symbol
                         and type(getattr(row, "position_id", None)) is int and row.position_id > 0})
    accepted = []
    excluded: Counter[str] = Counter()
    if len(candidates) > 32:
        excluded["position_query_limit"] = len(candidates) - 32
    row_count = 0
    fields = ("ticket", "position_id", "symbol", "type", "entry", "time_msc",
              "volume", "price", "profit", "commission", "fee", "swap")
    for position in candidates[:32]:
        try:
            full = mt5.history_deals_get(position=position)
        except Exception:
            excluded["native_position_history_unavailable"] += 1
            continue
        if full is None or not full:
            excluded["native_position_history_unavailable"] += 1
            continue
        row_count += len(full)
        if row_count > 10_000:
            excluded["position_history_row_limit"] += 1
            break
        recent = [row for row in recent_rows if getattr(row, "position_id", None) == position]
        # Require exact fields, not just a coincidentally balanced volume. This
        # excludes older opening fills, native revisions and truncated responses.
        normalize = lambda row: tuple(getattr(row, key, None) for key in fields)
        if (any(getattr(row, "position_id", None) != position for row in full)
                or Counter(map(normalize, full)) != Counter(map(normalize, recent))):
            excluded["full_position_history_differs_from_window"] += 1
            continue
        accepted.extend(full)
    result = reconcile_closed_positions(accepted, symbol, start, end)
    excluded.update(result["excluded_reasons"])
    result["excluded_reasons"] = dict(sorted(excluded.items()))
    result["history_basis"] = "NATIVE_FULL_POSITION_QUERY_MATCHED_TO_WINDOW"
    result["candidate_positions"] = len(candidates)
    result["candidate_queries_bounded_at"] = 32
    return result


def reconcile_closed_positions(rows: Sequence[Any], symbol: str, start: datetime, end: datetime) -> dict[str, Any]:
    """Arithmetic over full position-query rows supplied by observe_closed_positions."""
    result: dict[str, Any] = {"status": "NO_COMPLETE_SUPPORTED_POSITIONS", "positions": [],
                            "excluded_reasons": {}, "schedule_verified": False,
                            "used_as_simulation_costs": False,
                            "standalone_account_charges_included": False}
    if len(rows) > 10_000:
        result["status"] = "BOUNDED_READ_LIMIT_EXCEEDED"
        return result
    groups: dict[int, list[Any]] = defaultdict(list)
    excluded: Counter[str] = Counter()
    seen = set()
    for row in rows:
        if getattr(row, "symbol", None) != symbol:
            continue
        # Include unsupported same-symbol types in their position group, so a
        # canceled/reversed leg cannot silently vanish from a reconstructed pair.
        position = getattr(row, "position_id", None)
        ticket = getattr(row, "ticket", None)
        if type(position) is not int or position <= 0 or type(ticket) is not int or ticket <= 0:
            if getattr(row, "type", None) in (0, 1):
                result.update(status="INCOMPLETE_EXECUTION_IDENTIFIERS", excluded_reasons={"missing_position_or_ticket": 1})
                return result
            excluded["missing_position_or_ticket"] += 1
            continue
        if ticket in seen:
            result.update(status="DUPLICATE_TICKET_EVIDENCE", excluded_reasons={"duplicate_ticket": 1})
            return result
        seen.add(ticket)
        groups[position].append(row)
    for position, group in sorted(groups.items()):
        reason = ""
        for row in group:
            stamp = getattr(row, "time_msc", None)
            values = tuple(getattr(row, key, None) for key in ("volume", "price", "profit", "commission", "fee", "swap"))
            if (type(stamp) is not int or not start.timestamp() * 1000 <= stamp <= end.timestamp() * 1000
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
                    or values[0] <= 0 or values[1] <= 0):
                reason = "missing_invalid_or_out_of_window_fields"
                break
            if type(getattr(row, "entry", None)) is not int or row.entry not in (0, 1) or type(getattr(row, "type", None)) is not int or row.type not in (0, 1):
                reason = "unsupported_reversal_close_by_or_deal_type"
                break
        if reason:
            excluded[reason] += 1
            continue
        group.sort(key=lambda row: (row.time_msc, row.ticket))
        if any(a.time_msc == b.time_msc and a.entry != b.entry for a, b in zip(group, group[1:])):
            excluded["ambiguous_same_time_entry_exit"] += 1
            continue
        direction = group[0].type
        opened, closed, inventory = 0.0, 0.0, 0.0
        for index, row in enumerate(group):
            if row.type != (direction if row.entry == 0 else 1 - direction):
                reason = "inconsistent_position_direction"
                break
            if row.entry == 0:
                opened += row.volume
                inventory += row.volume
            else:
                closed += row.volume
                inventory -= row.volume
            if inventory < -1e-8 or (abs(inventory) <= 1e-8 and index != len(group) - 1):
                reason = "missing_open_or_reused_position_lifecycle"
                break
        if reason or opened <= 0 or not math.isclose(opened, closed, rel_tol=0, abs_tol=1e-8):
            excluded[reason or "position_not_fully_closed_in_window"] += 1
            continue
        totals = {key: math.fsum(getattr(row, key) for row in group) for key in ("profit", "commission", "fee", "swap")}
        result["positions"].append({
            "position_reference": sha256(str(position).encode()).hexdigest(),
            "opened_at_msc": group[0].time_msc, "closed_at_msc": group[-1].time_msc,
            "deal_count": len(group), "opened_lots": opened, "closed_lots": closed,
            "signed_cash": totals, "net_cash": math.fsum(totals.values()),
            "observed_commission_charge_per_round_trip_lot": -totals["commission"] / opened,
            "observed_fee_charge_per_round_trip_lot": -totals["fee"] / opened,
        })
    result["excluded_reasons"] = dict(sorted(excluded.items()))
    if result["positions"]:
        result["status"] = "OBSERVED_CLOSED_POSITION_ARITHMETIC_ONLY"
    return result
