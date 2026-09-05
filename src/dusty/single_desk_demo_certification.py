from __future__ import annotations

"""M194 deterministic Single-Desk Demo Certification.

M194 is a certification boundary over evidence produced by the already-certified
M185-M193 operational stack.  It does not trade, restart processes, mutate a
Champion, or manufacture runtime evidence.  CI may prove that this gate behaves
correctly, but a CERTIFIED desk additionally requires evidence explicitly marked
as real Demo runtime / controlled Demo exercises from the target workstation.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json

from .champion_registry import FrozenChampionRecord
from .demo_execution_cost_learning import DemoCostLearningStatus
from .strategy_drift import StrategyDriftStatus


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires a 40- or 64-character hexadecimal identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: str, label: str, *, maximum: int = 256) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _nonnegative_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return int(value)


def _positive_int(value: int, label: str) -> int:
    rendered = _nonnegative_int(value, label)
    if rendered < 1:
        raise ValueError(f"{label} must be positive")
    return rendered


REQUIRED_BUILD_MILESTONES = tuple(f"M{number}" for number in range(185, 194))


class DemoEvidenceOrigin(StrEnum):
    REAL_DEMO_RUNTIME = "real_demo_runtime"
    CONTROLLED_DEMO_EXERCISE = "controlled_demo_exercise"
    CI_FIXTURE = "ci_fixture"


class DemoOperationalScenario(StrEnum):
    PROCESS_RESTART = "process_restart"
    MT5_RESTART = "mt5_restart"
    NETWORK_INTERRUPTION = "network_interruption"
    PROVIDER_FAILURE = "provider_failure"
    PROVIDER_IDENTITY_DRIFT = "provider_identity_drift"
    AMBIGUOUS_SEND = "ambiguous_send"
    PARTIAL_FILL = "partial_fill"
    ORDER_REJECTION = "order_rejection"
    STALE_MARKET_DATA = "stale_market_data"
    ACCOUNT_PERMISSION_DRIFT = "account_permission_drift"
    DEMO_TO_LIVE_DRIFT = "demo_to_live_drift"
    AUTOMATIC_CHAMPION_SUSPENSION = "automatic_champion_suspension"


REQUIRED_OPERATIONAL_SCENARIOS = tuple(DemoOperationalScenario)


class SingleDeskDemoStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    CERTIFIED = "certified"


@dataclass(frozen=True, slots=True)
class MilestoneBuildEvidence:
    milestone: str
    source_commit: str
    artifact_fingerprint: str
    ci_fingerprint: str
    passed: bool

    def __post_init__(self) -> None:
        milestone = str(self.milestone).strip().upper()
        if milestone not in REQUIRED_BUILD_MILESTONES:
            raise ValueError(f"unsupported M194 prerequisite milestone: {milestone}")
        object.__setattr__(self, "milestone", milestone)
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "milestone source commit"))
        object.__setattr__(self, "artifact_fingerprint", _sha(self.artifact_fingerprint, "milestone artifact"))
        object.__setattr__(self, "ci_fingerprint", _sha(self.ci_fingerprint, "milestone CI evidence"))
        if not isinstance(self.passed, bool):
            raise ValueError("milestone passed must be boolean")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m194-prerequisite-milestone-v1",
            self.milestone,
            self.source_commit,
            self.artifact_fingerprint,
            self.ci_fingerprint,
            self.passed,
        ))


@dataclass(frozen=True, slots=True)
class SingleDeskDemoPolicy:
    minimum_runtime_seconds: int
    minimum_heartbeats: int
    minimum_completed_cycles: int
    minimum_reconciled_executions: int
    minimum_execution_cost_samples: int
    minimum_recovery_checkpoints: int
    required_scenarios: tuple[DemoOperationalScenario, ...] = REQUIRED_OPERATIONAL_SCENARIOS

    def __post_init__(self) -> None:
        for name in (
            "minimum_runtime_seconds",
            "minimum_heartbeats",
            "minimum_completed_cycles",
            "minimum_reconciled_executions",
            "minimum_execution_cost_samples",
            "minimum_recovery_checkpoints",
        ):
            object.__setattr__(self, name, _positive_int(getattr(self, name), name))
        scenarios = tuple(self.required_scenarios)
        if not scenarios or len(scenarios) != len(set(scenarios)):
            raise ValueError("M194 required scenarios must be unique and nonempty")
        if any(not isinstance(row, DemoOperationalScenario) for row in scenarios):
            raise ValueError("M194 required scenarios must use DemoOperationalScenario")
        object.__setattr__(self, "required_scenarios", tuple(sorted(scenarios, key=lambda row: row.value)))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m194-single-desk-policy-v1",
            self.minimum_runtime_seconds,
            self.minimum_heartbeats,
            self.minimum_completed_cycles,
            self.minimum_reconciled_executions,
            self.minimum_execution_cost_samples,
            self.minimum_recovery_checkpoints,
            tuple(row.value for row in self.required_scenarios),
        ))


@dataclass(frozen=True, slots=True)
class DemoDeskRuntimeEvidence:
    origin: DemoEvidenceOrigin
    desk_run_id: str
    lane_id: str
    champion_fingerprint: str
    session_fingerprint: str
    broker_profile_fingerprint: str
    terminal_fingerprint: str
    account_fingerprint: str
    source_commit: str
    started_at: datetime
    ended_at: datetime
    heartbeat_count: int
    completed_cycles: int
    reconciled_execution_count: int
    unresolved_execution_count: int
    execution_cost_sample_count: int
    recovery_checkpoint_count: int
    duplicate_action_count: int
    unauthorized_broker_write_count: int
    ledger_integrity_ok: bool
    artifact_integrity_ok: bool
    live_write_authorized: bool
    execution_cost_status: DemoCostLearningStatus
    deterministic_core_operational: bool
    latest_drift_status: StrategyDriftStatus
    automatic_suspension_exercise_passed: bool
    ledger_fingerprint: str
    artifact_vault_fingerprint: str
    execution_learning_fingerprint: str
    recovery_fingerprint: str
    provider_fleet_fingerprint: str
    drift_fingerprint: str
    suspension_fingerprint: str
    runtime_attestation_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.origin, DemoEvidenceOrigin):
            raise ValueError("runtime origin must use DemoEvidenceOrigin")
        object.__setattr__(self, "desk_run_id", _text(self.desk_run_id, "desk_run_id", maximum=128))
        object.__setattr__(self, "lane_id", _text(self.lane_id, "lane_id", maximum=128).lower())
        for field, label in (
            ("champion_fingerprint", "runtime Champion"),
            ("session_fingerprint", "runtime session"),
            ("broker_profile_fingerprint", "runtime broker profile"),
            ("terminal_fingerprint", "runtime terminal"),
            ("account_fingerprint", "runtime account"),
            ("ledger_fingerprint", "runtime execution ledger"),
            ("artifact_vault_fingerprint", "runtime artifact vault"),
            ("execution_learning_fingerprint", "runtime M189 learning"),
            ("recovery_fingerprint", "runtime M190 recovery"),
            ("provider_fleet_fingerprint", "runtime M191 provider fleet"),
            ("drift_fingerprint", "runtime M192 drift"),
            ("suspension_fingerprint", "runtime M193 suspension exercise"),
            ("runtime_attestation_fingerprint", "runtime attestation"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "runtime source commit"))
        start = _aware(self.started_at, "runtime started_at")
        end = _aware(self.ended_at, "runtime ended_at")
        if end <= start:
            raise ValueError("runtime ended_at must follow started_at")
        object.__setattr__(self, "started_at", start)
        object.__setattr__(self, "ended_at", end)
        for name in (
            "heartbeat_count",
            "completed_cycles",
            "reconciled_execution_count",
            "unresolved_execution_count",
            "execution_cost_sample_count",
            "recovery_checkpoint_count",
            "duplicate_action_count",
            "unauthorized_broker_write_count",
        ):
            object.__setattr__(self, name, _nonnegative_int(getattr(self, name), name))
        for name in (
            "ledger_integrity_ok",
            "artifact_integrity_ok",
            "live_write_authorized",
            "deterministic_core_operational",
            "automatic_suspension_exercise_passed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")
        if not isinstance(self.execution_cost_status, DemoCostLearningStatus):
            raise ValueError("execution_cost_status must use DemoCostLearningStatus")
        if not isinstance(self.latest_drift_status, StrategyDriftStatus):
            raise ValueError("latest_drift_status must use StrategyDriftStatus")

    @property
    def runtime_seconds(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds())

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m194-demo-runtime-evidence-v1",
            self.origin.value,
            self.desk_run_id,
            self.lane_id,
            self.champion_fingerprint,
            self.session_fingerprint,
            self.broker_profile_fingerprint,
            self.terminal_fingerprint,
            self.account_fingerprint,
            self.source_commit,
            self.started_at.isoformat(),
            self.ended_at.isoformat(),
            self.heartbeat_count,
            self.completed_cycles,
            self.reconciled_execution_count,
            self.unresolved_execution_count,
            self.execution_cost_sample_count,
            self.recovery_checkpoint_count,
            self.duplicate_action_count,
            self.unauthorized_broker_write_count,
            self.ledger_integrity_ok,
            self.artifact_integrity_ok,
            self.live_write_authorized,
            self.execution_cost_status.value,
            self.deterministic_core_operational,
            self.latest_drift_status.value,
            self.automatic_suspension_exercise_passed,
            self.ledger_fingerprint,
            self.artifact_vault_fingerprint,
            self.execution_learning_fingerprint,
            self.recovery_fingerprint,
            self.provider_fleet_fingerprint,
            self.drift_fingerprint,
            self.suspension_fingerprint,
            self.runtime_attestation_fingerprint,
        ))


@dataclass(frozen=True, slots=True)
class DemoOperationalExerciseEvidence:
    origin: DemoEvidenceOrigin
    scenario: DemoOperationalScenario
    desk_run_id: str
    source_commit: str
    started_at: datetime
    ended_at: datetime
    passed: bool
    duplicate_action_count: int
    unauthorized_broker_write_count: int
    evidence_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.origin, DemoEvidenceOrigin) or not isinstance(self.scenario, DemoOperationalScenario):
            raise ValueError("exercise origin/scenario types are invalid")
        object.__setattr__(self, "desk_run_id", _text(self.desk_run_id, "exercise desk_run_id", maximum=128))
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "exercise source commit"))
        start = _aware(self.started_at, "exercise started_at")
        end = _aware(self.ended_at, "exercise ended_at")
        if end <= start:
            raise ValueError("exercise ended_at must follow started_at")
        object.__setattr__(self, "started_at", start)
        object.__setattr__(self, "ended_at", end)
        if not isinstance(self.passed, bool):
            raise ValueError("exercise passed must be boolean")
        object.__setattr__(self, "duplicate_action_count", _nonnegative_int(self.duplicate_action_count, "exercise duplicate actions"))
        object.__setattr__(self, "unauthorized_broker_write_count", _nonnegative_int(self.unauthorized_broker_write_count, "exercise unauthorized writes"))
        evidence = tuple(sorted(_sha(value, "exercise evidence") for value in self.evidence_fingerprints))
        if not evidence or len(evidence) != len(set(evidence)):
            raise ValueError("exercise evidence must be unique and nonempty")
        object.__setattr__(self, "evidence_fingerprints", evidence)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m194-operational-exercise-v1",
            self.origin.value,
            self.scenario.value,
            self.desk_run_id,
            self.source_commit,
            self.started_at.isoformat(),
            self.ended_at.isoformat(),
            self.passed,
            self.duplicate_action_count,
            self.unauthorized_broker_write_count,
            self.evidence_fingerprints,
        ))


@dataclass(frozen=True, slots=True)
class SingleDeskDemoCertification:
    status: SingleDeskDemoStatus
    champion_fingerprint: str
    runtime_fingerprint: str
    policy_fingerprint: str
    prerequisite_fingerprints: tuple[str, ...]
    exercise_fingerprints: tuple[str, ...]
    pending_reasons: tuple[str, ...]
    rejection_reasons: tuple[str, ...]
    certification_fingerprint: str
    live_write_authorized: bool = False

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


def certify_single_demo_desk(
    champion: FrozenChampionRecord,
    prerequisites: tuple[MilestoneBuildEvidence, ...],
    runtime: DemoDeskRuntimeEvidence,
    exercises: tuple[DemoOperationalExerciseEvidence, ...],
    *,
    policy: SingleDeskDemoPolicy,
    current_source_commit: str,
) -> SingleDeskDemoCertification:
    """Certify one General Demo Desk from immutable runtime and exercise evidence."""

    current = _git_sha(current_source_commit, "M194 current source commit")
    pending: list[str] = []
    rejected: list[str] = []

    prerequisite_map: dict[str, MilestoneBuildEvidence] = {}
    for row in prerequisites:
        if row.milestone in prerequisite_map:
            rejected.append(f"duplicate_prerequisite:{row.milestone}")
        else:
            prerequisite_map[row.milestone] = row
    for milestone in REQUIRED_BUILD_MILESTONES:
        row = prerequisite_map.get(milestone)
        if row is None:
            pending.append(f"missing_prerequisite:{milestone}")
        elif not row.passed:
            rejected.append(f"failed_prerequisite:{milestone}")

    if runtime.origin is not DemoEvidenceOrigin.REAL_DEMO_RUNTIME:
        pending.append("real_demo_runtime_evidence_required")
    if runtime.source_commit != current:
        rejected.append("runtime_source_commit_mismatch")
    if runtime.champion_fingerprint != champion.fingerprint:
        rejected.append("runtime_champion_identity_mismatch")
    if runtime.lane_id != champion.lane_id:
        rejected.append("runtime_lane_identity_mismatch")
    if runtime.started_at <= champion.created_at:
        rejected.append("runtime_must_begin_after_champion_creation")
    if runtime.runtime_seconds < policy.minimum_runtime_seconds:
        pending.append("runtime_duration_insufficient")
    if runtime.heartbeat_count < policy.minimum_heartbeats:
        pending.append("heartbeat_depth_insufficient")
    if runtime.completed_cycles < policy.minimum_completed_cycles:
        pending.append("completed_cycle_depth_insufficient")
    if runtime.reconciled_execution_count < policy.minimum_reconciled_executions:
        pending.append("reconciled_execution_depth_insufficient")
    if runtime.execution_cost_sample_count < policy.minimum_execution_cost_samples:
        pending.append("execution_cost_sample_depth_insufficient")
    if runtime.recovery_checkpoint_count < policy.minimum_recovery_checkpoints:
        pending.append("recovery_checkpoint_depth_insufficient")
    if runtime.unresolved_execution_count:
        rejected.append("unresolved_execution_state_present")
    if runtime.duplicate_action_count:
        rejected.append("duplicate_action_detected")
    if runtime.unauthorized_broker_write_count:
        rejected.append("unauthorized_broker_write_detected")
    if not runtime.ledger_integrity_ok:
        rejected.append("execution_ledger_integrity_failed")
    if not runtime.artifact_integrity_ok:
        rejected.append("artifact_vault_integrity_failed")
    if runtime.live_write_authorized:
        rejected.append("live_write_must_remain_false")
    if runtime.execution_cost_status is not DemoCostLearningStatus.CALIBRATED:
        pending.append("M189_demo_execution_costs_not_calibrated")
    if not runtime.deterministic_core_operational:
        rejected.append("M191_deterministic_core_not_operational")
    if runtime.latest_drift_status in {StrategyDriftStatus.INSUFFICIENT, StrategyDriftStatus.WATCH}:
        pending.append(f"M192_drift_not_clear:{runtime.latest_drift_status.value}")
    elif runtime.latest_drift_status is not StrategyDriftStatus.STABLE:
        rejected.append(f"M192_drift_breach:{runtime.latest_drift_status.value}")
    if not runtime.automatic_suspension_exercise_passed:
        pending.append("M193_automatic_suspension_path_not_exercised")

    exercise_map: dict[DemoOperationalScenario, DemoOperationalExerciseEvidence] = {}
    for row in exercises:
        if row.scenario in exercise_map:
            rejected.append(f"duplicate_operational_exercise:{row.scenario.value}")
            continue
        exercise_map[row.scenario] = row
        if row.origin is not DemoEvidenceOrigin.CONTROLLED_DEMO_EXERCISE:
            pending.append(f"controlled_demo_exercise_required:{row.scenario.value}")
        if row.desk_run_id != runtime.desk_run_id:
            rejected.append(f"exercise_desk_identity_mismatch:{row.scenario.value}")
        if row.source_commit != current:
            rejected.append(f"exercise_source_commit_mismatch:{row.scenario.value}")
        if row.started_at < runtime.started_at or row.ended_at > runtime.ended_at:
            rejected.append(f"exercise_outside_runtime_window:{row.scenario.value}")
        if not row.passed:
            rejected.append(f"operational_exercise_failed:{row.scenario.value}")
        if row.duplicate_action_count:
            rejected.append(f"exercise_duplicate_action:{row.scenario.value}")
        if row.unauthorized_broker_write_count:
            rejected.append(f"exercise_unauthorized_broker_write:{row.scenario.value}")
    for scenario in policy.required_scenarios:
        if scenario not in exercise_map:
            pending.append(f"missing_operational_exercise:{scenario.value}")

    prerequisite_fingerprints = tuple(sorted(row.fingerprint for row in prerequisites))
    exercise_fingerprints = tuple(sorted(row.fingerprint for row in exercises))
    pending_tuple = tuple(sorted(set(pending)))
    rejected_tuple = tuple(sorted(set(rejected)))
    status = (
        SingleDeskDemoStatus.REJECTED
        if rejected_tuple
        else SingleDeskDemoStatus.PENDING
        if pending_tuple
        else SingleDeskDemoStatus.CERTIFIED
    )
    certification_fingerprint = _digest((
        "dusty-m194-single-desk-demo-certification-v1",
        status.value,
        champion.fingerprint,
        runtime.fingerprint,
        policy.fingerprint,
        prerequisite_fingerprints,
        exercise_fingerprints,
        pending_tuple,
        rejected_tuple,
        current,
        False,
    ))
    return SingleDeskDemoCertification(
        status,
        champion.fingerprint,
        runtime.fingerprint,
        policy.fingerprint,
        prerequisite_fingerprints,
        exercise_fingerprints,
        pending_tuple,
        rejected_tuple,
        certification_fingerprint,
        False,
    )
