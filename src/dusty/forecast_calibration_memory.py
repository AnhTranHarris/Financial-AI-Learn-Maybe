from __future__ import annotations

"""M177 point-in-time forecast calibration memory.

Calibration memory describes observed provider behavior.  It does not modify a
forecast, vote on a trade, or grant operational authority.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import mean
from typing import Iterable

from .forecast_campaign import EXPECTED_PROVIDERS, PITForecastAttempt, PITForecastCase, PITForecastOutcome
from .forecast_specialization import ForecastContextBucket


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


class CalibrationMemoryStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    MEASURED = "measured"


@dataclass(frozen=True, slots=True)
class ForecastCalibrationObservation:
    case_fingerprint: str
    provider_id: str
    bucket: ForecastContextBucket
    signed_error_fraction: float
    absolute_error: float
    baseline_absolute_error: float
    direction_hit: bool
    interval_80_hit: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_fingerprint", _sha(self.case_fingerprint, "calibration case"))
        provider = str(self.provider_id).strip().lower()
        if provider not in EXPECTED_PROVIDERS:
            raise ValueError("unexpected calibration provider")
        object.__setattr__(self, "provider_id", provider)
        object.__setattr__(self, "signed_error_fraction", _finite(self.signed_error_fraction, "signed_error_fraction"))
        for name in ("absolute_error", "baseline_absolute_error"):
            value = _finite(getattr(self, name), name)
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")
            object.__setattr__(self, name, value)

    @property
    def fingerprint(self) -> str:
        return _digest((self.case_fingerprint, self.provider_id, self.bucket.fingerprint, self.signed_error_fraction, self.absolute_error, self.baseline_absolute_error, self.direction_hit, self.interval_80_hit))


@dataclass(frozen=True, slots=True)
class CalibrationMemoryPolicy:
    minimum_cases: int = 30
    nominal_interval_coverage: float = 0.80

    def __post_init__(self) -> None:
        if not 1 <= int(self.minimum_cases) <= 1_000_000:
            raise ValueError("minimum_cases out of range")
        nominal = _finite(self.nominal_interval_coverage, "nominal_interval_coverage")
        if not 0 < nominal < 1:
            raise ValueError("nominal_interval_coverage must be in (0,1)")


@dataclass(frozen=True, slots=True)
class ForecastCalibrationMemory:
    provider_id: str
    bucket: ForecastContextBucket
    status: CalibrationMemoryStatus
    case_count: int
    mean_signed_error_fraction: float | None
    mae: float | None
    baseline_mae: float | None
    skill: float | None
    direction_accuracy: float | None
    observed_interval_80_coverage: float | None
    interval_coverage_error: float | None
    observation_fingerprints: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m177-calibration-memory-v1", self.provider_id, self.bucket.fingerprint, self.status.value, self.case_count, self.mean_signed_error_fraction, self.mae, self.baseline_mae, self.skill, self.direction_accuracy, self.observed_interval_80_coverage, self.interval_coverage_error, self.observation_fingerprints))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def forecast_correction_authority(self) -> bool:
        return False

    @property
    def voting_authority(self) -> bool:
        return False


def make_calibration_observation(
    case: PITForecastCase,
    attempt: PITForecastAttempt,
    outcome: PITForecastOutcome,
    *,
    bucket: ForecastContextBucket,
) -> ForecastCalibrationObservation:
    if attempt.case_fingerprint != case.case_fingerprint or outcome.case_fingerprint != case.case_fingerprint:
        raise ValueError("calibration case identity mismatch")
    if outcome.observed_at < case.target_at:
        raise ValueError("calibration outcome not yet knowable")
    evidence = attempt.evidence
    if evidence is None:
        raise ValueError("calibration observation requires available forecast evidence")
    if evidence.provider_id != attempt.provider_id or evidence.symbol.upper() != bucket.symbol or evidence.timeframe.upper() != bucket.timeframe or evidence.horizon_steps != bucket.horizon_steps:
        raise ValueError("calibration bucket/evidence identity drift")
    if evidence.as_of != case.as_of or evidence.context_sha256 != case.context_sha256:
        raise ValueError("calibration point-in-time evidence drift")
    actual = outcome.target_value
    predicted = evidence.p50
    predicted_direction = 0 if predicted == case.origin_value else (1 if predicted > case.origin_value else -1)
    actual_direction = 0 if actual == case.origin_value else (1 if actual > case.origin_value else -1)
    return ForecastCalibrationObservation(
        case.case_fingerprint,
        attempt.provider_id,
        bucket,
        (predicted - actual) / case.origin_value,
        abs(predicted - actual),
        abs(case.origin_value - actual),
        predicted_direction == actual_direction,
        evidence.p10 <= actual <= evidence.p90,
    )


def build_calibration_memory(
    provider_id: str,
    bucket: ForecastContextBucket,
    observations: Iterable[ForecastCalibrationObservation],
    *,
    policy: CalibrationMemoryPolicy = CalibrationMemoryPolicy(),
) -> ForecastCalibrationMemory:
    provider = str(provider_id).strip().lower()
    if provider not in EXPECTED_PROVIDERS:
        raise ValueError("unexpected calibration provider")
    rows = tuple(row for row in observations if row.provider_id == provider and row.bucket == bucket)
    case_ids = tuple(row.case_fingerprint for row in rows)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("calibration memory cannot contain duplicate cases")
    fingerprints = tuple(sorted(row.fingerprint for row in rows))
    if len(rows) < policy.minimum_cases:
        return ForecastCalibrationMemory(provider, bucket, CalibrationMemoryStatus.INSUFFICIENT, len(rows), None, None, None, None, None, None, None, fingerprints)
    mae = mean(row.absolute_error for row in rows)
    baseline = mean(row.baseline_absolute_error for row in rows)
    coverage = mean(row.interval_80_hit for row in rows)
    skill = None if baseline == 0 else 1.0 - mae / baseline
    return ForecastCalibrationMemory(
        provider,
        bucket,
        CalibrationMemoryStatus.MEASURED,
        len(rows),
        mean(row.signed_error_fraction for row in rows),
        mae,
        baseline,
        skill,
        mean(row.direction_hit for row in rows),
        coverage,
        abs(coverage - policy.nominal_interval_coverage),
        fingerprints,
    )
