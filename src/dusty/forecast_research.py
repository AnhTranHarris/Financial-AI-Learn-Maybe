from __future__ import annotations

"""Point-in-time forecast research for independent contractors.

No ensemble, vote, weighting, veto, or trading authority is created here.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import fmean
from typing import Iterable, Sequence

from .features import FeatureBar
from .provider_forecast_adapter import ForecastEvidence


def _digest(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class PITForecastContext:
    symbol: str
    timeframe: str
    as_of: datetime
    rows: tuple[FeatureBar, ...]
    context_hash: str

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip():
            raise ValueError("PIT forecast context identity required")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("PIT as_of must be timezone-aware")
        if not self.rows:
            raise ValueError("PIT context requires bars")
        if any(row.at > self.as_of for row in self.rows):
            raise ValueError("future bar leaked into PIT context")
        if any(a.at >= b.at for a, b in zip(self.rows, self.rows[1:])):
            raise ValueError("PIT context must be chronological")
        if len(self.context_hash) != 64:
            raise ValueError("PIT context requires SHA-256 identity")


def _bar_payload(row: FeatureBar) -> dict[str, object]:
    return {
        "at": row.at.isoformat(),
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "spread_points": row.spread_points,
        "tick_volume": row.tick_volume,
        "source_open_at": None if row.source_open_at is None else row.source_open_at.isoformat(),
        "execution_price": row.execution_price,
        "decision_spread_proxy_points": row.decision_spread_proxy_points,
    }


def build_pit_context(
    bars: Iterable[FeatureBar],
    *,
    symbol: str,
    timeframe: str,
    as_of: datetime,
    max_observations: int = 2048,
) -> PITForecastContext:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("PIT as_of must be timezone-aware")
    if max_observations < 1:
        raise ValueError("max observations must be positive")
    rows = tuple(row for row in bars if row.at <= as_of)
    rows = rows[-max_observations:]
    payload = {
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip().upper(),
        "as_of": as_of.isoformat(),
        "rows": tuple(_bar_payload(row) for row in rows),
    }
    return PITForecastContext(
        symbol.strip().upper(),
        timeframe.strip().upper(),
        as_of,
        rows,
        _digest(payload),
    )


def future_mutation_invariant(
    original: Sequence[FeatureBar],
    mutated: Sequence[FeatureBar],
    *,
    symbol: str,
    timeframe: str,
    as_of: datetime,
) -> bool:
    """Future-only changes must be incapable of changing the context at T."""

    left = build_pit_context(original, symbol=symbol, timeframe=timeframe, as_of=as_of)
    right = build_pit_context(mutated, symbol=symbol, timeframe=timeframe, as_of=as_of)
    return left.context_hash == right.context_hash


@dataclass(frozen=True, slots=True)
class ProviderOutcomeCase:
    evidence: ForecastEvidence
    realized_value: float
    realized_at: datetime
    regime: str = "unclassified"
    session: str = "unclassified"

    def __post_init__(self) -> None:
        if not math.isfinite(self.realized_value) or self.realized_value <= 0:
            raise ValueError("forecast realized price must be finite and positive")
        if self.realized_at.tzinfo is None or self.realized_at.utcoffset() is None:
            raise ValueError("forecast realization time must be timezone-aware")
        if self.realized_at <= self.evidence.as_of:
            raise ValueError("forecast realization must follow issue time")
        if not self.regime.strip() or not self.session.strip():
            raise ValueError("forecast case regime/session required")


@dataclass(frozen=True, slots=True)
class ProviderReliability:
    provider_id: str
    model_id: str
    model_revision: str
    symbol: str
    timeframe: str
    horizon_steps: int
    regime: str
    session: str
    count: int
    mae: float
    directional_accuracy: float
    interval_coverage: float
    mean_interval_width_fraction: float
    bias_fraction: float

    def __post_init__(self) -> None:
        if self.count < 1:
            raise ValueError("provider reliability requires observations")
        numeric = (
            self.mae,
            self.directional_accuracy,
            self.interval_coverage,
            self.mean_interval_width_fraction,
            self.bias_fraction,
        )
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("provider reliability must be finite")
        if not 0 <= self.directional_accuracy <= 1 or not 0 <= self.interval_coverage <= 1:
            raise ValueError("provider probabilities must lie in [0,1]")


def score_provider_cases(cases: Iterable[ProviderOutcomeCase]) -> tuple[ProviderReliability, ...]:
    """Score each provider/market/regime/session identity independently."""

    groups: dict[tuple[str, str, str, str, str, int, str, str], list[ProviderOutcomeCase]] = {}
    for case in cases:
        ev = case.evidence
        key = (
            ev.provider_id,
            ev.model_id,
            ev.model_revision,
            ev.symbol.upper(),
            ev.timeframe.upper(),
            ev.horizon_steps,
            case.regime,
            case.session,
        )
        groups.setdefault(key, []).append(case)

    results = []
    for key, rows in sorted(groups.items()):
        provider, model, revision, symbol, timeframe, horizon, regime, session = key
        mae = fmean(abs(row.evidence.p50 - row.realized_value) for row in rows)
        direction = fmean(
            float(
                (row.evidence.p50 > row.evidence.origin_value)
                == (row.realized_value > row.evidence.origin_value)
            )
            for row in rows
        )
        coverage = fmean(
            float(row.evidence.p10 <= row.realized_value <= row.evidence.p90) for row in rows
        )
        width = fmean(
            (row.evidence.p90 - row.evidence.p10) / row.evidence.origin_value for row in rows
        )
        bias = fmean(
            (row.evidence.p50 - row.realized_value) / row.evidence.origin_value for row in rows
        )
        results.append(
            ProviderReliability(
                provider,
                model,
                revision,
                symbol,
                timeframe,
                horizon,
                regime,
                session,
                len(rows),
                mae,
                direction,
                coverage,
                width,
                bias,
            )
        )
    return tuple(results)


class DisagreementState(StrEnum):
    UNANIMOUS_UP = "unanimous_up"
    UNANIMOUS_DOWN = "unanimous_down"
    UNANIMOUS_FLAT = "unanimous_flat"
    TWO_UP_ONE_DOWN = "two_up_one_down"
    TWO_DOWN_ONE_UP = "two_down_one_up"
    MIXED_WITH_FLAT = "mixed_with_flat"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ForecastDisagreement:
    state: DisagreementState
    provider_directions: tuple[tuple[str, str], ...]
    evidence_fingerprints: tuple[str, ...]
    decision_authority: bool = False

    def __post_init__(self) -> None:
        if self.decision_authority:
            raise ValueError("forecast disagreement cannot receive trade authority")
        if len(self.provider_directions) != len(set(name for name, _ in self.provider_directions)):
            raise ValueError("forecast disagreement providers must be unique")


def _direction(evidence: ForecastEvidence, flat_fraction: float) -> str:
    change = evidence.p50 / evidence.origin_value - 1.0
    if change > flat_fraction:
        return "up"
    if change < -flat_fraction:
        return "down"
    return "flat"


def classify_disagreement(
    evidences: Iterable[ForecastEvidence],
    *,
    expected_provider_ids: Iterable[str] = ("chronos2", "kronos-small", "timesfm-2.5"),
    flat_fraction: float = 0.0,
) -> ForecastDisagreement:
    if flat_fraction < 0:
        raise ValueError("flat threshold cannot be negative")
    rows = tuple(evidences)
    expected = tuple(expected_provider_ids)
    if len(set(expected)) != len(expected):
        raise ValueError("expected providers must be unique")
    if not rows:
        return ForecastDisagreement(DisagreementState.UNAVAILABLE, (), ())
    identities = {
        (row.symbol.upper(), row.timeframe.upper(), row.as_of, row.horizon_steps, row.context_sha256)
        for row in rows
    }
    if len(identities) != 1:
        raise ValueError("forecast disagreement requires identical PIT context")
    if len({row.provider_id for row in rows}) != len(rows):
        raise ValueError("duplicate provider evidence")
    directions = tuple(sorted((row.provider_id, _direction(row, flat_fraction)) for row in rows))
    fingerprints = tuple(sorted(row.fingerprint for row in rows))
    if set(name for name, _ in directions) != set(expected):
        return ForecastDisagreement(DisagreementState.PARTIAL, directions, fingerprints)

    counts = {name: sum(direction == name for _, direction in directions) for name in ("up", "down", "flat")}
    if counts["up"] == 3:
        state = DisagreementState.UNANIMOUS_UP
    elif counts["down"] == 3:
        state = DisagreementState.UNANIMOUS_DOWN
    elif counts["flat"] == 3:
        state = DisagreementState.UNANIMOUS_FLAT
    elif counts["up"] == 2 and counts["down"] == 1:
        state = DisagreementState.TWO_UP_ONE_DOWN
    elif counts["down"] == 2 and counts["up"] == 1:
        state = DisagreementState.TWO_DOWN_ONE_UP
    else:
        state = DisagreementState.MIXED_WITH_FLAT
    return ForecastDisagreement(state, directions, fingerprints)
