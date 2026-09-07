from __future__ import annotations

"""M200 Long-Running Soak Test certification contract.

M200 certifies evidence from a prolonged, broker-connected Demo operating run.
It does not implement a second runtime, recovery engine, provider manager, market
clock, execution bridge, or reconciliation path. Those remain owned by earlier
milestones. M200 only proves that the existing organism survived the required
operational disturbances without authority or state-integrity violations.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json

from .six_desk_graduation import (
    SixDeskGraduationCertification,
    SixDeskGraduationStatus,
)


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


class SoakDisturbanceKind(StrEnum):
    MARKET_CLOSURE = "market_closure"
    MT5_RECONNECT = "mt5_reconnect"
    PROVIDER_OR_MODEL_FAILURE = "provider_or_model_failure"
    DATA_GAP = "data_gap"
    PROCESS_RESTART = "process_restart"
    ABNORMAL_BROKER_CONDITION = "abnormal_broker_condition"


class SoakRecoveryStatus(StrEnum):
    RECOVERED = "recovered"
    SAFE_HALT = "safe_halt"
    UNRESOLVED = "unresolved"


class LongRunningSoakStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    CERTIFIED = "certified"


@dataclass(frozen=True, slots=True)
class LongRunningSoakPolicy:
    minimum_duration: timedelta
    required_disturbances: tuple[SoakDisturbanceKind, ...] = tuple(SoakDisturbanceKind)
    minimum_heartbeats: int = 1

    def __post_init__(self) -> None:
        if self.minimum_duration <= timedelta(0):
            raise ValueError("minimum_duration must be positive")
        if self.minimum_heartbeats <= 0:
            raise ValueError("minimum_heartbeats must be positive")
        if len(set(self.required_disturbances)) != len(self.required_disturbances):
            raise ValueError("required_disturbances must be unique")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m200-soak-policy-v1",
            self.minimum_duration.total_seconds(),
            tuple(item.value for item in self.required_disturbances),
            self.minimum_heartbeats,
        ))


@dataclass(frozen=True, slots=True)
class SoakDisturbanceEvidence:
    kind: SoakDisturbanceKind
    occurred_at: datetime
    recovery_status: SoakRecoveryStatus
    evidence_fingerprint: str

    def __post_init__(self) -> None:
        _utc(self.occurred_at, "occurred_at")
        rendered = self.evidence_fingerprint.strip().lower()
        if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
            raise ValueError("evidence_fingerprint requires SHA-256 identity")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m200-disturbance-v1",
            self.kind.value,
            _utc(self.occurred_at, "occurred_at").isoformat(),
            self.recovery_status.value,
            self.evidence_fingerprint,
        ))


@dataclass(frozen=True, slots=True)
class LongRunningSoakEvidence:
    graduation: SixDeskGraduationCertification
    source_commit: str
    started_at: datetime
    ended_at: datetime
    heartbeat_count: int
    disturbances: tuple[SoakDisturbanceEvidence, ...]
    duplicate_action_count: int
    unauthorized_write_count: int
    unresolved_reconciliation_count: int
    state_integrity_ok: bool
    artifact_integrity_ok: bool
    final_safe_state: bool

    def __post_init__(self) -> None:
        start = _utc(self.started_at, "started_at")
        end = _utc(self.ended_at, "ended_at")
        if end <= start:
            raise ValueError("ended_at must be after started_at")
        if self.heartbeat_count < 0:
            raise ValueError("heartbeat_count cannot be negative")
        for name in (
            "duplicate_action_count",
            "unauthorized_write_count",
            "unresolved_reconciliation_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        commit = self.source_commit.strip().lower()
        if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
            raise ValueError("source_commit requires full SHA-1 Git identity")
        fingerprints = tuple(row.fingerprint for row in self.disturbances)
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError("duplicate disturbance evidence prohibited")

    @property
    def duration(self) -> timedelta:
        return _utc(self.ended_at, "ended_at") - _utc(self.started_at, "started_at")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m200-soak-evidence-v1",
            self.graduation.fingerprint,
            self.source_commit,
            _utc(self.started_at, "started_at").isoformat(),
            _utc(self.ended_at, "ended_at").isoformat(),
            self.heartbeat_count,
            tuple(row.fingerprint for row in self.disturbances),
            self.duplicate_action_count,
            self.unauthorized_write_count,
            self.unresolved_reconciliation_count,
            self.state_integrity_ok,
            self.artifact_integrity_ok,
            self.final_safe_state,
        ))


@dataclass(frozen=True, slots=True)
class LongRunningSoakCertification:
    status: LongRunningSoakStatus
    policy_fingerprint: str
    graduation_fingerprint: str
    evidence_fingerprint: str
    observed_duration_seconds: float
    heartbeat_count: int
    observed_disturbances: tuple[str, ...]
    blockers: tuple[str, ...]

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m200-long-running-soak-certification-v1",
            self.status.value,
            self.policy_fingerprint,
            self.graduation_fingerprint,
            self.evidence_fingerprint,
            self.observed_duration_seconds,
            self.heartbeat_count,
            self.observed_disturbances,
            self.blockers,
        ))


def certify_long_running_soak(
    policy: LongRunningSoakPolicy,
    evidence: LongRunningSoakEvidence,
) -> LongRunningSoakCertification:
    """Certify prolonged Demo operation against an explicit campaign policy."""
    blockers: list[str] = []
    hard_reject = False

    if evidence.graduation.status is not SixDeskGraduationStatus.GRADUATED:
        blockers.append("m199_graduation_not_proven")
        hard_reject = True

    if evidence.duration < policy.minimum_duration:
        blockers.append("minimum_duration_not_met")

    if evidence.heartbeat_count < policy.minimum_heartbeats:
        blockers.append("minimum_heartbeats_not_met")

    observed = {row.kind for row in evidence.disturbances}
    missing = tuple(item for item in policy.required_disturbances if item not in observed)
    if missing:
        blockers.extend(f"missing_disturbance:{item.value}" for item in missing)

    if any(row.recovery_status is SoakRecoveryStatus.UNRESOLVED for row in evidence.disturbances):
        blockers.append("unresolved_disturbance")
        hard_reject = True

    if evidence.duplicate_action_count:
        blockers.append("duplicate_action_detected")
        hard_reject = True

    if evidence.unauthorized_write_count:
        blockers.append("unauthorized_write_detected")
        hard_reject = True

    if evidence.unresolved_reconciliation_count:
        blockers.append("unresolved_reconciliation")
        hard_reject = True

    if not evidence.state_integrity_ok:
        blockers.append("state_integrity_failed")
        hard_reject = True

    if not evidence.artifact_integrity_ok:
        blockers.append("artifact_integrity_failed")
        hard_reject = True

    if not evidence.final_safe_state:
        blockers.append("final_safe_state_failed")
        hard_reject = True

    blockers = list(dict.fromkeys(blockers))
    if hard_reject:
        status = LongRunningSoakStatus.REJECTED
    elif blockers:
        status = LongRunningSoakStatus.PENDING
    else:
        status = LongRunningSoakStatus.CERTIFIED

    return LongRunningSoakCertification(
        status=status,
        policy_fingerprint=policy.fingerprint,
        graduation_fingerprint=evidence.graduation.fingerprint,
        evidence_fingerprint=evidence.fingerprint,
        observed_duration_seconds=evidence.duration.total_seconds(),
        heartbeat_count=evidence.heartbeat_count,
        observed_disturbances=tuple(sorted(item.value for item in observed)),
        blockers=tuple(blockers),
    )
