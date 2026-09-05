from __future__ import annotations

"""M168 parameter-neighborhood stability assessment.

A single optimum is not treated as robust.  Stability is measured against a
pre-declared local neighborhood around one frozen parameter fingerprint.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import median
from typing import Iterable


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


def _unit(value: float, label: str) -> float:
    rendered = _finite(value, label)
    if not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{label} must be in [0,1]")
    return rendered


class NeighborhoodStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    STABLE = "stable"
    UNSTABLE = "unstable"


@dataclass(frozen=True, slots=True)
class ParameterPointResult:
    parameter_fingerprint: str
    normalized_distance: float
    score: float
    max_drawdown: float
    passed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameter_fingerprint", _sha(self.parameter_fingerprint, "parameter point"))
        distance = _finite(self.normalized_distance, "parameter distance")
        if distance < 0:
            raise ValueError("parameter distance cannot be negative")
        object.__setattr__(self, "normalized_distance", distance)
        object.__setattr__(self, "score", _finite(self.score, "parameter score"))
        drawdown = _finite(self.max_drawdown, "parameter drawdown")
        if drawdown < 0:
            raise ValueError("parameter drawdown cannot be negative")
        object.__setattr__(self, "max_drawdown", drawdown)


@dataclass(frozen=True, slots=True)
class NeighborhoodPolicy:
    radius: float = 1.0
    minimum_neighbors: int = 4
    minimum_stable_fraction: float = 0.60
    maximum_score_degradation: float = 0.25
    maximum_drawdown_multiplier: float = 1.50

    def __post_init__(self) -> None:
        radius = _finite(self.radius, "neighborhood radius")
        if radius <= 0:
            raise ValueError("neighborhood radius must be positive")
        object.__setattr__(self, "radius", radius)
        if not 1 <= int(self.minimum_neighbors) <= 10_000:
            raise ValueError("minimum_neighbors out of range")
        object.__setattr__(self, "minimum_neighbors", int(self.minimum_neighbors))
        object.__setattr__(self, "minimum_stable_fraction", _unit(self.minimum_stable_fraction, "minimum_stable_fraction"))
        degradation = _unit(self.maximum_score_degradation, "maximum_score_degradation")
        object.__setattr__(self, "maximum_score_degradation", degradation)
        multiplier = _finite(self.maximum_drawdown_multiplier, "maximum_drawdown_multiplier")
        if multiplier < 1.0:
            raise ValueError("maximum_drawdown_multiplier must be >= 1")
        object.__setattr__(self, "maximum_drawdown_multiplier", multiplier)


@dataclass(frozen=True, slots=True)
class NeighborhoodAssessment:
    center_parameter_fingerprint: str
    status: NeighborhoodStatus
    neighbor_count: int
    stable_neighbor_count: int
    stable_fraction: float
    center_score: float
    neighbor_median_score: float | None
    worst_neighbor_score: float | None
    maximum_relative_degradation: float | None
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "center_parameter_fingerprint", _sha(self.center_parameter_fingerprint, "center parameter"))
        if not self.reason.strip():
            raise ValueError("neighborhood assessment reason required")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m168-neighborhood-v1",
                "center": self.center_parameter_fingerprint,
                "status": self.status.value,
                "neighbor_count": self.neighbor_count,
                "stable_neighbor_count": self.stable_neighbor_count,
                "stable_fraction": self.stable_fraction,
                "center_score": self.center_score,
                "neighbor_median_score": self.neighbor_median_score,
                "worst_neighbor_score": self.worst_neighbor_score,
                "maximum_relative_degradation": self.maximum_relative_degradation,
                "reason": self.reason,
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


def assess_parameter_neighborhood(
    center: ParameterPointResult,
    neighbors: Iterable[ParameterPointResult],
    *,
    policy: NeighborhoodPolicy = NeighborhoodPolicy(),
) -> NeighborhoodAssessment:
    if center.normalized_distance != 0.0:
        raise ValueError("center parameter result must have zero normalized distance")
    rows = tuple(neighbors)
    identities = tuple(row.parameter_fingerprint for row in rows)
    if center.parameter_fingerprint in identities or len(identities) != len(set(identities)):
        raise ValueError("parameter neighborhood identities must be unique and exclude center")
    local = tuple(row for row in rows if 0.0 < row.normalized_distance <= policy.radius)
    if len(local) < policy.minimum_neighbors:
        return NeighborhoodAssessment(
            center.parameter_fingerprint,
            NeighborhoodStatus.INSUFFICIENT,
            len(local),
            0,
            0.0,
            center.score,
            None,
            None,
            None,
            "insufficient local parameter neighbors",
        )

    scale = max(abs(center.score), 1e-12)
    degradations = [max(0.0, (center.score - row.score) / scale) for row in local]
    drawdown_limit = max(center.max_drawdown, 1e-12) * policy.maximum_drawdown_multiplier
    stable = [
        row
        for row, degradation in zip(local, degradations)
        if row.passed
        and degradation <= policy.maximum_score_degradation
        and row.max_drawdown <= drawdown_limit
    ]
    stable_fraction = len(stable) / len(local)
    status = NeighborhoodStatus.STABLE if center.passed and stable_fraction >= policy.minimum_stable_fraction else NeighborhoodStatus.UNSTABLE
    return NeighborhoodAssessment(
        center.parameter_fingerprint,
        status,
        len(local),
        len(stable),
        stable_fraction,
        center.score,
        median(row.score for row in local),
        min(row.score for row in local),
        max(degradations),
        "local parameter plateau satisfies stability policy" if status is NeighborhoodStatus.STABLE else "local parameter neighborhood is too fragile",
    )
