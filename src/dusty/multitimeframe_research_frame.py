from __future__ import annotations

"""M196.9 adapter from PIT multi-timeframe evidence to semantic research replay.

The M196.7 binder intentionally namespaces every analytical feature and removes
internal execution references. Existing StrategySpecV3 semantic replay still
expects legacy *primary-timeframe* aliases such as ``atr`` for typed protection
rules. This adapter restores only those primary aliases while keeping all
context features namespaced and obtaining execution price exclusively from the
primary completed-bar FeatureVector.
"""

import math
from typing import Iterable

from .analysis_runtime import AnalysisFrame
from .chart_intelligence import AnalysisSnapshot
from .features import FeatureVector
from .multitimeframe_context import BoundTimeframeContext
from .strategy_reconstruction_campaign import TimeframeProfile

_EXECUTION_PRICE_KEY = "__execution_price__"
_PRIMARY_INTERNAL_COMPAT = frozenset({"__proposed_stop__"})


def _feature_map(vector: FeatureVector) -> dict[str, float | bool]:
    values = dict(vector.values)
    if len(values) != len(vector.values):
        raise ValueError("M196.9 primary feature keys must be unique")
    return values


def analysis_frame_from_multitimeframe_context(
    profile: TimeframeProfile,
    bound: BoundTimeframeContext,
    primary: FeatureVector,
) -> AnalysisFrame:
    """Create one backward-compatible semantic replay frame without flattening context.

    Public primary values are available twice by design: namespaced (for new
    multi-timeframe graphs) and bare (for the existing single-timeframe runtime
    contract). Higher-timeframe values remain namespaced only. The execution
    reference is never read from the bound snapshot; it must come from the
    original primary vector produced by completed-bar feature generation.
    """

    if bound.profile_fingerprint != profile.fingerprint:
        raise ValueError("M196.9 bound context/profile identity mismatch")
    if primary.at != bound.decision_at:
        raise ValueError("M196.9 primary feature must share the bound decision clock")

    primary_values = _feature_map(primary)
    raw_execution = primary_values.get(_EXECUTION_PRICE_KEY)
    if (
        isinstance(raw_execution, bool)
        or not isinstance(raw_execution, (int, float))
        or not math.isfinite(float(raw_execution))
        or float(raw_execution) <= 0
    ):
        raise ValueError("M196.9 primary feature requires positive execution reference")

    bound_values = dict(bound.snapshot.values)
    bound_known = dict(bound.snapshot.known_at)
    prefix = f"{profile.primary.lower()}."

    values: dict[str, float | bool] = dict(bound_values)
    known_at = dict(bound_known)

    for key, raw_value in primary.values:
        if key == _EXECUTION_PRICE_KEY:
            continue
        if key.startswith("__"):
            if key not in _PRIMARY_INTERNAL_COMPAT:
                continue
            if key in values:
                raise ValueError(f"M196.9 primary compatibility collision: {key}")
            values[key] = raw_value
            known_at[key] = primary.at
            continue

        namespaced = f"{prefix}{key}"
        if namespaced not in bound_values:
            raise ValueError(f"M196.9 bound context missing primary feature: {namespaced}")
        if bound_values[namespaced] != raw_value:
            raise ValueError(f"M196.9 primary feature disagrees with bound context: {namespaced}")
        if key in values:
            raise ValueError(f"M196.9 primary compatibility collision: {key}")
        values[key] = raw_value
        known_at[key] = primary.at

    snapshot = AnalysisSnapshot.of(bound.decision_at, values, known_at=known_at)
    return AnalysisFrame(snapshot, float(raw_execution))


def analysis_frames_from_multitimeframe_history(
    profile: TimeframeProfile,
    bounds: Iterable[BoundTimeframeContext],
    primary_features: Iterable[FeatureVector],
) -> tuple[AnalysisFrame, ...]:
    """Adapt a PIT-bound history using exact primary decision timestamps."""

    primary_by_at: dict[object, FeatureVector] = {}
    for vector in primary_features:
        if vector.at in primary_by_at:
            raise ValueError("M196.9 primary feature history timestamps must be unique")
        primary_by_at[vector.at] = vector

    result: list[AnalysisFrame] = []
    previous = None
    for bound in bounds:
        if previous is not None and bound.decision_at <= previous:
            raise ValueError("M196.9 bound history must be strictly chronological")
        previous = bound.decision_at
        primary = primary_by_at.get(bound.decision_at)
        if primary is None:
            raise ValueError("M196.9 bound history lacks exact primary feature row")
        result.append(analysis_frame_from_multitimeframe_context(profile, bound, primary))
    return tuple(result)
