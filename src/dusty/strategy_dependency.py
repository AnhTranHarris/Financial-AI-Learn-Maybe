from __future__ import annotations

"""M173 synchronized strategy dependency matrix."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
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


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("dependency timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _pearson(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    mean_l = sum(left) / len(left)
    mean_r = sum(right) / len(right)
    num = sum((a - mean_l) * (b - mean_r) for a, b in zip(left, right))
    den_l = math.sqrt(sum((a - mean_l) ** 2 for a in left))
    den_r = math.sqrt(sum((b - mean_r) ** 2 for b in right))
    if den_l == 0.0 or den_r == 0.0:
        return 0.0
    return max(-1.0, min(1.0, num / (den_l * den_r)))


class DependencyStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    DIVERSIFIED = "diversified"
    CONCENTRATED = "concentrated"


@dataclass(frozen=True, slots=True)
class StrategyReturnSeries:
    strategy_fingerprint: str
    timestamps: tuple[datetime, ...]
    returns: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_fingerprint", _sha(self.strategy_fingerprint, "dependency strategy"))
        timestamps = tuple(_aware(value) for value in self.timestamps)
        object.__setattr__(self, "timestamps", timestamps)
        values = tuple(float(value) for value in self.returns)
        if len(timestamps) != len(values) or not timestamps:
            raise ValueError("dependency series timestamps/returns must be nonempty and aligned")
        if len(set(timestamps)) != len(timestamps) or tuple(sorted(timestamps)) != timestamps:
            raise ValueError("dependency timestamps must be unique and increasing")
        if any(not math.isfinite(value) for value in values):
            raise ValueError("dependency returns must be finite")
        object.__setattr__(self, "returns", values)

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "strategy": self.strategy_fingerprint,
                "timestamps": [value.isoformat() for value in self.timestamps],
                "returns": list(self.returns),
            }
        )


@dataclass(frozen=True, slots=True)
class StrategyDependencyPair:
    left_strategy_fingerprint: str
    right_strategy_fingerprint: str
    correlation: float
    co_loss_fraction: float

    def __post_init__(self) -> None:
        left = _sha(self.left_strategy_fingerprint, "dependency left strategy")
        right = _sha(self.right_strategy_fingerprint, "dependency right strategy")
        if left >= right:
            raise ValueError("dependency pair identities must be canonical and distinct")
        object.__setattr__(self, "left_strategy_fingerprint", left)
        object.__setattr__(self, "right_strategy_fingerprint", right)
        if not -1.0 <= self.correlation <= 1.0 or not 0.0 <= self.co_loss_fraction <= 1.0:
            raise ValueError("dependency pair metrics out of range")


@dataclass(frozen=True, slots=True)
class DependencyPolicy:
    minimum_observations: int = 30
    maximum_absolute_correlation: float = 0.80
    maximum_co_loss_fraction: float = 0.70

    def __post_init__(self) -> None:
        if not 2 <= int(self.minimum_observations) <= 10_000_000:
            raise ValueError("minimum_observations out of range")
        for name in ("maximum_absolute_correlation", "maximum_co_loss_fraction"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0,1]")


@dataclass(frozen=True, slots=True)
class StrategyDependencyMatrix:
    status: DependencyStatus
    strategy_fingerprints: tuple[str, ...]
    observation_count: int
    pairs: tuple[StrategyDependencyPair, ...]
    maximum_absolute_correlation: float | None
    maximum_co_loss_fraction: float | None
    reason: str

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m173-strategy-dependency-v1",
                "status": self.status.value,
                "strategies": list(self.strategy_fingerprints),
                "observation_count": self.observation_count,
                "pairs": [
                    {
                        "left": row.left_strategy_fingerprint,
                        "right": row.right_strategy_fingerprint,
                        "correlation": row.correlation,
                        "co_loss_fraction": row.co_loss_fraction,
                    }
                    for row in self.pairs
                ],
                "maximum_absolute_correlation": self.maximum_absolute_correlation,
                "maximum_co_loss_fraction": self.maximum_co_loss_fraction,
                "reason": self.reason,
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


def build_strategy_dependency_matrix(
    series: Iterable[StrategyReturnSeries],
    *,
    policy: DependencyPolicy = DependencyPolicy(),
) -> StrategyDependencyMatrix:
    rows = tuple(series)
    if len(rows) < 2:
        raise ValueError("dependency matrix requires at least two strategies")
    identities = tuple(row.strategy_fingerprint for row in rows)
    if len(identities) != len(set(identities)):
        raise ValueError("dependency matrix strategy identities must be unique")
    canonical = tuple(sorted(rows, key=lambda row: row.strategy_fingerprint))
    reference_times = canonical[0].timestamps
    if any(row.timestamps != reference_times for row in canonical[1:]):
        raise ValueError("dependency series must share an exact synchronized timestamp grid")
    if len(reference_times) < policy.minimum_observations:
        return StrategyDependencyMatrix(
            DependencyStatus.INSUFFICIENT,
            tuple(row.strategy_fingerprint for row in canonical),
            len(reference_times),
            (),
            None,
            None,
            "insufficient synchronized observations",
        )

    pairs: list[StrategyDependencyPair] = []
    for index, left in enumerate(canonical):
        for right in canonical[index + 1 :]:
            corr = _pearson(left.returns, right.returns)
            loss_union = sum(1 for a, b in zip(left.returns, right.returns) if a < 0 or b < 0)
            co_losses = sum(1 for a, b in zip(left.returns, right.returns) if a < 0 and b < 0)
            co_loss_fraction = 0.0 if loss_union == 0 else co_losses / loss_union
            pairs.append(StrategyDependencyPair(left.strategy_fingerprint, right.strategy_fingerprint, corr, co_loss_fraction))
    max_corr = max(abs(row.correlation) for row in pairs)
    max_co_loss = max(row.co_loss_fraction for row in pairs)
    status = (
        DependencyStatus.DIVERSIFIED
        if max_corr <= policy.maximum_absolute_correlation and max_co_loss <= policy.maximum_co_loss_fraction
        else DependencyStatus.CONCENTRATED
    )
    return StrategyDependencyMatrix(
        status,
        tuple(row.strategy_fingerprint for row in canonical),
        len(reference_times),
        tuple(pairs),
        max_corr,
        max_co_loss,
        "strategy dependencies remain below declared concentration limits" if status is DependencyStatus.DIVERSIFIED else "strategy dependency concentration exceeds declared limits",
    )
