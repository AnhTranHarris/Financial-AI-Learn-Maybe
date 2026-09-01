from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import fmean
from typing import Iterable, Sequence

from .forecast_dataset import RollingForecastExample
from .forecasting_v2 import ForecastRealization, ProbabilisticForecast


def pinball_loss(level: float, predicted: float, realized: float) -> float:
    if not 0 < level < 1 or not all(math.isfinite(value) for value in (predicted, realized)):
        raise ValueError("pinball inputs are invalid")
    error = realized - predicted
    return level * error if error >= 0 else (level - 1) * error


@dataclass(frozen=True, slots=True)
class ForecastScorecard:
    model_fingerprint: str
    symbol: str
    timeframe: str
    horizon_steps: int
    regime: str
    count: int
    median_mae: float
    mean_pinball: float
    crps_approximation: float
    brier_score: float
    direction_accuracy: float
    calibration_error: float

    def __post_init__(self) -> None:
        if self.count < 1 or not self.model_fingerprint.strip():
            raise ValueError("forecast scorecard identity/count is invalid")
        numeric = (
            self.median_mae,
            self.mean_pinball,
            self.crps_approximation,
            self.brier_score,
            self.direction_accuracy,
            self.calibration_error,
        )
        if any(not math.isfinite(value) or value < 0 for value in numeric):
            raise ValueError("forecast scores must be finite and nonnegative")


def score_forecasts(
    forecasts: Iterable[ProbabilisticForecast],
    realizations: Iterable[ForecastRealization],
) -> ForecastScorecard:
    predictions = tuple(forecasts)
    actuals = tuple(realizations)
    if not predictions or len(predictions) != len(actuals):
        raise ValueError("forecast scoring requires equal nonempty samples")
    identity = {
        (
            row.model.fingerprint,
            row.key.symbol.upper(),
            row.key.timeframe.upper(),
            row.key.horizon_steps,
            row.key.target,
            row.key.regime,
        )
        for row in predictions
    }
    if len(identity) != 1:
        raise ValueError("scorecards cannot mix models, targets, horizons or regimes")
    model, symbol, timeframe, horizon, _, regime = next(iter(identity))
    by_key = {
        (row.symbol.upper(), row.timeframe.upper(), row.issued_at, row.horizon_steps, row.target, row.regime): row
        for row in actuals
    }
    if len(by_key) != len(actuals):
        raise ValueError("forecast realizations must be unique")
    matched: list[tuple[ProbabilisticForecast, ForecastRealization]] = []
    for forecast in predictions:
        key = forecast.key
        realization = by_key.get((key.symbol.upper(), key.timeframe.upper(), key.issued_at, key.horizon_steps, key.target, key.regime))
        if realization is None or not realization.matches(forecast):
            raise ValueError("forecast and realization identities do not match")
        matched.append((forecast, realization))

    losses = [
        pinball_loss(point.level, point.value, realization.value)
        for forecast, realization in matched
        for point in forecast.quantiles
    ]
    levels = tuple(point.level for point in predictions[0].quantiles)
    if any(tuple(point.level for point in forecast.quantiles) != levels for forecast in predictions):
        raise ValueError("scorecards require a common quantile grid")
    calibration = fmean(
        abs(fmean(float(realization.value <= forecast.quantile(level)) for forecast, realization in matched) - level)
        for level in levels
    )
    brier = fmean(
        (forecast.probability_up - float(realization.value > _direction_threshold(forecast))) ** 2
        for forecast, realization in matched
    )
    direction = fmean(
        float((forecast.probability_up >= 0.5) == (realization.value > _direction_threshold(forecast)))
        for forecast, realization in matched
    )
    median_mae = fmean(abs(forecast.median - realization.value) for forecast, realization in matched)
    mean_pinball = fmean(losses)
    return ForecastScorecard(
        model,
        symbol,
        timeframe,
        horizon,
        regime,
        len(matched),
        median_mae,
        mean_pinball,
        2 * mean_pinball,
        brier,
        direction,
        calibration,
    )


def _direction_threshold(forecast: ProbabilisticForecast) -> float:
    return forecast.origin_value if forecast.key.target.value == "price" else 0.0


@dataclass(frozen=True, slots=True)
class PurgedWalkForwardSplit:
    train: tuple[RollingForecastExample, ...]
    test: tuple[RollingForecastExample, ...]
    train_cutoff: datetime
    test_start: datetime

    def __post_init__(self) -> None:
        if not self.train or not self.test:
            raise ValueError("purged walk-forward split cannot be empty")
        if any(row.target_known_at > self.train_cutoff for row in self.train):
            raise ValueError("training target was unknown at cutoff")
        if any(row.features.as_of < self.test_start for row in self.test):
            raise ValueError("test sample precedes test start")


def purged_walk_forward_splits(
    examples: Sequence[RollingForecastExample],
    *,
    minimum_train_size: int,
    test_size: int,
    embargo: timedelta = timedelta(0),
) -> tuple[PurgedWalkForwardSplit, ...]:
    if minimum_train_size < 2 or test_size < 1 or embargo < timedelta(0):
        raise ValueError("walk-forward widths/embargo are invalid")
    rows = tuple(examples)
    if tuple(sorted(rows, key=lambda item: item.features.as_of)) != rows:
        raise ValueError("walk-forward examples must be chronological")
    splits = []
    test_index = minimum_train_size
    while test_index < len(rows):
        test = rows[test_index : test_index + test_size]
        if not test:
            break
        test_start = test[0].features.as_of
        cutoff = test_start - embargo
        train = tuple(row for row in rows[:test_index] if row.target_known_at <= cutoff)
        if len(train) >= minimum_train_size:
            splits.append(PurgedWalkForwardSplit(train, test, cutoff, test_start))
        test_index += test_size
    return tuple(splits)


@dataclass(frozen=True, slots=True)
class ChallengerEvidence:
    candidate: ForecastScorecard
    baseline: ForecastScorecard
    profitable_after_costs: bool
    stable_neighborhood: bool
    native_parity_passed: bool


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    promoted: bool
    reasons: tuple[str, ...]


def assess_challenger(
    evidence: ChallengerEvidence,
    *,
    minimum_samples: int = 100,
    maximum_calibration_error: float = 0.10,
    minimum_crps_improvement: float = 0.0,
) -> PromotionDecision:
    if minimum_samples < 1 or maximum_calibration_error < 0 or minimum_crps_improvement < 0:
        raise ValueError("promotion thresholds are invalid")
    reasons = []
    candidate, baseline = evidence.candidate, evidence.baseline
    if (candidate.symbol, candidate.timeframe, candidate.horizon_steps, candidate.regime) != (
        baseline.symbol,
        baseline.timeframe,
        baseline.horizon_steps,
        baseline.regime,
    ):
        reasons.append("baseline_identity_mismatch")
    if candidate.count < minimum_samples:
        reasons.append("insufficient_out_of_sample_observations")
    if candidate.calibration_error > maximum_calibration_error:
        reasons.append("forecast_miscalibrated")
    if candidate.crps_approximation > baseline.crps_approximation - minimum_crps_improvement:
        reasons.append("baseline_not_beaten")
    if not evidence.profitable_after_costs:
        reasons.append("not_profitable_after_costs")
    if not evidence.stable_neighborhood:
        reasons.append("parameter_neighborhood_unstable")
    if not evidence.native_parity_passed:
        reasons.append("native_mt5_parity_missing")
    return PromotionDecision(not reasons, tuple(reasons))
