"""Read-only fee evidence. Observed deal costs are not a verified fee schedule."""
from datetime import datetime, timedelta
import math
from typing import Any

from .closed_position_costs import observe_closed_positions


def observe_recent_costs(mt5: Any, symbol: str, at: datetime) -> dict[str, object]:
    """Bounded recent account history, exact symbol and execution types only.

    Never infer zero fees from a deposit, empty history or absent fee fields. Do
    not infer a per-lot round-trip price from partial fills or incomplete positions.
    This observation does not replace the costs frozen before the research run.
    """
    start = at - timedelta(days=30)
    result: dict[str, object] = {
        "symbol": symbol, "observed_at": at.isoformat(), "window_start": start.isoformat(),
        "window_end": at.isoformat(), "status": "UNAVAILABLE", "schedule_verified": False,
        "used_as_simulation_costs": False,
        "totals_scope": "complete_execution_rows_only_not_a_full_account_statement",
        "execution_deals": 0, "complete_cost_rows": 0, "incomplete_cost_rows": 0,
        "commission_cash": None, "fee_cash": None, "swap_cash": None,
    }
    try:
        rows = mt5.history_deals_get(start, at)
    except Exception:
        return result  # Do not record raw vendor errors/account identifiers.
    if rows is None:
        return result
    if len(rows) > 10_000:
        result["status"] = "BOUNDED_READ_LIMIT_EXCEEDED"
        return result
    result["closed_position_evidence"] = observe_closed_positions(mt5, rows, symbol, start, at)
    costs: list[tuple[float, float, float]] = []
    matched = 0
    for row in rows:
        kind = getattr(row, "type", None)
        if getattr(row, "symbol", None) != symbol or isinstance(kind, bool) or kind not in (0, 1):
            continue  # Native BUY/SELL only; balance, credit and unrelated symbols are excluded.
        matched += 1
        values = tuple(getattr(row, name, None) for name in ("commission", "fee", "swap"))
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            continue
        costs.append(values)
    result.update(execution_deals=matched, complete_cost_rows=len(costs), incomplete_cost_rows=matched - len(costs))
    if not matched:
        result["status"] = "NO_MATCHING_EXECUTIONS"
    elif len(costs) != matched:
        result["status"] = "INCOMPLETE_COST_FIELDS"
    else:
        result["status"] = "OBSERVED_NOT_VERIFIED"
    if costs:
        for index, name in enumerate(("commission_cash", "fee_cash", "swap_cash")):
            result[name] = math.fsum(values[index] for values in costs)
    return result
