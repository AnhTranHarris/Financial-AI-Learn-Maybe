from __future__ import annotations

"""M182 strategy/forecast interaction map from matched ablation evidence."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import mean
from typing import Iterable

from .forecast_ablation import ForecastAblationComparison, ForecastAblationVariant
from .forecast_specialization import ForecastContextBucket


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


class InteractionStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    BENEFICIAL = "beneficial"
    NEUTRAL = "neutral"
    HARMFUL = "harmful"


@dataclass(frozen=True, slots=True)
class StrategyForecastInteractionObservation:
    strategy_family: str
    bucket: ForecastContextBucket
    comparison: ForecastAblationComparison

    def __post_init__(self) -> None:
        family = str(self.strategy_family).strip().lower()
        if not family or "\n" in family or "\r" in family:
            raise ValueError("strategy_family must be one line")
        object.__setattr__(self, "strategy_family", family)

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m182-interaction-observation-v1", self.strategy_family, self.bucket.fingerprint, self.comparison.fingerprint))


@dataclass(frozen=True, slots=True)
class InteractionPolicy:
    minimum_observations: int = 3
    neutral_mean_return_delta: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= int(self.minimum_observations) <= 1_000_000:
            raise ValueError("minimum_observations out of range")
        neutral = float(self.neutral_mean_return_delta)
        if not math.isfinite(neutral) or neutral < 0:
            raise ValueError("neutral_mean_return_delta must be finite and nonnegative")
        object.__setattr__(self, "neutral_mean_return_delta", neutral)


@dataclass(frozen=True, slots=True)
class StrategyForecastInteractionCell:
    strategy_family: str
    bucket: ForecastContextBucket
    variant: ForecastAblationVariant
    status: InteractionStatus
    observation_count: int
    mean_net_return_delta: float | None
    mean_max_drawdown_delta: float | None
    worst_max_drawdown_delta: float | None
    beneficial_fraction: float | None
    harmful_fraction: float | None
    observation_fingerprints: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m182-interaction-cell-v1", self.strategy_family, self.bucket.fingerprint, self.variant.fingerprint, self.status.value, self.observation_count, self.mean_net_return_delta, self.mean_max_drawdown_delta, self.worst_max_drawdown_delta, self.beneficial_fraction, self.harmful_fraction, self.observation_fingerprints))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def provider_selection_authority(self) -> bool:
        return False

    @property
    def strategy_mutation_authority(self) -> bool:
        return False


def build_interaction_cell(
    strategy_family: str,
    bucket: ForecastContextBucket,
    variant: ForecastAblationVariant,
    observations: Iterable[StrategyForecastInteractionObservation],
    *,
    policy: InteractionPolicy = InteractionPolicy(),
) -> StrategyForecastInteractionCell:
    family = str(strategy_family).strip().lower()
    if not family:
        raise ValueError("strategy_family required")
    if variant.is_control:
        raise ValueError("interaction map requires a forecast variant, not NO_FORECAST control")
    rows = tuple(row for row in observations if row.strategy_family == family and row.bucket == bucket and row.comparison.variant == variant)
    identities = tuple((row.comparison.strategy_fingerprint, row.comparison.evaluation_fingerprint) for row in rows)
    if len(identities) != len(set(identities)):
        raise ValueError("interaction map cannot double-count a strategy/evaluation identity")
    fingerprints = tuple(sorted(row.fingerprint for row in rows))
    if len(rows) < policy.minimum_observations:
        return StrategyForecastInteractionCell(family, bucket, variant, InteractionStatus.INSUFFICIENT, len(rows), None, None, None, None, None, fingerprints)
    mean_delta = mean(row.comparison.net_return_delta for row in rows)
    threshold = policy.neutral_mean_return_delta
    status = InteractionStatus.BENEFICIAL if mean_delta > threshold else (InteractionStatus.HARMFUL if mean_delta < -threshold else InteractionStatus.NEUTRAL)
    return StrategyForecastInteractionCell(
        family,
        bucket,
        variant,
        status,
        len(rows),
        mean_delta,
        mean(row.comparison.max_drawdown_delta for row in rows),
        max(row.comparison.max_drawdown_delta for row in rows),
        mean(row.comparison.net_return_delta > threshold for row in rows),
        mean(row.comparison.net_return_delta < -threshold for row in rows),
        fingerprints,
    )
