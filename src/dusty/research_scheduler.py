from __future__ import annotations

"""Resource-aware research scheduling for a single Windows workstation."""

from dataclasses import dataclass
from enum import IntEnum, StrEnum
import math
from typing import Iterable

from .resource import (
    JobPriority,
    ResourceBudget,
    ResourceSnapshot,
    ResourceState,
    admit_job,
    resource_state,
)


class FidelityTier(IntEnum):
    CHEAP = 0
    STANDARD = 1
    NATIVE = 2


class JobKind(StrEnum):
    FORECAST = "forecast"
    STRATEGY_SCREEN = "strategy_screen"
    BACKTEST = "backtest"
    NATIVE_MT5 = "native_mt5"
    TRAINING = "training"
    RESEARCH = "research"


@dataclass(frozen=True, slots=True)
class ResearchJob:
    job_id: str
    kind: JobKind
    priority: JobPriority
    fidelity: FidelityTier
    estimated_ram_bytes: int
    estimated_cpu_seconds: float
    information_value: float
    model_inference: bool = False

    def __post_init__(self) -> None:
        if not self.job_id.strip():
            raise ValueError("research job id required")
        if self.estimated_ram_bytes < 0 or self.estimated_cpu_seconds < 0:
            raise ValueError("research resource estimates cannot be negative")
        if not math.isfinite(self.information_value) or self.information_value < 0:
            raise ValueError("research information value must be finite/nonnegative")

    @property
    def value_per_cpu_second(self) -> float:
        return self.information_value / max(self.estimated_cpu_seconds, 1e-9)


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    reserve_ram_bytes: int = 1_073_741_824
    green_parallelism: int = 4
    yellow_parallelism: int = 2
    orange_parallelism: int = 1
    red_parallelism: int = 0
    sequential_model_inference: bool = True
    green_resident_model_limit: int = 3
    yellow_resident_model_limit: int = 2
    orange_resident_model_limit: int = 1

    def __post_init__(self) -> None:
        numeric = (
            self.reserve_ram_bytes,
            self.green_parallelism,
            self.yellow_parallelism,
            self.orange_parallelism,
            self.red_parallelism,
            self.green_resident_model_limit,
            self.yellow_resident_model_limit,
            self.orange_resident_model_limit,
        )
        if any(value < 0 for value in numeric):
            raise ValueError("scheduler limits cannot be negative")
        if not self.green_parallelism >= self.yellow_parallelism >= self.orange_parallelism >= self.red_parallelism:
            raise ValueError("parallelism must decrease under resource pressure")


@dataclass(frozen=True, slots=True)
class ScheduleDecision:
    admitted: tuple[ResearchJob, ...]
    deferred: tuple[ResearchJob, ...]
    state: ResourceState
    max_parallelism: int
    resident_model_limit: int


def _limits(state: ResourceState, policy: SchedulerPolicy) -> tuple[int, int]:
    parallel = {
        ResourceState.GREEN: policy.green_parallelism,
        ResourceState.YELLOW: policy.yellow_parallelism,
        ResourceState.ORANGE: policy.orange_parallelism,
        ResourceState.RED: policy.red_parallelism,
    }[state]
    resident = {
        ResourceState.GREEN: policy.green_resident_model_limit,
        ResourceState.YELLOW: policy.yellow_resident_model_limit,
        ResourceState.ORANGE: policy.orange_resident_model_limit,
        ResourceState.RED: 0,
    }[state]
    return parallel, resident


def schedule_jobs(
    jobs: Iterable[ResearchJob],
    snapshot: ResourceSnapshot,
    budget: ResourceBudget,
    policy: SchedulerPolicy = SchedulerPolicy(),
) -> ScheduleDecision:
    """Admit highest-value safe work while preserving workstation headroom.

    Model inference is sequential by default because the certified workstation
    already demonstrated large model CPU/RAM contention.
    """

    state = resource_state(snapshot, budget)
    parallel_limit, resident_limit = _limits(state, policy)
    available_for_jobs = max(0, snapshot.available_ram_bytes - policy.reserve_ram_bytes)
    ordered = sorted(
        tuple(jobs),
        key=lambda job: (
            job.fidelity,
            job.priority,
            -job.value_per_cpu_second,
            job.estimated_cpu_seconds,
            job.job_id,
        ),
    )

    admitted: list[ResearchJob] = []
    deferred: list[ResearchJob] = []
    used_ram = 0
    model_count = 0
    native_count = 0

    for job in ordered:
        if len(admitted) >= parallel_limit:
            deferred.append(job)
            continue
        resource_decision = admit_job(job.priority, snapshot, budget)
        if not resource_decision.admitted:
            deferred.append(job)
            continue
        if used_ram + job.estimated_ram_bytes > available_for_jobs:
            deferred.append(job)
            continue
        if job.model_inference:
            if model_count >= resident_limit:
                deferred.append(job)
                continue
            if policy.sequential_model_inference and model_count >= 1:
                deferred.append(job)
                continue
        if state is not ResourceState.GREEN and job.fidelity is FidelityTier.NATIVE:
            if native_count >= 1:
                deferred.append(job)
                continue

        admitted.append(job)
        used_ram += job.estimated_ram_bytes
        model_count += int(job.model_inference)
        native_count += int(job.fidelity is FidelityTier.NATIVE)

    return ScheduleDecision(tuple(admitted), tuple(deferred), state, parallel_limit, resident_limit)


def next_fidelity(current: FidelityTier, *, passed: bool) -> FidelityTier | None:
    """Escalate compute only after a cheaper test survives."""

    if not passed:
        return None
    if current is FidelityTier.CHEAP:
        return FidelityTier.STANDARD
    if current is FidelityTier.STANDARD:
        return FidelityTier.NATIVE
    return None
