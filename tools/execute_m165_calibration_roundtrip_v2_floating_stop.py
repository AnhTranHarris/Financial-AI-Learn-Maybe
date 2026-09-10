from __future__ import annotations

"""Spread-aware guard around the certified M165 V2 Demo operator.

Some MT5 servers report SYMBOL_TRADE_STOPS_LEVEL == 0 while still enforcing a
floating server-side minimum. This wrapper changes only V2's pure stop-geometry
helper before delegating to its existing M187 execution path. It owns no raw
broker-send surface and grants no retry/live/promotion authority.
"""

import math
from pathlib import Path
import sys


# Windows invokes this file directly from V3. In that mode Python places the
# tools directory, not the repository root, at sys.path[0]. Add the repository
# root deterministically so `tools.*` resolves identically in direct execution,
# unittest imports, and CI. This changes no broker authority.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools import execute_m165_calibration_roundtrip_v2 as v2


FLOATING_STOP_SPREAD_MULTIPLIER = 3


def _floating_safe_long_stop(*, ask: float, bid: float, planned_distance: float, tick_size: float) -> float:
    values = (float(ask), float(bid), float(planned_distance), float(tick_size))
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("floating long-stop geometry requires finite positive values")
    ask_value, bid_value, distance, tick = values
    if ask_value < bid_value:
        raise ValueError("floating long-stop geometry received crossed quote")
    spread = ask_value - bid_value
    if spread + tick * 1e-9 < tick:
        spread = tick
    if distance + tick * 1e-9 < tick:
        raise ValueError("planned stop distance cannot be below native tick size")

    planned_stop = v2._aligned_price_down(ask_value - distance, tick)
    floating_stop = v2._aligned_price_down(
        ask_value - FLOATING_STOP_SPREAD_MULTIPLIER * spread,
        tick,
    )
    bid_guard = v2._aligned_price_down(bid_value - tick, tick)
    stop = min(planned_stop, floating_stop, bid_guard)
    if stop <= 0 or stop >= bid_value:
        raise ValueError("floating long stop cannot remain strictly below bid")
    return stop


def main() -> int:
    v2._safe_long_stop = _floating_safe_long_stop
    return int(v2.main())


if __name__ == "__main__":
    raise SystemExit(main())
