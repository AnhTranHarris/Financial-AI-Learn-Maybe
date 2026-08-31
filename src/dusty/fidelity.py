from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from .mt5lab import MT5TestRequest, MT5TickMode, next_tick_mode
from .operations import ReconciliationResult


@dataclass(frozen=True, slots=True)
class FidelityDecision:
    advance: bool
    completed: bool
    next_request: MT5TestRequest | None
    reason: str


def advance_fidelity(
    request: MT5TestRequest,
    reconciliation: ReconciliationResult,
) -> FidelityDecision:
    """Escalate tester fidelity only after the current level reconciles."""
    if not reconciliation.passed:
        return FidelityDecision(False, False, None, "reconciliation_failed")
    next_mode = next_tick_mode(request.tick_mode)
    if next_mode is None:
        return FidelityDecision(False, True, None, "real_tick_validation_complete")
    next_request = replace(
        request,
        request_id=f"{request.request_id}:{next_mode.value}",
        tick_mode=next_mode,
    )
    return FidelityDecision(True, False, next_request, "advance_fidelity")


def validate_fidelity_chain(modes: Iterable[MT5TickMode]) -> bool:
    observed = tuple(modes)
    if not observed:
        return False
    ordered = (
        MT5TickMode.OPEN_PRICES,
        MT5TickMode.ONE_MINUTE_OHLC,
        MT5TickMode.EVERY_TICK,
        MT5TickMode.REAL_TICKS,
    )
    positions = tuple(ordered.index(mode) for mode in observed)
    return all(right - left == 1 for left, right in zip(positions, positions[1:]))
