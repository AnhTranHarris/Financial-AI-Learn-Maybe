from __future__ import annotations

"""M162 deterministic resource-governed parallel research factory.

M155 owns durable job leases and M160 owns research-value ranking.  M162 has a
narrower responsibility: given an already-ranked batch and one point-in-time
hardware telemetry snapshot, decide which jobs may execute concurrently without
oversubscribing the workstation or violating an exclusive resource boundary.

The governor never launches a process and owns no broker, risk, entry-veto, or
promotion authority.  A caller must still claim work through M155 before launch.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .experiment_queue import ExperimentResource


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _nonnegative_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return int(value)


def _percent(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or not 0.0 <= rendered <= 100.0:
        raise ValueError(f"{label} must be finite in [0, 100]")
    return rendered


class ResourceDeferralReason(StrEnum):
    CPU_PRESSURE = "cpu_pressure"
    CPU_CAPACITY = "cpu_capacity"
    RAM_CAPACITY = "ram_capacity"
    GPU_UNVERIFIABLE = "gpu_unverifiable"
    GPU_CAPACITY = "gpu_capacity"
    LANE_CAPACITY = "lane_capacity"
    EXCLUSIVE_CONFLICT = "exclusive_conflict"


@dataclass(frozen=True, slots=True)
class HardwareTelemetry:
    logical_cores: int
    cpu_percent: float
    available_ram_mb: int
    gpu_free_mb: int | None = None
    gpu_total_mb: int | None = None

    def __post_init__(self) -> None:
        if isinstance(self.logical_cores, bool) or int(self.logical_cores) != self.logical_cores or int(self.logical_cores) < 1:
            raise ValueError("logical_cores must be a positive integer")
        object.__setattr__(self, "logical_cores", int(self.logical_cores))
        object.__setattr__(self, "cpu_percent", _percent(self.cpu_percent, "cpu_percent"))
        object.__setattr__(self, "available_ram_mb", _nonnegative_int(self.available_ram_mb, "available_ram_mb"))
        if self.gpu_free_mb is not None:
            object.__setattr__(self, "gpu_free_mb", _nonnegative_int(self.gpu_free_mb, "gpu_free_mb"))
        if self.gpu_total_mb is not None:
            total = _nonnegative_int(self.gpu_total_mb, "gpu_total_mb")
            if total == 0:
                raise ValueError("gpu_total_mb must be positive when supplied")
            object.__setattr__(self, "gpu_total_mb", total)
        if self.gpu_free_mb is not None and self.gpu_total_mb is not None and self.gpu_free_mb > self.gpu_total_mb:
            raise ValueError("gpu_free_mb cannot exceed gpu_total_mb")


@dataclass(frozen=True, slots=True)
class ParallelFactoryPolicy:
    reserve_cpu_cores: int = 2
    reserve_ram_mb: int = 4096
    reserve_gpu_mb: int = 1024
    max_cpu_workers: int = 6
    max_forecast_workers: int = 1
    max_mt5_workers: int = 1
    max_ollama_workers: int = 1
    cpu_soft_percent: float = 75.0
    cpu_hard_percent: float = 92.0
    soft_pressure_fraction: float = 0.50
    version: str = "m162-resource-governor-v1"

    def __post_init__(self) -> None:
        for name in (
            "reserve_cpu_cores",
            "reserve_ram_mb",
            "reserve_gpu_mb",
            "max_cpu_workers",
            "max_forecast_workers",
            "max_mt5_workers",
            "max_ollama_workers",
        ):
            value = _nonnegative_int(getattr(self, name), name)
            object.__setattr__(self, name, value)
        if self.max_cpu_workers < 1 or self.max_forecast_workers < 1 or self.max_mt5_workers < 1 or self.max_ollama_workers < 1:
            raise ValueError("all M162 worker limits must be positive")
        object.__setattr__(self, "cpu_soft_percent", _percent(self.cpu_soft_percent, "cpu_soft_percent"))
        object.__setattr__(self, "cpu_hard_percent", _percent(self.cpu_hard_percent, "cpu_hard_percent"))
        if self.cpu_soft_percent >= self.cpu_hard_percent:
            raise ValueError("cpu_soft_percent must be below cpu_hard_percent")
        fraction = float(self.soft_pressure_fraction)
        if not math.isfinite(fraction) or not 0.0 < fraction <= 1.0:
            raise ValueError("soft_pressure_fraction must be finite in (0, 1]")
        object.__setattr__(self, "soft_pressure_fraction", fraction)
        version = str(self.version).strip()
        if not version:
            raise ValueError("parallel factory policy version required")
        object.__setattr__(self, "version", version)


@dataclass(frozen=True, slots=True)
class ResourceWorkRequest:
    job_fingerprint: str
    resource: ExperimentResource
    priority: int
    estimated_cpu_cores: int
    estimated_ram_mb: int
    estimated_gpu_mb: int = 0
    exclusive_key: str | None = None
    provider: str | None = None
    batch_key: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "job_fingerprint", _sha(self.job_fingerprint, "resource job"))
        if not -100 <= int(self.priority) <= 100:
            raise ValueError("resource request priority must be between -100 and 100")
        object.__setattr__(self, "priority", int(self.priority))
        cpu = _nonnegative_int(self.estimated_cpu_cores, "estimated_cpu_cores")
        if cpu < 1:
            raise ValueError("estimated_cpu_cores must be positive")
        object.__setattr__(self, "estimated_cpu_cores", cpu)
        object.__setattr__(self, "estimated_ram_mb", _nonnegative_int(self.estimated_ram_mb, "estimated_ram_mb"))
        object.__setattr__(self, "estimated_gpu_mb", _nonnegative_int(self.estimated_gpu_mb, "estimated_gpu_mb"))
        if self.exclusive_key is not None:
            key = str(self.exclusive_key).strip().lower()
            if not key:
                raise ValueError("exclusive_key cannot be blank")
            object.__setattr__(self, "exclusive_key", key)
        if self.provider is not None:
            provider = str(self.provider).strip().lower()
            if not provider:
                raise ValueError("provider cannot be blank")
            object.__setattr__(self, "provider", provider)
        if self.batch_key is not None:
            batch_key = str(self.batch_key).strip().lower()
            if not batch_key:
                raise ValueError("batch_key cannot be blank")
            object.__setattr__(self, "batch_key", batch_key)
        if self.resource is ExperimentResource.MT5_TESTER and self.exclusive_key is None:
            raise ValueError("MT5 tester work requires an exclusive terminal identity")
        if self.resource is ExperimentResource.OLLAMA:
            if self.exclusive_key is None:
                object.__setattr__(self, "exclusive_key", "ollama-local-runtime")
        if self.resource is ExperimentResource.FORECAST and self.provider is None:
            raise ValueError("forecast work requires provider identity")

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "job_fingerprint": self.job_fingerprint,
            "resource": self.resource.value,
            "priority": self.priority,
            "estimated_cpu_cores": self.estimated_cpu_cores,
            "estimated_ram_mb": self.estimated_ram_mb,
            "estimated_gpu_mb": self.estimated_gpu_mb,
            "exclusive_key": self.exclusive_key,
            "provider": self.provider,
            "batch_key": self.batch_key,
        }

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class DeferredWork:
    request: ResourceWorkRequest
    reason: ResourceDeferralReason


@dataclass(frozen=True, slots=True)
class ParallelAdmissionPlan:
    admitted: tuple[ResourceWorkRequest, ...]
    deferred: tuple[DeferredWork, ...]
    cpu_budget_cores: int
    ram_budget_mb: int
    gpu_budget_mb: int | None
    policy_version: str

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "admitted": [row.fingerprint for row in self.admitted],
                "deferred": [(row.request.fingerprint, row.reason.value) for row in self.deferred],
                "cpu_budget_cores": self.cpu_budget_cores,
                "ram_budget_mb": self.ram_budget_mb,
                "gpu_budget_mb": self.gpu_budget_mb,
                "policy_version": self.policy_version,
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


class AdaptiveParallelComputeGovernor:
    """Pure admission governor over one immutable telemetry snapshot."""

    def __init__(self, policy: ParallelFactoryPolicy = ParallelFactoryPolicy()) -> None:
        self.policy = policy

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def promotion_authorized(self) -> bool:
        return False

    def _cpu_budget(self, telemetry: HardwareTelemetry) -> int:
        base = min(self.policy.max_cpu_workers, max(0, telemetry.logical_cores - self.policy.reserve_cpu_cores))
        if telemetry.cpu_percent >= self.policy.cpu_hard_percent:
            return 0
        if telemetry.cpu_percent >= self.policy.cpu_soft_percent:
            return max(0, int(math.floor(base * self.policy.soft_pressure_fraction)))
        return base

    def plan(
        self,
        requests: Iterable[ResourceWorkRequest],
        *,
        telemetry: HardwareTelemetry,
    ) -> ParallelAdmissionPlan:
        rows = tuple(requests)
        job_ids = tuple(row.job_fingerprint for row in rows)
        if len(job_ids) != len(set(job_ids)):
            raise ValueError("parallel admission batch cannot contain duplicate job fingerprints")

        cpu_budget = self._cpu_budget(telemetry)
        ram_budget = max(0, telemetry.available_ram_mb - self.policy.reserve_ram_mb)
        gpu_budget = None if telemetry.gpu_free_mb is None else max(0, telemetry.gpu_free_mb - self.policy.reserve_gpu_mb)

        ordered = tuple(sorted(rows, key=lambda row: (-row.priority, row.resource.value, row.job_fingerprint)))
        admitted: list[ResourceWorkRequest] = []
        deferred: list[DeferredWork] = []
        used_cpu = 0
        used_ram = 0
        used_gpu = 0
        lane_counts = {resource: 0 for resource in ExperimentResource}
        exclusive_keys: set[str] = set()

        limits = {
            ExperimentResource.CPU_RESEARCH: self.policy.max_cpu_workers,
            ExperimentResource.FORECAST: self.policy.max_forecast_workers,
            ExperimentResource.MT5_TESTER: self.policy.max_mt5_workers,
            ExperimentResource.OLLAMA: self.policy.max_ollama_workers,
        }

        for row in ordered:
            if cpu_budget == 0:
                deferred.append(DeferredWork(row, ResourceDeferralReason.CPU_PRESSURE))
                continue
            if lane_counts[row.resource] >= limits[row.resource]:
                deferred.append(DeferredWork(row, ResourceDeferralReason.LANE_CAPACITY))
                continue
            if row.exclusive_key is not None and row.exclusive_key in exclusive_keys:
                deferred.append(DeferredWork(row, ResourceDeferralReason.EXCLUSIVE_CONFLICT))
                continue
            if used_cpu + row.estimated_cpu_cores > cpu_budget:
                deferred.append(DeferredWork(row, ResourceDeferralReason.CPU_CAPACITY))
                continue
            if used_ram + row.estimated_ram_mb > ram_budget:
                deferred.append(DeferredWork(row, ResourceDeferralReason.RAM_CAPACITY))
                continue
            if row.estimated_gpu_mb > 0:
                if gpu_budget is None:
                    deferred.append(DeferredWork(row, ResourceDeferralReason.GPU_UNVERIFIABLE))
                    continue
                if used_gpu + row.estimated_gpu_mb > gpu_budget:
                    deferred.append(DeferredWork(row, ResourceDeferralReason.GPU_CAPACITY))
                    continue

            admitted.append(row)
            lane_counts[row.resource] += 1
            used_cpu += row.estimated_cpu_cores
            used_ram += row.estimated_ram_mb
            used_gpu += row.estimated_gpu_mb
            if row.exclusive_key is not None:
                exclusive_keys.add(row.exclusive_key)

        return ParallelAdmissionPlan(
            admitted=tuple(admitted),
            deferred=tuple(deferred),
            cpu_budget_cores=cpu_budget,
            ram_budget_mb=ram_budget,
            gpu_budget_mb=gpu_budget,
            policy_version=self.policy.version,
        )
