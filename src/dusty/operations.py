from __future__ import annotations

from dataclasses import dataclass

from .mt5lab import MT5TestRequest, MT5TestResult, MT5TickMode, fidelity_at_least
from .research import ExperimentResult
from .resource import JobPriority, ResourceBudget, ResourceSnapshot, admit_job


@dataclass(frozen=True, slots=True)
class ReconciliationGate:
    max_total_return_gap: float = 0.05
    max_trade_count_gap: int = 5
    required_tick_mode: MT5TickMode = MT5TickMode.EVERY_TICK

    def __post_init__(self) -> None:
        if self.max_total_return_gap < 0 or self.max_trade_count_gap < 0:
            raise ValueError("reconciliation tolerances cannot be negative")


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    passed: bool
    total_return_gap: float
    trade_count_gap: int
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TerminalAssignment:
    request_id: str
    terminal_id: str


@dataclass(frozen=True, slots=True)
class SchedulePlan:
    assignments: tuple[TerminalAssignment, ...]
    deferred_request_ids: tuple[str, ...]
    reason: str


def reconcile_fast_with_mt5(
    fast: ExperimentResult,
    mt5: MT5TestResult,
    gate: ReconciliationGate,
) -> ReconciliationResult:
    """MT5 is the higher-fidelity arbiter when the two laboratories disagree materially."""
    reasons: list[str] = []
    if fast.strategy_hash != mt5.strategy_hash:
        reasons.append("strategy_hash_mismatch")
    return_gap = abs(fast.total_return - mt5.net_return)
    trade_gap = abs(fast.sample_count - mt5.trade_count)
    if return_gap > gate.max_total_return_gap:
        reasons.append("return_gap_exceeded")
    if trade_gap > gate.max_trade_count_gap:
        reasons.append("trade_count_gap_exceeded")
    if not fidelity_at_least(mt5.tick_mode, gate.required_tick_mode):
        reasons.append("mt5_fidelity_too_low")
    return ReconciliationResult(not reasons, return_gap, trade_gap, tuple(reasons))


def plan_mt5_tests(
    requests: tuple[MT5TestRequest, ...],
    terminal_ids: tuple[str, ...],
    snapshot: ResourceSnapshot,
    budget: ResourceBudget,
    *,
    max_concurrent: int,
) -> SchedulePlan:
    """Pure scheduler: one request per terminal, with backtesting yielding under host pressure."""
    if max_concurrent < 1:
        raise ValueError("max_concurrent must be positive")
    decision = admit_job(JobPriority.BACKTEST, snapshot, budget)
    ordered_requests = tuple(sorted(requests, key=lambda item: item.request_id))
    if not decision.admitted:
        return SchedulePlan((), tuple(item.request_id for item in ordered_requests), decision.reason)

    terminals = tuple(sorted({terminal for terminal in terminal_ids if terminal}))
    capacity = max(0, min(max_concurrent - snapshot.active_backtests, len(terminals)))
    assignments = tuple(
        TerminalAssignment(request.request_id, terminal)
        for request, terminal in zip(ordered_requests[:capacity], terminals[:capacity])
    )
    assigned = {item.request_id for item in assignments}
    deferred = tuple(item.request_id for item in ordered_requests if item.request_id not in assigned)
    return SchedulePlan(assignments, deferred, "scheduled" if assignments else "no_capacity")
