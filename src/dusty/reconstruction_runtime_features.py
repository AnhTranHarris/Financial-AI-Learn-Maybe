from __future__ import annotations

"""Point-in-time normalized features exposed only to strategy reconstruction.

The canonical M156 feature engine intentionally retains price-valued indicators
for parity and diagnostics.  Model-authored scalar predicates receive a narrower
layer made only from dimensionless transformations of those completed-bar
features.  This prevents lookback periods from being confused with indicator
values and makes thresholds comparable across EURUSD, XAUUSD and NASUSD.
"""

from dataclasses import replace
import math
from typing import Iterable

from .research import Scalar
from .runtime import RuntimeBar


CLOSE_SMA_20_DISTANCE_FRAC = "close_sma_20_distance_frac"
CLOSE_EMA_20_DISTANCE_FRAC = "close_ema_20_distance_frac"
ATR_14_FRACTION = "atr_14_fraction"
MODEL_SAFE_NORMALIZED_FEATURES = (
    CLOSE_SMA_20_DISTANCE_FRAC,
    CLOSE_EMA_20_DISTANCE_FRAC,
    ATR_14_FRACTION,
)


def _numeric(features: dict[str, Scalar], name: str) -> float | None:
    value = features.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def normalized_reconstruction_values(features: dict[str, Scalar]) -> dict[str, float]:
    """Derive dimensionless reconstruction features from one PIT feature map.

    Missing warm-up inputs simply leave the corresponding normalized feature
    unavailable.  Existing values are never overwritten and no future row is
    consulted.
    """

    close = _numeric(features, "close")
    if close is None or close <= 0.0:
        return {}

    result: dict[str, float] = {}
    sma = _numeric(features, "sma_20")
    if sma is not None and sma > 0.0:
        result[CLOSE_SMA_20_DISTANCE_FRAC] = close / sma - 1.0

    ema = _numeric(features, "ema_20")
    if ema is not None and ema > 0.0:
        result[CLOSE_EMA_20_DISTANCE_FRAC] = close / ema - 1.0

    atr = _numeric(features, "atr_14")
    if atr is not None and atr >= 0.0:
        result[ATR_14_FRACTION] = atr / close

    if any(not math.isfinite(value) for value in result.values()):
        raise ValueError("normalized reconstruction feature became non-finite")
    return result


def augment_runtime_bar(row: RuntimeBar) -> RuntimeBar:
    features = row.feature_map()
    derived = normalized_reconstruction_values(features)
    collision = set(features).intersection(derived)
    if collision:
        raise ValueError(f"normalized reconstruction feature collision: {sorted(collision)!r}")
    if not derived:
        return row
    features.update(derived)
    return replace(row, features=tuple(sorted(features.items())))


def augment_runtime_bars(rows: Iterable[RuntimeBar]) -> tuple[RuntimeBar, ...]:
    return tuple(augment_runtime_bar(row) for row in rows)


broker_write_authority = False
live_write_authority = False
promotion_authority = False
risk_override_authority = False
retry_authority = False
