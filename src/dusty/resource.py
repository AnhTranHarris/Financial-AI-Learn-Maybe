from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum


class ResourceState(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class JobPriority(IntEnum):
    CORE = 0
    JOURNAL = 1
    EVIDENCE = 2
    FORECAST = 3
    RESEARCH = 4
    BACKTEST = 5
    TRAINING = 6


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    total_ram_bytes: int
    available_ram_bytes: int
    disk_free_bytes: int
    cpu_percent: float = 0.0
    active_mt5_workers: int = 0
    active_model_workers: int = 0
    active_backtests: int = 0

    def __post_init__(self) -> None:
        if self.total_ram_bytes <= 0:
            raise ValueError("total RAM must be positive")
        if not 0 <= self.available_ram_bytes <= self.total_ram_bytes:
            raise ValueError("available RAM must be within total RAM")
        if self.disk_free_bytes < 0:
            raise ValueError("disk free bytes cannot be negative")
        if not 0.0 <= self.cpu_percent <= 100.0:
            raise ValueError("cpu_percent must be between 0 and 100")
        if min(self.active_mt5_workers, self.active_model_workers, self.active_backtests) < 0:
            raise ValueError("worker counts cannot be negative")

    @property
    def available_ram_ratio(self) -> float:
        return self.available_ram_bytes / self.total_ram_bytes


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    min_free_disk_bytes: int
    yellow_ram_ratio: float = 0.25
    orange_ram_ratio: float = 0.12
    red_ram_ratio: float = 0.05
    yellow_cpu_percent: float = 90.0
    orange_cpu_percent: float = 98.0

    def __post_init__(self) -> None:
        if self.min_free_disk_bytes < 0:
            raise ValueError("minimum free disk cannot be negative")
        if not 0 < self.red_ram_ratio < self.orange_ram_ratio < self.yellow_ram_ratio < 1:
            raise ValueError("RAM thresholds must be ordered red < orange < yellow")
        if not 0 <= self.yellow_cpu_percent < self.orange_cpu_percent <= 100:
            raise ValueError("CPU thresholds must be ordered")


@dataclass(frozen=True, slots=True)
class ResourceDecision:
    state: ResourceState
    admitted: bool
    reason: str


def resource_state(snapshot: ResourceSnapshot, budget: ResourceBudget) -> ResourceState:
    """Classify host pressure without depending on psutil or platform-specific APIs."""
    if snapshot.disk_free_bytes < budget.min_free_disk_bytes:
        return ResourceState.RED
    ram = snapshot.available_ram_ratio
    if ram <= budget.red_ram_ratio:
        return ResourceState.RED
    if ram <= budget.orange_ram_ratio or snapshot.cpu_percent >= budget.orange_cpu_percent:
        return ResourceState.ORANGE
    if ram <= budget.yellow_ram_ratio or snapshot.cpu_percent >= budget.yellow_cpu_percent:
        return ResourceState.YELLOW
    return ResourceState.GREEN


def admit_job(
    priority: JobPriority,
    snapshot: ResourceSnapshot,
    budget: ResourceBudget,
) -> ResourceDecision:
    """Admit high-value work first; background work yields as pressure rises."""
    state = resource_state(snapshot, budget)
    ceiling = {
        ResourceState.GREEN: JobPriority.TRAINING,
        ResourceState.YELLOW: JobPriority.BACKTEST,
        ResourceState.ORANGE: JobPriority.FORECAST,
        ResourceState.RED: JobPriority.JOURNAL,
    }[state]
    admitted = priority <= ceiling
    reason = "admitted" if admitted else f"resource_{state.value}_throttle"
    return ResourceDecision(state, admitted, reason)
