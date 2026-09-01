from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Mapping, Sequence

from .experience import TradeSide
from .forecast_evaluation import ForecastScorecard
from .forecasting_v2 import (
    ForecastModelIdentity,
    ForecastTargetKind,
    ProbabilisticForecast,
    QuantilePoint,
)
from .market_clock import MarketClockAssessment
from .strategy_ir import StrategySpecV2


class ForecastTradeAction(StrEnum):
    WAIT = "wait"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"


@dataclass(frozen=True, slots=True)
class ForecastCouncilPolicy:
    minimum_probability: float = 0.55
    maximum_calibration_error: float = 0.10
    minimum_score_samples: int = 30
    minimum_net_edge_fraction: float = 0.0

    def __post_init__(self) -> None:
        if not 0.5 <= self.minimum_probability <= 1:
            raise ValueError("forecast probability threshold must lie in [0.5,1]")
        if self.maximum_calibration_error < 0 or self.minimum_score_samples < 1 or self.minimum_net_edge_fraction < 0:
            raise ValueError("forecast council thresholds are invalid")


@dataclass(frozen=True, slots=True)
class ForecastCouncilRequest:
    strategy: StrategySpecV2
    strategy_setup_present: bool
    forecasts: tuple[ProbabilisticForecast, ...]
    scorecards: tuple[ForecastScorecard, ...]
    market_clock: MarketClockAssessment
    reasoning_at: datetime
    estimated_round_trip_cost_fraction: float
    risk_allowed: bool

    def __post_init__(self) -> None:
        if self.reasoning_at.tzinfo is None or self.reasoning_at.utcoffset() is None:
            raise ValueError("forecast reasoning time must be timezone-aware")
        if not math.isfinite(self.estimated_round_trip_cost_fraction) or self.estimated_round_trip_cost_fraction < 0:
            raise ValueError("forecast costs must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class ForecastCouncilDecision:
    action: ForecastTradeAction
    net_edge_fraction: float
    favorable_probability: float
    analyst_reasons: tuple[str, ...]
    skeptic_reasons: tuple[str, ...]
    patience_reasons: tuple[str, ...]
    guardian_reasons: tuple[str, ...]
    fingerprint: str


def reason_about_forecast(
    request: ForecastCouncilRequest,
    policy: ForecastCouncilPolicy = ForecastCouncilPolicy(),
) -> ForecastCouncilDecision:
    """Forecasts may validate a frozen setup; they never manufacture one or override risk/clock."""
    analyst: list[str] = []
    skeptic: list[str] = []
    patience: list[str] = []
    guardian: list[str] = []
    direction = request.strategy.direction
    if not request.strategy_setup_present:
        analyst.append("frozen_strategy_setup_absent")
    if not request.forecasts:
        skeptic.append("forecast_evidence_absent")
    if any(row.key.issued_at > request.reasoning_at or request.reasoning_at > row.valid_until for row in request.forecasts):
        skeptic.append("forecast_not_point_in_time_valid")

    score_by_model = {row.model_fingerprint: row for row in request.scorecards}
    if len(score_by_model) != len(request.scorecards):
        skeptic.append("duplicate_model_scorecard")
    consensus_identities = {
        (
            row.key.symbol.upper(),
            row.key.timeframe.upper(),
            row.key.issued_at,
            row.key.origin_at,
            row.key.horizon_steps,
            row.key.target,
            row.key.regime,
            row.origin_value,
            row.context_hash,
        )
        for row in request.forecasts
    }
    consensus_compatible = len(consensus_identities) <= 1
    if not consensus_compatible:
        skeptic.append("forecast_consensus_identity_mismatch")
    usable = []
    for forecast in request.forecasts if consensus_compatible else ():
        if forecast.key.target not in {ForecastTargetKind.RETURN, ForecastTargetKind.PRICE}:
            skeptic.append(f"forecast_target_not_directional:{forecast.model.model_name}")
            continue
        score = score_by_model.get(forecast.model.fingerprint)
        if score is None:
            skeptic.append(f"scorecard_missing:{forecast.model.model_name}")
            continue
        key = forecast.key
        if (score.symbol, score.timeframe, score.horizon_steps, score.regime) != (
            key.symbol.upper(),
            key.timeframe.upper(),
            key.horizon_steps,
            key.regime,
        ):
            skeptic.append(f"scorecard_identity_mismatch:{forecast.model.model_name}")
            continue
        if score.count < policy.minimum_score_samples:
            skeptic.append(f"scorecard_sample_insufficient:{forecast.model.model_name}")
            continue
        if score.calibration_error > policy.maximum_calibration_error:
            skeptic.append(f"forecast_miscalibrated:{forecast.model.model_name}")
            continue
        weight = score.count / max(score.crps_approximation, 1e-12)
        usable.append((forecast, weight))

    expected = 0.0
    favorable_probability = 0.0
    if usable:
        total_weight = sum(weight for _, weight in usable)
        expected = sum(_expected_return(row) * weight for row, weight in usable) / total_weight
        favorable_probability = sum(_favorable_probability(row, direction) * weight for row, weight in usable) / total_weight
        analyst.append("calibrated_forecast_consensus_available")
    signed_edge = expected if direction is TradeSide.LONG else -expected
    net_edge = signed_edge - request.estimated_round_trip_cost_fraction
    if net_edge <= policy.minimum_net_edge_fraction:
        skeptic.append("forecast_edge_does_not_clear_costs")
    if favorable_probability < policy.minimum_probability:
        skeptic.append("favorable_probability_below_threshold")

    clock_allowed = (
        request.market_clock.long_entries_authorized
        if direction is TradeSide.LONG
        else request.market_clock.short_entries_authorized
    )
    if not clock_allowed:
        patience.append(f"market_clock:{request.market_clock.state.value}")
    if not request.risk_allowed:
        guardian.append("risk_not_approved")

    enter = (
        request.strategy_setup_present
        and bool(usable)
        and not skeptic
        and clock_allowed
        and request.risk_allowed
    )
    if enter:
        action = ForecastTradeAction.ENTER_LONG if direction is TradeSide.LONG else ForecastTradeAction.ENTER_SHORT
        patience.append("setup_clock_and_forecast_aligned")
        guardian.append("risk_gate_clear")
    else:
        action = ForecastTradeAction.WAIT
        if not patience:
            patience.append("wait_for_complete_forecast_evidence")
        if not guardian:
            guardian.append("no_risk_override_requested")
    payload = {
        "strategy_hash": request.strategy.strategy_hash,
        "action": action.value,
        "forecast_hashes": tuple(row.fingerprint for row in request.forecasts),
        "score_models": tuple(sorted(score_by_model)),
        "clock": request.market_clock.state.value,
        "reasoning_at": request.reasoning_at.isoformat(),
        "edge": net_edge,
        "probability": favorable_probability,
        "reasons": (analyst, skeptic, patience, guardian),
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return ForecastCouncilDecision(
        action,
        net_edge,
        favorable_probability,
        tuple(analyst),
        tuple(skeptic),
        tuple(patience),
        tuple(guardian),
        fingerprint,
    )


def ensemble_forecasts(
    forecasts: Sequence[ProbabilisticForecast],
    weights_by_model_fingerprint: Mapping[str, float],
) -> ProbabilisticForecast:
    rows = tuple(forecasts)
    if len(rows) < 2:
        raise ValueError("forecast ensemble requires at least two members")
    first = rows[0]
    if any(
        row.key != first.key
        or row.origin_value != first.origin_value
        or row.context_hash != first.context_hash
        or tuple(point.level for point in row.quantiles) != tuple(point.level for point in first.quantiles)
        for row in rows[1:]
    ):
        raise ValueError("forecast ensemble cannot mix keys, origins, contexts or quantile grids")
    weights = tuple(weights_by_model_fingerprint.get(row.model.fingerprint, 0.0) for row in rows)
    if any(not math.isfinite(value) or value <= 0 for value in weights):
        raise ValueError("every ensemble member requires a positive finite weight")
    total = sum(weights)
    quantiles = tuple(
        QuantilePoint(point.level, sum(row.quantiles[index].value * weight for row, weight in zip(rows, weights)) / total)
        for index, point in enumerate(first.quantiles)
    )
    member_hash = sha256("".join(sorted(row.fingerprint for row in rows)).encode("ascii")).hexdigest()
    config_hash = sha256(json.dumps(tuple(weights), separators=(",", ":")).encode("utf-8")).hexdigest()
    return ProbabilisticForecast(
        ForecastModelIdentity("dusty", "calibrated_ensemble", "1", member_hash, config_hash),
        first.key,
        first.origin_value,
        quantiles,
        sum(row.probability_up * weight for row, weight in zip(rows, weights)) / total,
        min(row.training_cutoff for row in rows),
        min(row.valid_until for row in rows),
        first.context_hash,
    )


def _expected_return(forecast: ProbabilisticForecast) -> float:
    if forecast.key.target is ForecastTargetKind.RETURN:
        return forecast.median
    if forecast.key.target is ForecastTargetKind.PRICE:
        return forecast.median / forecast.origin_value - 1
    return 0.0


def _favorable_probability(forecast: ProbabilisticForecast, direction: TradeSide) -> float:
    return forecast.probability_up if direction is TradeSide.LONG else 1 - forecast.probability_up
