from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from statistics import fmean
from math import isfinite
from typing import Iterable

from .core import EvidenceItem


@dataclass(frozen=True, slots=True)
class Forecast:
    provider: str
    at: datetime
    horizon_steps: int
    origin: float
    point: float
    lower: float | None = None
    upper: float | None = None

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("forecast provider is required")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("forecast timestamp must be timezone-aware")
        if type(self.horizon_steps) is not int or self.horizon_steps < 1:
            raise ValueError("horizon_steps must be positive")
        if any(isinstance(v, bool) or not isfinite(v) or v <= 0 for v in (self.origin, self.point)):
            raise ValueError("forecast prices must be finite and positive")
        if (self.lower is None) != (self.upper is None):
            raise ValueError("forecast interval requires both lower and upper")
        if self.lower is not None and not self.lower <= self.point <= self.upper:
            raise ValueError("forecast point must lie inside interval")
        if self.lower is not None and any(isinstance(v, bool) or not isfinite(v) or v <= 0
                                          for v in (self.lower, self.upper)):
            raise ValueError("forecast interval must be finite and positive")

    @property
    def predicted_return(self) -> float:
        return (self.point - self.origin) / self.origin


@dataclass(frozen=True, slots=True)
class RealizedTarget:
    at: datetime
    horizon_steps: int
    origin: float
    target: float

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("target timestamp must be timezone-aware")
        if self.horizon_steps < 1 or self.origin <= 0 or self.target <= 0:
            raise ValueError("invalid realized target")


@dataclass(frozen=True, slots=True)
class ForecastScore:
    provider: str
    count: int
    mae: float
    directional_accuracy: float
    interval_coverage: float | None


def score_forecasts(
    forecasts: Iterable[Forecast],
    realized: Iterable[RealizedTarget],
) -> tuple[ForecastScore, ...]:
    """Score providers on matched out-of-sample targets with no trading authority."""
    targets = {(item.at, item.horizon_steps): item for item in realized}
    grouped: dict[str, list[tuple[Forecast, RealizedTarget]]] = defaultdict(list)
    for forecast in forecasts:
        target = targets.get((forecast.at, forecast.horizon_steps))
        if target is None:
            continue
        if abs(forecast.origin - target.origin) > 1e-12:
            raise ValueError("forecast and realized target disagree on origin")
        grouped[forecast.provider].append((forecast, target))

    scores: list[ForecastScore] = []
    for provider, pairs in grouped.items():
        absolute_errors = [abs(forecast.point - target.target) for forecast, target in pairs]
        direction_hits = []
        coverage_hits = []
        for forecast, target in pairs:
            predicted_delta = forecast.point - forecast.origin
            realized_delta = target.target - target.origin
            direction_hits.append(
                (predicted_delta > 0 and realized_delta > 0)
                or (predicted_delta < 0 and realized_delta < 0)
                or (predicted_delta == 0 and realized_delta == 0)
            )
            if forecast.lower is not None:
                coverage_hits.append(forecast.lower <= target.target <= forecast.upper)
        scores.append(
            ForecastScore(
                provider=provider,
                count=len(pairs),
                mae=fmean(absolute_errors),
                directional_accuracy=sum(direction_hits) / len(direction_hits),
                interval_coverage=(
                    sum(coverage_hits) / len(coverage_hits) if coverage_hits else None
                ),
            )
        )
    return tuple(sorted(scores, key=lambda item: (item.mae, -item.directional_accuracy, item.provider)))


def forecast_evidence(forecast: Forecast, symbol: str) -> tuple[EvidenceItem, ...]:
    """Translate a model forecast into evidence; never into a trade decision."""
    prefix = f"forecast:{forecast.provider}:{forecast.horizon_steps}"
    items = [
        EvidenceItem(
            key=f"{prefix}:return",
            value=forecast.predicted_return,
            source=forecast.provider,
            observed_at=forecast.at,
            category="forecast",
            provenance=f"forecast:{forecast.provider}:{symbol.upper()}",
        ),
        EvidenceItem(
            key=f"{prefix}:point",
            value=forecast.point,
            source=forecast.provider,
            observed_at=forecast.at,
            category="forecast",
            provenance=f"forecast:{forecast.provider}:{symbol.upper()}",
        ),
    ]
    if forecast.lower is not None:
        items.extend(
            (
                EvidenceItem(
                    key=f"{prefix}:lower",
                    value=forecast.lower,
                    source=forecast.provider,
                    observed_at=forecast.at,
                    category="forecast",
                    provenance=f"forecast:{forecast.provider}:{symbol.upper()}",
                ),
                EvidenceItem(
                    key=f"{prefix}:upper",
                    value=forecast.upper,
                    source=forecast.provider,
                    observed_at=forecast.at,
                    category="forecast",
                    provenance=f"forecast:{forecast.provider}:{symbol.upper()}",
                ),
            )
        )
    return tuple(items)
