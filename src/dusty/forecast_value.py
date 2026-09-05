from __future__ import annotations

"""M180 research value-of-information accounting for forecast ablations."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math

from .forecast_ablation import AblationEffect, ForecastAblationComparison


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite_nonnegative(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return rendered


class InformationValueStatus(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


@dataclass(frozen=True, slots=True)
class ForecastInformationCost:
    variant_fingerprint: str
    wall_seconds: float
    cpu_seconds: float
    gpu_seconds: float
    external_cost: float = 0.0

    def __post_init__(self) -> None:
        fingerprint = str(self.variant_fingerprint).strip().lower()
        if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
            raise ValueError("information cost requires variant SHA-256")
        object.__setattr__(self, "variant_fingerprint", fingerprint)
        for name in ("wall_seconds", "cpu_seconds", "gpu_seconds", "external_cost"):
            object.__setattr__(self, name, _finite_nonnegative(getattr(self, name), name))
        if self.wall_seconds == 0 and self.cpu_seconds == 0 and self.gpu_seconds == 0:
            raise ValueError("information cost requires measured compute or latency")

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m180-information-cost-v1", self.variant_fingerprint, self.wall_seconds, self.cpu_seconds, self.gpu_seconds, self.external_cost))


@dataclass(frozen=True, slots=True)
class InformationValuePolicy:
    gpu_second_weight: float = 1.0
    neutral_return_delta: float = 0.0

    def __post_init__(self) -> None:
        gpu_weight = _finite_nonnegative(self.gpu_second_weight, "gpu_second_weight")
        if gpu_weight == 0:
            raise ValueError("gpu_second_weight must be positive")
        object.__setattr__(self, "gpu_second_weight", gpu_weight)
        object.__setattr__(self, "neutral_return_delta", _finite_nonnegative(self.neutral_return_delta, "neutral_return_delta"))


@dataclass(frozen=True, slots=True)
class ForecastInformationValue:
    comparison_fingerprint: str
    cost_fingerprint: str
    status: InformationValueStatus
    net_return_delta: float
    weighted_compute_seconds: float
    value_per_compute_second: float
    wall_seconds: float
    external_cost: float

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m180-voi-v1", self.comparison_fingerprint, self.cost_fingerprint, self.status.value, self.net_return_delta, self.weighted_compute_seconds, self.value_per_compute_second, self.wall_seconds, self.external_cost))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def allocation_authority(self) -> bool:
        return False


def measure_information_value(
    comparison: ForecastAblationComparison,
    cost: ForecastInformationCost,
    *,
    policy: InformationValuePolicy = InformationValuePolicy(),
) -> ForecastInformationValue:
    if cost.variant_fingerprint != comparison.variant.fingerprint:
        raise ValueError("information cost/ablation variant identity drift")
    weighted = cost.cpu_seconds + policy.gpu_second_weight * cost.gpu_seconds
    if weighted <= 0:
        weighted = cost.wall_seconds
    delta = comparison.net_return_delta
    threshold = policy.neutral_return_delta
    if delta > threshold and comparison.effect is not AblationEffect.HARMFUL:
        status = InformationValueStatus.POSITIVE
    elif delta < -threshold or comparison.effect is AblationEffect.HARMFUL:
        status = InformationValueStatus.NEGATIVE
    else:
        status = InformationValueStatus.NEUTRAL
    return ForecastInformationValue(comparison.fingerprint, cost.fingerprint, status, delta, weighted, delta / weighted, cost.wall_seconds, cost.external_cost)
