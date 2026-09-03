"""Small, auditable fitted forecast; no downloads, credentials or trading authority.

Direct 16-observation return regression on a completed four-observation return.
Coefficients and feature scaling are fitted once using labels strictly before a
fold's start, then frozen. Horizons count observations, not wall-clock minutes.
"""
from dataclasses import asdict, dataclass
from datetime import datetime
from math import fsum, isfinite, sqrt
from typing import Sequence

from .features import FeatureBar
from .forecasting import Forecast, RealizedTarget, score_forecasts
from .research_comparison import _fingerprint


FORECAST_PROTOCOL = "frozen-direct-ridge-return-v1"
HORIZON = 16
LOOKBACK = 4
MIN_TRAINING_PAIRS = 64
RIDGE = 1.0  # penalty against mean squared error in standardized feature space
RETURN_CAP = 0.20  # fixed numerical guard, not an optimized parameter


def forecast_contract() -> dict:
    return {"protocol": FORECAST_PROTOCOL, "horizon_observations": HORIZON,
            "feature": "completed_close_return_over_4_observations_bps",
            "lookback_observations": LOOKBACK, "minimum_training_pairs": MIN_TRAINING_PAIRS,
            "ridge_penalty": RIDGE, "return_cap": RETURN_CAP,
            "fit": "one_fit_per_fold_labels_strictly_before_test_start",
            "models": ["ridge", "training-mean", "no-change"],
            "entry_integration": "ridge_conflict_veto_only_missing_forecast_blocks_entry",
            "prediction_intervals": False, "promotion_eligible": False}


def _rows(bars: Sequence[FeatureBar]) -> tuple[FeatureBar, ...]:
    rows = tuple(bars)
    if not rows or len(rows) > 3000 or any(a.at >= b.at for a, b in zip(rows, rows[1:])):
        raise ValueError("forecast_requires_1_to_3000_unique_chronological_bars")
    if any(not isfinite(b.close) or b.close <= 0 for b in rows):
        raise ValueError("forecast_close_must_be_finite_positive")
    return rows


def _feature(rows: Sequence[FeatureBar], index: int) -> float:
    return 10000.0 * (rows[index].close / rows[index-LOOKBACK].close - 1.0)


@dataclass(frozen=True, slots=True)
class FittedReturnModel:
    fold_start: datetime
    trained_through: datetime
    pairs: int
    feature_mean: float
    feature_scale: float
    target_mean: float
    slope: float
    training_sha256: str
    contract_sha256: str

    @property
    def fingerprint(self) -> str:
        return _fingerprint(asdict(self))

    def predict(self, x: float) -> float:
        value = (self.target_mean + self.slope * ((x-self.feature_mean)/self.feature_scale)) / 10000.0
        if not isfinite(value):
            raise ValueError("nonfinite_fitted_forecast")
        return max(-RETURN_CAP, min(RETURN_CAP, value))


def fit_return_model(bars: Sequence[FeatureBar], *, before: datetime) -> FittedReturnModel:
    if before.tzinfo is None or before.utcoffset() is None:
        raise ValueError("forecast_fit_boundary_must_be_aware")
    # Filter BEFORE constructing features/targets/scalers. No held-out data is fitted.
    rows = _rows(tuple(b for b in bars if b.at < before))
    pairs = [(_feature(rows, i), 10000.0*(rows[i+HORIZON].close/rows[i].close-1.0))
             for i in range(LOOKBACK, len(rows)-HORIZON)]
    if len(pairs) < MIN_TRAINING_PAIRS:
        raise ValueError("insufficient_past_training_pairs_no_fallback")
    n = len(pairs)
    mx, my = (fsum(p[k] for p in pairs)/n for k in (0, 1))
    scale = sqrt(fsum((x-mx)**2 for x, _ in pairs)/n) or 1.0
    z = [((x-mx)/scale, y-my) for x, y in pairs]
    slope = (fsum(x*y for x, y in z)/n) / (fsum(x*x for x, _ in z)/n + RIDGE)
    if not all(isfinite(v) for v in (mx, my, scale, slope)):
        raise ValueError("nonfinite_model_fit")
    # Bind only observed close/timestamp inputs, not unrelated execution proxies.
    identity = [{"at": b.at, "close": b.close} for b in rows]
    return FittedReturnModel(before, rows[-1].at, n, mx, scale, my, slope,
                             _fingerprint(identity), _fingerprint(forecast_contract()))


def forecast_fold(bars: Sequence[FeatureBar], *, start: datetime, end: datetime) -> dict:
    rows = _rows(bars)
    if not start < end:
        raise ValueError("invalid_forecast_fold")
    model = fit_return_model(rows, before=start)
    forecasts = []
    evidence = []
    realized = []
    for i, bar in enumerate(rows):
        if not start <= bar.at < end:
            continue
        if i < LOOKBACK:
            raise ValueError("missing_past_forecast_context")
        x = _feature(rows, i)
        predictions = {"ridge": model.predict(x),
                       "training-mean": max(-RETURN_CAP, min(RETURN_CAP, model.target_mean/10000.0)),
                       "no-change": 0.0}
        issued = [Forecast(name, bar.at, HORIZON, bar.close, bar.close*(1.0+value))
                  for name, value in predictions.items()]
        forecasts.extend(issued)
        evidence.append({"issued_at": bar.at, "model_fingerprint": model.fingerprint,
                         "feature_bps": x, "origin": bar.close,
                         "forecasts": [asdict(f) for f in issued]})
        # Separate scoring path: never pass these labels to the frozen predictor.
        if i+HORIZON < len(rows) and rows[i+HORIZON].at < end:
            realized.append(RealizedTarget(bar.at, HORIZON, bar.close, rows[i+HORIZON].close))
    if not evidence or not realized:
        raise ValueError("insufficient_forecast_test_observations")
    scored = {s.provider: asdict(s) for s in score_forecasts(forecasts, realized)}
    scores = {name: scored[name] for name in forecast_contract()["models"]}
    naive_mae = scores["no-change"]["mae"]
    for score in scores.values():
        score["mae_skill_vs_no_change"] = 1.0-score["mae"]/naive_mae if naive_mae > 0 else None
    return {"protocol": FORECAST_PROTOCOL, "model": asdict(model), "model_fingerprint": model.fingerprint,
            "forecasts": evidence, "realized_targets": [asdict(r) for r in realized], "scores": scores,
            "unscored_tail_observations": len(evidence)-len(realized), "promotion_eligible": False,
            "target": "completed_close_after_16_observations_not_trade_exit_or_profit",
            "scores_are_overlapping_not_independent_trials": True}


def ridge_forecast_map(evidence: dict) -> dict[datetime, tuple[Forecast, ...]]:
    """Typed, same-process evidence bridge; JSON is never executed as model code."""
    return {row["issued_at"]: tuple(Forecast(**f) for f in row["forecasts"] if f["provider"] == "ridge")
            for row in evidence["forecasts"]}
