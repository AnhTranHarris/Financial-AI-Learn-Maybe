"""Research diagnostics that separate selection skill from reduced activity/exposure.

These functions are post-hoc research attribution only. They do not authorize entries,
change sizing, optimize thresholds, or promote a strategy. Their purpose is to make two
common confounders explicit: an entry veto can look better merely by trading less, and a
cost stress can look better merely because higher costs force smaller positions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import math
from typing import Iterable, Mapping

from .experience import TradeSide
from .forecasting import Forecast
from .runtime import RuntimeTrade


class ForecastStance(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


def forecast_stance(forecast: Forecast, direction_threshold: float) -> ForecastStance:
    if isinstance(direction_threshold, bool) or not math.isfinite(direction_threshold) or direction_threshold < 0:
        raise ValueError("direction_threshold_must_be_finite_and_nonnegative")
    value = forecast.predicted_return
    if value > direction_threshold:
        return ForecastStance.BULLISH
    if value < -direction_threshold:
        return ForecastStance.BEARISH
    return ForecastStance.NEUTRAL


@dataclass(frozen=True, slots=True)
class ForecastVetoDiagnostic:
    provider: str
    horizon_steps: int
    direction_threshold: float
    issued_forecasts: int
    bullish_forecasts: int
    bearish_forecasts: int
    neutral_forecasts: int
    baseline_entries: int
    entries_with_forecast: int
    missing_entry_forecasts: int
    favorable_entries: int
    neutral_entries: int
    conflicting_entries: int
    conflicting_winners: int
    conflicting_losers: int
    conflicting_flat: int
    conflicting_net_pnl: float

    @property
    def bearish_fraction(self) -> float:
        return self.bearish_forecasts / self.issued_forecasts if self.issued_forecasts else 0.0

    @property
    def conflict_fraction(self) -> float:
        return self.conflicting_entries / self.entries_with_forecast if self.entries_with_forecast else 0.0


def audit_forecast_veto(
    forecasts: Iterable[Forecast],
    baseline_trades: Iterable[RuntimeTrade],
    net_pnl_by_entry: Mapping[datetime, float],
    *,
    provider: str,
    horizon_steps: int,
    direction_threshold: float,
) -> ForecastVetoDiagnostic:
    """Attribute a forecast's stance at frozen baseline entry times.

    Baseline trades stay frozen. The function does not simulate the filtered strategy and
    therefore cannot be distorted by later occupancy/cooldown changes. P&L labels are supplied
    by the caller so "winner" and "loser" use the campaign's own reconciled net-P&L definition.
    """
    if not provider.strip() or type(horizon_steps) is not int or horizon_steps < 1:
        raise ValueError("provider_and_positive_horizon_required")
    rows = tuple(row for row in forecasts if row.provider == provider and row.horizon_steps == horizon_steps)
    by_time: dict[datetime, Forecast] = {}
    for row in rows:
        if row.at in by_time:
            raise ValueError("duplicate_forecast_for_provider_horizon_and_time")
        by_time[row.at] = row
    stances = [forecast_stance(row, direction_threshold) for row in rows]
    bullish = stances.count(ForecastStance.BULLISH)
    bearish = stances.count(ForecastStance.BEARISH)
    neutral = stances.count(ForecastStance.NEUTRAL)

    baseline = tuple(baseline_trades)
    if len({trade.entry_at for trade in baseline}) != len(baseline):
        raise ValueError("baseline_entries_must_be_unique_for_veto_attribution")
    if any(trade.entry_at not in net_pnl_by_entry for trade in baseline):
        raise ValueError("every_baseline_entry_requires_reconciled_net_pnl")
    if any(isinstance(value, bool) or not math.isfinite(value) for value in net_pnl_by_entry.values()):
        raise ValueError("net_pnl_labels_must_be_finite")

    favorable = neutral_entries = conflicts = winners = losers = flat = 0
    conflict_pnl = 0.0
    present = 0
    for trade in baseline:
        forecast = by_time.get(trade.entry_at)
        if forecast is None:
            continue
        present += 1
        stance = forecast_stance(forecast, direction_threshold)
        favorable_stance = ForecastStance.BULLISH if trade.side is TradeSide.LONG else ForecastStance.BEARISH
        conflicting_stance = ForecastStance.BEARISH if trade.side is TradeSide.LONG else ForecastStance.BULLISH
        if stance is favorable_stance:
            favorable += 1
        elif stance is ForecastStance.NEUTRAL:
            neutral_entries += 1
        elif stance is conflicting_stance:
            conflicts += 1
            pnl = float(net_pnl_by_entry[trade.entry_at])
            conflict_pnl += pnl
            if pnl > 0:
                winners += 1
            elif pnl < 0:
                losers += 1
            else:
                flat += 1
        else:  # pragma: no cover - enum exhaustiveness guard
            raise AssertionError("unhandled_forecast_stance")

    return ForecastVetoDiagnostic(
        provider=provider,
        horizon_steps=horizon_steps,
        direction_threshold=direction_threshold,
        issued_forecasts=len(rows),
        bullish_forecasts=bullish,
        bearish_forecasts=bearish,
        neutral_forecasts=neutral,
        baseline_entries=len(baseline),
        entries_with_forecast=present,
        missing_entry_forecasts=len(baseline) - present,
        favorable_entries=favorable,
        neutral_entries=neutral_entries,
        conflicting_entries=conflicts,
        conflicting_winners=winners,
        conflicting_losers=losers,
        conflicting_flat=flat,
        conflicting_net_pnl=conflict_pnl,
    )


@dataclass(frozen=True, slots=True)
class MatchedExposureTrade:
    trade_id: str
    volume: float
    gross_pnl: float
    original_cost_per_lot: float
    stressed_cost_per_lot: float

    def __post_init__(self) -> None:
        values = (self.volume, self.gross_pnl, self.original_cost_per_lot, self.stressed_cost_per_lot)
        if not self.trade_id.strip() or any(isinstance(value, bool) or not math.isfinite(value) for value in values):
            raise ValueError("matched_exposure_trade_requires_id_and_finite_values")
        if self.volume <= 0 or self.original_cost_per_lot < 0 or self.stressed_cost_per_lot < self.original_cost_per_lot:
            raise ValueError("matched_exposure_trade_has_invalid_volume_or_cost_stress")


@dataclass(frozen=True, slots=True)
class MatchedExposureAttribution:
    trade_count: int
    original_net_pnl: float
    stressed_net_pnl_same_exposure: float
    additional_cost_effect: float


@dataclass(frozen=True, slots=True)
class StressedResultDecomposition:
    original_net_pnl: float
    additional_cost_effect_same_exposure: float
    exposure_or_sequence_effect: float
    actual_stressed_net_pnl: float

    @property
    def total_change(self) -> float:
        return self.actual_stressed_net_pnl - self.original_net_pnl


def matched_exposure_cost_attribution(rows: Iterable[MatchedExposureTrade]) -> MatchedExposureAttribution:
    """Reprice identical trades/volumes under higher costs without re-sizing them."""
    trades = tuple(rows)
    original = sum(row.gross_pnl - row.original_cost_per_lot * row.volume for row in trades)
    stressed = sum(row.gross_pnl - row.stressed_cost_per_lot * row.volume for row in trades)
    return MatchedExposureAttribution(len(trades), original, stressed, stressed - original)


def decompose_stressed_result(
    attribution: MatchedExposureAttribution,
    actual_stressed_net_pnl: float,
) -> StressedResultDecomposition:
    """Residualize actual stressed performance after holding original exposure fixed.

    The residual is deliberately named exposure_or_sequence_effect because a changed sizing
    rule can also alter later occupancy/cooldown and therefore the actual trade sequence.
    """
    if isinstance(actual_stressed_net_pnl, bool) or not math.isfinite(actual_stressed_net_pnl):
        raise ValueError("actual_stressed_net_pnl_must_be_finite")
    residual = actual_stressed_net_pnl - attribution.stressed_net_pnl_same_exposure
    return StressedResultDecomposition(
        attribution.original_net_pnl,
        attribution.additional_cost_effect,
        residual,
        actual_stressed_net_pnl,
    )
