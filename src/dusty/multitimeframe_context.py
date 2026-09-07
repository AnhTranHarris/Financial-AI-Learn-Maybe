from __future__ import annotations

"""M196.7 point-in-time binding for bounded multi-timeframe research profiles.

M196.6 decides *which* primary/context timeframes a strategy family may study.
This module binds already-completed feature vectors from those timeframes to one
primary decision clock without permitting future context, silent primary
backfill, or context-timeframe execution prices.
"""

from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Mapping, Sequence

from .chart_intelligence import AnalysisSnapshot
from .features import FeatureVector
from .strategy_reconstruction_campaign import TimeframeProfile


_INTERNAL_FEATURE_PREFIX = "__"


@dataclass(frozen=True, slots=True)
class BoundTimeframeContext:
    profile_fingerprint: str
    decision_at: datetime
    source_times: tuple[tuple[str, datetime], ...]
    snapshot: AnalysisSnapshot

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    def __post_init__(self) -> None:
        if len(self.profile_fingerprint) != 64:
            raise ValueError("M196.7 profile fingerprint must be SHA-256")
        if self.decision_at.tzinfo is None or self.decision_at.utcoffset() is None:
            raise ValueError("M196.7 decision timestamp must be timezone-aware")
        if self.snapshot.at != self.decision_at:
            raise ValueError("M196.7 snapshot must share the primary decision clock")
        if len(dict(self.source_times)) != len(self.source_times):
            raise ValueError("M196.7 source timeframe identities must be unique")
        if any(at > self.decision_at for _, at in self.source_times):
            raise ValueError("M196.7 cannot bind future timeframe evidence")

    @property
    def fingerprint(self) -> str:
        payload = {
            "protocol": "dusty-m1967-pit-multitimeframe-context-v1",
            "profile": self.profile_fingerprint,
            "decision_at": self.decision_at.isoformat(),
            "source_times": tuple((tf, at.isoformat()) for tf, at in self.source_times),
            "values": self.snapshot.values,
            "known_at": tuple((key, at.isoformat()) for key, at in self.snapshot.known_at),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return sha256(raw.encode("utf-8")).hexdigest()


def _normalize_series(
    timeframe: str,
    rows: Sequence[FeatureVector],
) -> tuple[FeatureVector, ...]:
    timeframe = timeframe.strip().upper()
    series = tuple(rows)
    previous: datetime | None = None
    for row in series:
        if row.at.tzinfo is None or row.at.utcoffset() is None:
            raise ValueError(f"M196.7 {timeframe} feature timestamp must be timezone-aware")
        if previous is not None and row.at <= previous:
            raise ValueError(f"M196.7 {timeframe} feature series must be strictly chronological and unique")
        previous = row.at
        for key, value in row.values:
            if key.startswith(_INTERNAL_FEATURE_PREFIX):
                continue
            if isinstance(value, bool):
                continue
            if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                raise ValueError(f"M196.7 analytical feature must be finite numeric/bool: {timeframe}.{key}")
    return series


def _latest_as_of(series: tuple[FeatureVector, ...], at: datetime) -> FeatureVector | None:
    if not series:
        return None
    positions = tuple(row.at for row in series)
    index = bisect_right(positions, at) - 1
    return None if index < 0 else series[index]


def bind_multitimeframe_context(
    profile: TimeframeProfile,
    series_by_timeframe: Mapping[str, Sequence[FeatureVector]],
    *,
    decision_at: datetime,
) -> BoundTimeframeContext:
    """Bind one primary decision timestamp to the latest completed context rows.

    The primary timeframe must contain a feature vector *exactly* at
    ``decision_at``. Context timeframes use their latest vector whose availability
    timestamp is <= ``decision_at``. Because the input vectors are produced from
    completed ``FeatureBar`` observations, this preserves MT5 bar-close semantics
    while naturally carrying a closed D1/H4/H1 observation until its next close.
    """

    if decision_at.tzinfo is None or decision_at.utcoffset() is None:
        raise ValueError("M196.7 decision timestamp must be timezone-aware")

    normalized_input: dict[str, Sequence[FeatureVector]] = {}
    for raw_timeframe, series in series_by_timeframe.items():
        timeframe = raw_timeframe.strip().upper()
        if not timeframe or timeframe in normalized_input:
            raise ValueError("M196.7 timeframe input identities must be unique after normalization")
        normalized_input[timeframe] = series

    required = (profile.primary, *profile.context)
    missing = tuple(timeframe for timeframe in required if timeframe not in normalized_input)
    if missing:
        raise ValueError(f"M196.7 missing required timeframe series: {','.join(missing)}")

    prepared = {
        timeframe: _normalize_series(timeframe, normalized_input[timeframe])
        for timeframe in required
    }

    primary_rows = prepared[profile.primary]
    primary = _latest_as_of(primary_rows, decision_at)
    if primary is None or primary.at != decision_at:
        raise ValueError("M196.7 primary timeframe cannot be silently backfilled")

    selected: list[tuple[str, FeatureVector]] = [(profile.primary, primary)]
    for timeframe in profile.context:
        context = _latest_as_of(prepared[timeframe], decision_at)
        if context is None:
            raise ValueError(f"M196.7 no completed {timeframe} context exists as of decision time")
        selected.append((timeframe, context))

    values: dict[str, float | bool] = {}
    known_at: dict[str, datetime] = {}
    source_times: list[tuple[str, datetime]] = []
    for timeframe, vector in selected:
        source_times.append((timeframe, vector.at))
        for key, value in vector.values:
            # Internal fields include the primary execution reference. Context is
            # analytical evidence only, so no timeframe may inject an alternate
            # fill/reference price through this binder.
            if key.startswith(_INTERNAL_FEATURE_PREFIX):
                continue
            namespaced = f"{timeframe.lower()}.{key}"
            if namespaced in values:
                raise ValueError(f"M196.7 duplicate namespaced feature: {namespaced}")
            if isinstance(value, bool):
                values[namespaced] = value
            else:
                values[namespaced] = float(value)
            known_at[namespaced] = vector.at

    snapshot = AnalysisSnapshot.of(decision_at, values, known_at=known_at)
    return BoundTimeframeContext(
        profile_fingerprint=profile.fingerprint,
        decision_at=decision_at,
        source_times=tuple(source_times),
        snapshot=snapshot,
    )


def bind_multitimeframe_history(
    profile: TimeframeProfile,
    series_by_timeframe: Mapping[str, Sequence[FeatureVector]],
) -> tuple[BoundTimeframeContext, ...]:
    """Create a PIT-safe bound history on the primary completed-bar clock.

    Early primary rows without all required context are skipped. Invalid series
    structure is never skipped; it fails closed before any binding occurs.
    """

    normalized: dict[str, tuple[FeatureVector, ...]] = {}
    for raw_timeframe, series in series_by_timeframe.items():
        timeframe = raw_timeframe.strip().upper()
        if not timeframe or timeframe in normalized:
            raise ValueError("M196.7 timeframe input identities must be unique after normalization")
        normalized[timeframe] = _normalize_series(timeframe, series)

    required = (profile.primary, *profile.context)
    missing = tuple(timeframe for timeframe in required if timeframe not in normalized)
    if missing:
        raise ValueError(f"M196.7 missing required timeframe series: {','.join(missing)}")

    result: list[BoundTimeframeContext] = []
    for primary in normalized[profile.primary]:
        if any(_latest_as_of(normalized[timeframe], primary.at) is None for timeframe in profile.context):
            continue
        result.append(bind_multitimeframe_context(profile, normalized, decision_at=primary.at))
    return tuple(result)
