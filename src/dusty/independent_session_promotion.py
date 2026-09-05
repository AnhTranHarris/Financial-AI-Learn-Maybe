from __future__ import annotations

"""M188 independent demo-session promotion evidence.

The gate extends the older six-desk forecast certification with M185 shadow,
M186 desk-account, and M187 broker-deviation provenance. Passing means only
that one frozen champion has accumulated enough independent demo evidence to
advance in research governance. It never authorizes live broker writes.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .broker_deviation import BrokerDeviationAssessment, BrokerDeviationStatus
from .demo_capital_allocator import DemoDeskCapitalState
from .forecast_demo import ForecastDeskEvidence


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 1:
        raise ValueError(f"{label} must be a positive integer")
    return int(value)


def _nonnegative_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return int(value)


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


def _unit(value: float, label: str) -> float:
    rendered = _finite(value, label)
    if not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{label} must be in [0,1]")
    return rendered


class IndependentPromotionStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    REJECTED = "rejected"
    RESEARCH_PROMOTION_ELIGIBLE = "research_promotion_eligible"


@dataclass(frozen=True, slots=True)
class IndependentPromotionPolicy:
    required_sessions: int = 6
    minimum_completed_forecasts: int = 30
    maximum_calibration_error: float = 0.10
    maximum_drawdown_fraction: float = 0.10
    minimum_execution_observations_per_session: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "required_sessions", _positive_int(self.required_sessions, "required_sessions"))
        object.__setattr__(
            self,
            "minimum_completed_forecasts",
            _positive_int(self.minimum_completed_forecasts, "minimum_completed_forecasts"),
        )
        object.__setattr__(
            self,
            "minimum_execution_observations_per_session",
            _positive_int(
                self.minimum_execution_observations_per_session,
                "minimum_execution_observations_per_session",
            ),
        )
        object.__setattr__(
            self,
            "maximum_calibration_error",
            _unit(self.maximum_calibration_error, "maximum_calibration_error"),
        )
        object.__setattr__(
            self,
            "maximum_drawdown_fraction",
            _unit(self.maximum_drawdown_fraction, "maximum_drawdown_fraction"),
        )

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m188-independent-promotion-policy-v1",
            self.required_sessions,
            self.minimum_completed_forecasts,
            self.maximum_calibration_error,
            self.maximum_drawdown_fraction,
            self.minimum_execution_observations_per_session,
        ))


@dataclass(frozen=True, slots=True)
class IndependentDemoSessionEvidence:
    forecast: ForecastDeskEvidence
    capital: DemoDeskCapitalState
    shadow_fingerprints: tuple[str, ...]
    broker_assessments: tuple[BrokerDeviationAssessment, ...]
    rule_violations: int = 0

    def __post_init__(self) -> None:
        if self.forecast.desk_id != self.capital.desk_id:
            raise ValueError("forecast/capital desk identity drift")
        if _sha(self.forecast.session_fingerprint, "forecast session") != self.capital.session_fingerprint:
            raise ValueError("forecast/capital session identity drift")
        _sha(self.forecast.champion_fingerprint, "forecast champion")
        object.__setattr__(self, "rule_violations", _nonnegative_int(self.rule_violations, "rule_violations"))
        shadows = tuple(sorted(_sha(value, "session shadow") for value in self.shadow_fingerprints))
        if len(shadows) != len(set(shadows)):
            raise ValueError("session shadow evidence must be unique")
        object.__setattr__(self, "shadow_fingerprints", shadows)
        assessments = tuple(sorted(self.broker_assessments, key=lambda row: row.fingerprint))
        assessment_fps = tuple(row.fingerprint for row in assessments)
        if len(assessment_fps) != len(set(assessment_fps)):
            raise ValueError("session broker assessments must be unique")
        assessment_shadows = tuple(sorted(row.shadow_fingerprint for row in assessments))
        if assessment_shadows != shadows:
            raise ValueError("M187 broker assessments must exactly cover M185 session shadows")
        object.__setattr__(self, "broker_assessments", assessments)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m188-independent-demo-session-v1",
            self.forecast.desk_id,
            self.forecast.champion_fingerprint,
            self.forecast.session_fingerprint,
            self.forecast.completed_forecasts,
            self.forecast.calibration_error,
            self.forecast.net_pnl_after_costs,
            self.forecast.maximum_drawdown_fraction,
            self.forecast.unexpected_clock_faults,
            self.forecast.scheduled_closed_observations,
            self.capital.fingerprint,
            self.shadow_fingerprints,
            tuple(row.fingerprint for row in self.broker_assessments),
            self.rule_violations,
        ))

    @property
    def live_write_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class IndependentSessionPromotionAssessment:
    status: IndependentPromotionStatus
    champion_fingerprint: str
    passing_sessions: int
    evaluated_sessions: int
    blockers: tuple[str, ...]
    session_fingerprints: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    policy_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "champion_fingerprint", _sha(self.champion_fingerprint, "promotion champion"))
        object.__setattr__(self, "passing_sessions", _nonnegative_int(self.passing_sessions, "passing_sessions"))
        object.__setattr__(self, "evaluated_sessions", _nonnegative_int(self.evaluated_sessions, "evaluated_sessions"))
        if self.passing_sessions > self.evaluated_sessions:
            raise ValueError("passing sessions cannot exceed evaluated sessions")
        sessions = tuple(sorted(_sha(value, "promotion session") for value in self.session_fingerprints))
        evidence = tuple(sorted(_sha(value, "promotion evidence") for value in self.evidence_fingerprints))
        if len(sessions) != len(set(sessions)) or len(evidence) != len(set(evidence)):
            raise ValueError("promotion evidence identities must be unique")
        object.__setattr__(self, "session_fingerprints", sessions)
        object.__setattr__(self, "evidence_fingerprints", evidence)
        object.__setattr__(self, "policy_fingerprint", _sha(self.policy_fingerprint, "promotion policy"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m188-independent-session-promotion-v1",
            self.status.value,
            self.champion_fingerprint,
            self.passing_sessions,
            self.evaluated_sessions,
            self.blockers,
            self.session_fingerprints,
            self.evidence_fingerprints,
            self.policy_fingerprint,
        ))

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def champion_mutation_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def assess_independent_session_promotion(
    evidence: Iterable[IndependentDemoSessionEvidence],
    *,
    policy: IndependentPromotionPolicy = IndependentPromotionPolicy(),
) -> IndependentSessionPromotionAssessment:
    rows = tuple(evidence)
    if not rows:
        raise ValueError("independent promotion requires session evidence")

    desk_ids = tuple(row.forecast.desk_id for row in rows)
    session_ids = tuple(_sha(row.forecast.session_fingerprint, "promotion session") for row in rows)
    champion_ids = tuple(_sha(row.forecast.champion_fingerprint, "promotion champion") for row in rows)
    if len(desk_ids) != len(set(desk_ids)):
        raise ValueError("independent promotion cannot count the same desk twice")
    if len(session_ids) != len(set(session_ids)):
        raise ValueError("independent promotion cannot count the same session twice")
    if len(set(champion_ids)) != 1:
        raise ValueError("independent promotion requires one frozen champion")

    all_shadows = tuple(shadow for row in rows for shadow in row.shadow_fingerprints)
    if len(all_shadows) != len(set(all_shadows)):
        raise ValueError("M185 shadow evidence cannot be reused across independent sessions")

    rejected: list[str] = []
    insufficient: list[str] = []
    passing = 0
    for row in rows:
        prefix = f"desk:{row.forecast.desk_id}"
        row_rejected: list[str] = []
        row_insufficient: list[str] = []
        if row.forecast.completed_forecasts < policy.minimum_completed_forecasts:
            row_insufficient.append("insufficient_forecasts")
        if row.forecast.calibration_error > policy.maximum_calibration_error:
            row_rejected.append("miscalibrated")
        if row.forecast.net_pnl_after_costs <= 0:
            row_rejected.append("not_profitable_after_costs")
        if row.forecast.maximum_drawdown_fraction > policy.maximum_drawdown_fraction:
            row_rejected.append("drawdown_exceeded")
        if row.forecast.unexpected_clock_faults:
            row_rejected.append("unexpected_clock_fault")
        if row.rule_violations:
            row_rejected.append("rule_violation")
        if len(row.broker_assessments) < policy.minimum_execution_observations_per_session:
            row_insufficient.append("insufficient_broker_execution_observations")
        for assessment in row.broker_assessments:
            if assessment.status is BrokerDeviationStatus.INCOMPLETE:
                row_insufficient.append("broker_execution_evidence_incomplete")
            elif assessment.status is not BrokerDeviationStatus.WITHIN_POLICY:
                row_rejected.append(f"broker_execution_{assessment.status.value}")
        if row_rejected:
            rejected.extend(f"{prefix}:{reason}" for reason in sorted(set(row_rejected)))
        if row_insufficient:
            insufficient.extend(f"{prefix}:{reason}" for reason in sorted(set(row_insufficient)))
        if not row_rejected and not row_insufficient:
            passing += 1

    if len(rows) < policy.required_sessions:
        insufficient.append("independent_session_count_below_policy")
    if passing < policy.required_sessions:
        insufficient.append("passing_independent_sessions_below_policy")

    if rejected:
        status = IndependentPromotionStatus.REJECTED
        blockers = tuple(sorted(set(rejected + insufficient)))
    elif insufficient:
        status = IndependentPromotionStatus.INSUFFICIENT
        blockers = tuple(sorted(set(insufficient)))
    else:
        status = IndependentPromotionStatus.RESEARCH_PROMOTION_ELIGIBLE
        blockers = ()

    return IndependentSessionPromotionAssessment(
        status,
        champion_ids[0],
        passing,
        len(rows),
        blockers,
        session_ids,
        tuple(row.fingerprint for row in rows),
        policy.fingerprint,
    )
