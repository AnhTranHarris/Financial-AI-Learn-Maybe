from __future__ import annotations

"""M178 context-bound forecast disagreement atlas."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import mean
from typing import Iterable

from .forecast_research import DisagreementState, ForecastDisagreement
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


class DisagreementAtlasStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    MEASURED = "measured"


@dataclass(frozen=True, slots=True)
class DisagreementOutcomeObservation:
    case_fingerprint: str
    bucket: ForecastContextBucket
    disagreement: ForecastDisagreement
    realized_direction: str
    realized_return: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "case_fingerprint", _sha(self.case_fingerprint, "disagreement case"))
        direction = str(self.realized_direction).strip().lower()
        if direction not in {"up", "down", "flat"}:
            raise ValueError("realized_direction must be up/down/flat")
        object.__setattr__(self, "realized_direction", direction)
        rendered = float(self.realized_return)
        if not math.isfinite(rendered):
            raise ValueError("realized_return must be finite")
        object.__setattr__(self, "realized_return", rendered)

    @property
    def fingerprint(self) -> str:
        return _digest((self.case_fingerprint, self.bucket.fingerprint, self.disagreement.state.value, self.disagreement.provider_directions, self.disagreement.evidence_fingerprints, self.realized_direction, self.realized_return))


@dataclass(frozen=True, slots=True)
class DisagreementAtlasCell:
    bucket: ForecastContextBucket
    state: DisagreementState
    status: DisagreementAtlasStatus
    case_count: int
    consensus_direction: str | None
    consensus_accuracy: float | None
    mean_realized_return: float | None
    observation_fingerprints: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m178-disagreement-atlas-v1", self.bucket.fingerprint, self.state.value, self.status.value, self.case_count, self.consensus_direction, self.consensus_accuracy, self.mean_realized_return, self.observation_fingerprints))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def decision_authority(self) -> bool:
        return False


def _consensus(state: DisagreementState) -> str | None:
    if state in {DisagreementState.UNANIMOUS_UP, DisagreementState.TWO_UP_ONE_DOWN}:
        return "up"
    if state in {DisagreementState.UNANIMOUS_DOWN, DisagreementState.TWO_DOWN_ONE_UP}:
        return "down"
    if state is DisagreementState.UNANIMOUS_FLAT:
        return "flat"
    return None


def build_disagreement_cell(
    bucket: ForecastContextBucket,
    state: DisagreementState,
    observations: Iterable[DisagreementOutcomeObservation],
    *,
    minimum_cases: int = 20,
) -> DisagreementAtlasCell:
    if not 1 <= int(minimum_cases) <= 1_000_000:
        raise ValueError("minimum_cases out of range")
    rows = tuple(row for row in observations if row.bucket == bucket and row.disagreement.state is state)
    case_ids = tuple(row.case_fingerprint for row in rows)
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("disagreement atlas cannot contain duplicate cases")
    fingerprints = tuple(sorted(row.fingerprint for row in rows))
    consensus = _consensus(state)
    if len(rows) < minimum_cases:
        return DisagreementAtlasCell(bucket, state, DisagreementAtlasStatus.INSUFFICIENT, len(rows), consensus, None, None, fingerprints)
    accuracy = None if consensus is None else mean(row.realized_direction == consensus for row in rows)
    return DisagreementAtlasCell(bucket, state, DisagreementAtlasStatus.MEASURED, len(rows), consensus, accuracy, mean(row.realized_return for row in rows), fingerprints)
