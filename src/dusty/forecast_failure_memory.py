from __future__ import annotations

"""M181 forecast failure memory with recurrence thresholds."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Iterable

from .forecast_campaign import EXPECTED_PROVIDERS
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


class ForecastFailureKind(StrEnum):
    WRONG_DIRECTION = "wrong_direction"
    INTERVAL_MISS = "interval_miss"
    WORSE_THAN_NO_CHANGE = "worse_than_no_change"
    UNAVAILABLE = "unavailable"
    HARMFUL_ABLATION = "harmful_ablation"


class FailurePatternStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    RECURRENT = "recurrent"


@dataclass(frozen=True, slots=True)
class ForecastFailureEvent:
    case_fingerprint: str
    providers: tuple[str, ...]
    bucket: ForecastContextBucket
    kind: ForecastFailureKind
    source_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_fingerprint", _sha(self.case_fingerprint, "failure case"))
        object.__setattr__(self, "source_fingerprint", _sha(self.source_fingerprint, "failure source"))
        providers = tuple(sorted(str(value).strip().lower() for value in self.providers))
        if not providers or len(providers) != len(set(providers)) or any(value not in EXPECTED_PROVIDERS for value in providers):
            raise ValueError("failure event providers must be unique known providers")
        object.__setattr__(self, "providers", providers)

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m181-failure-event-v1", self.case_fingerprint, self.providers, self.bucket.fingerprint, self.kind.value, self.source_fingerprint))


@dataclass(frozen=True, slots=True)
class ForecastFailurePattern:
    providers: tuple[str, ...]
    bucket: ForecastContextBucket
    kind: ForecastFailureKind
    status: FailurePatternStatus
    occurrence_count: int
    case_fingerprints: tuple[str, ...]
    event_fingerprints: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m181-failure-pattern-v1", self.providers, self.bucket.fingerprint, self.kind.value, self.status.value, self.occurrence_count, self.case_fingerprints, self.event_fingerprints))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def provider_disable_authority(self) -> bool:
        return False

    @property
    def strategy_mutation_authority(self) -> bool:
        return False


def build_failure_pattern(
    providers: tuple[str, ...],
    bucket: ForecastContextBucket,
    kind: ForecastFailureKind,
    events: Iterable[ForecastFailureEvent],
    *,
    minimum_occurrences: int = 3,
) -> ForecastFailurePattern:
    if not 1 <= int(minimum_occurrences) <= 1_000_000:
        raise ValueError("minimum_occurrences out of range")
    canonical_providers = tuple(sorted(str(value).strip().lower() for value in providers))
    if not canonical_providers or len(canonical_providers) != len(set(canonical_providers)) or any(value not in EXPECTED_PROVIDERS for value in canonical_providers):
        raise ValueError("failure pattern providers must be unique known providers")
    rows = tuple(row for row in events if row.providers == canonical_providers and row.bucket == bucket and row.kind is kind)
    cases = tuple(sorted(row.case_fingerprint for row in rows))
    if len(cases) != len(set(cases)):
        raise ValueError("failure memory cannot count duplicate case identity twice")
    event_fps = tuple(sorted(row.fingerprint for row in rows))
    status = FailurePatternStatus.RECURRENT if len(rows) >= minimum_occurrences else FailurePatternStatus.INSUFFICIENT
    return ForecastFailurePattern(canonical_providers, bucket, kind, status, len(rows), cases, event_fps)
