from __future__ import annotations

"""Production provenance wrapper for final M194 Single-Desk Demo Certification."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json

from .artifact_vault import ArtifactKind, ResearchArtifactRecord, ResearchArtifactVault
from .champion_suspension import ChampionSuspensionAssessment
from .m185_production_custody import ProductionChampionCustodyEnvelope
from .m189_production_cost_learning import M189ProductionCostLearningEnvelope
from .m190_production_recovery import M190ProductionRecoveryEnvelope
from .m194_production_runtime_admission import M194ProductionRuntimeAdmission
from .provider_degradation import ProviderFleetAssessment
from .single_desk_demo_certification import (
    DemoDeskRuntimeEvidence,
    DemoOperationalExerciseEvidence,
    MilestoneBuildEvidence,
    SingleDeskDemoCertification,
    SingleDeskDemoPolicy,
    SingleDeskDemoStatus,
    certify_single_demo_desk,
)
from .strategy_drift import StrategyDriftAssessment


PRODUCTION_CERTIFICATION_CONTENT_TYPE = "application/vnd.dusty.m194-production-certification+json;version=2"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def production_recovery_lineage_fingerprint(rows: tuple[M190ProductionRecoveryEnvelope, ...]) -> str:
    fingerprints = tuple(sorted(row.fingerprint for row in rows))
    if not fingerprints or len(fingerprints) != len(set(fingerprints)):
        raise ValueError("M194 production recovery lineage requires unique nonempty M190 envelopes")
    return _digest(("dusty-m190-production-recovery-lineage-v1", fingerprints))


def production_runtime_attestation_fingerprint(
    *,
    custody_fingerprint: str,
    runtime_admission_fingerprint: str,
    execution_learning_fingerprint: str,
    recovery_lineage_fingerprint: str,
    provider_fleet_fingerprint: str,
    drift_fingerprint: str,
    suspension_fingerprint: str,
) -> str:
    return _digest((
        "dusty-m194-production-runtime-attestation-v1",
        custody_fingerprint,
        runtime_admission_fingerprint,
        execution_learning_fingerprint,
        recovery_lineage_fingerprint,
        provider_fleet_fingerprint,
        drift_fingerprint,
        suspension_fingerprint,
    ))


@dataclass(frozen=True, slots=True)
class M194ProductionCertificationEnvelope:
    production_custody_fingerprint: str
    runtime_admission_fingerprint: str
    runtime_evidence_fingerprint: str
    execution_learning_envelope_fingerprint: str
    recovery_lineage_fingerprint: str
    provider_fleet_fingerprint: str
    drift_fingerprint: str
    suspension_fingerprint: str
    certification_fingerprint: str
    certification_status: SingleDeskDemoStatus
    pending_reasons: tuple[str, ...]
    rejection_reasons: tuple[str, ...]

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    position_mutation_authority = False
    promotion_authority = False
    risk_override_authority = False

    def __post_init__(self) -> None:
        if not isinstance(self.certification_status, SingleDeskDemoStatus):
            raise ValueError("M194 production envelope requires SingleDeskDemoStatus")
        pending = tuple(sorted(set(str(row).strip() for row in self.pending_reasons if str(row).strip())))
        rejected = tuple(sorted(set(str(row).strip() for row in self.rejection_reasons if str(row).strip())))
        if self.certification_status is SingleDeskDemoStatus.CERTIFIED and (pending or rejected):
            raise ValueError("CERTIFIED M194 production envelope cannot carry pending/rejection reasons")
        if self.certification_status is SingleDeskDemoStatus.PENDING and not pending:
            raise ValueError("PENDING M194 production envelope requires pending reasons")
        if self.certification_status is SingleDeskDemoStatus.REJECTED and not rejected:
            raise ValueError("REJECTED M194 production envelope requires rejection reasons")
        object.__setattr__(self, "pending_reasons", pending)
        object.__setattr__(self, "rejection_reasons", rejected)

    @property
    def certified(self) -> bool:
        return self.certification_status is SingleDeskDemoStatus.CERTIFIED

    @property
    def production_activation_eligible(self) -> bool:
        return self.certified

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m194-production-certification-v2",
            "production_custody_fingerprint": self.production_custody_fingerprint,
            "runtime_admission_fingerprint": self.runtime_admission_fingerprint,
            "runtime_evidence_fingerprint": self.runtime_evidence_fingerprint,
            "execution_learning_envelope_fingerprint": self.execution_learning_envelope_fingerprint,
            "recovery_lineage_fingerprint": self.recovery_lineage_fingerprint,
            "provider_fleet_fingerprint": self.provider_fleet_fingerprint,
            "drift_fingerprint": self.drift_fingerprint,
            "suspension_fingerprint": self.suspension_fingerprint,
            "certification_fingerprint": self.certification_fingerprint,
            "certification_status": self.certification_status.value,
            "pending_reasons": list(self.pending_reasons),
            "rejection_reasons": list(self.rejection_reasons),
            "production_activation_eligible": self.production_activation_eligible,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "retry": False,
                "position_mutation": False,
                "promotion": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def certification_source_fingerprints(envelope: M194ProductionCertificationEnvelope) -> tuple[str, ...]:
    return tuple(sorted({
        envelope.production_custody_fingerprint,
        envelope.runtime_admission_fingerprint,
        envelope.runtime_evidence_fingerprint,
        envelope.execution_learning_envelope_fingerprint,
        envelope.recovery_lineage_fingerprint,
        envelope.provider_fleet_fingerprint,
        envelope.drift_fingerprint,
        envelope.suspension_fingerprint,
        envelope.certification_fingerprint,
    }))


def persist_production_certification(
    vault: ResearchArtifactVault,
    envelope: M194ProductionCertificationEnvelope,
    *,
    producer_fingerprint: str,
    now: datetime,
) -> ResearchArtifactRecord:
    """Persist the exact M194 production result as immutable M164 evidence."""

    return vault.store_bytes(
        _canonical(envelope.payload).encode("utf-8"),
        kind=ArtifactKind.EVALUATION,
        content_type=PRODUCTION_CERTIFICATION_CONTENT_TYPE,
        producer_fingerprint=producer_fingerprint,
        subject_fingerprint=envelope.certification_fingerprint,
        source_fingerprints=certification_source_fingerprints(envelope),
        now=now,
    )


def certify_production_single_demo_desk(
    *,
    custody: ProductionChampionCustodyEnvelope,
    runtime_admission: M194ProductionRuntimeAdmission,
    prerequisites: tuple[MilestoneBuildEvidence, ...],
    runtime: DemoDeskRuntimeEvidence,
    exercises: tuple[DemoOperationalExerciseEvidence, ...],
    policy: SingleDeskDemoPolicy,
    current_source_commit: str,
    execution_learning: M189ProductionCostLearningEnvelope,
    recovery_envelopes: tuple[M190ProductionRecoveryEnvelope, ...],
    provider_fleet: ProviderFleetAssessment,
    drift: StrategyDriftAssessment,
    suspension: ChampionSuspensionAssessment,
) -> tuple[M194ProductionCertificationEnvelope, SingleDeskDemoCertification]:
    champion = custody.champion_record
    if runtime_admission.production_custody_fingerprint != custody.fingerprint:
        raise PermissionError("M194 runtime admission belongs to different production custody")
    if runtime_admission.champion_fingerprint != champion.fingerprint:
        raise PermissionError("M194 runtime admission Champion drift")
    if runtime_admission.lane_id != champion.lane_id:
        raise PermissionError("M194 runtime admission lane drift")
    if runtime.champion_fingerprint != champion.fingerprint or runtime.lane_id != champion.lane_id:
        raise PermissionError("M194 runtime evidence does not match production custody Champion/lane")
    if runtime.source_commit != runtime_admission.source_commit:
        raise PermissionError("M194 runtime source commit does not match production runtime admission")

    if execution_learning.production_custody_fingerprint != custody.fingerprint:
        raise PermissionError("M194 M189 learning belongs to different production custody")
    if runtime.execution_learning_fingerprint != execution_learning.learning_fingerprint:
        raise PermissionError("M194 runtime M189 learning fingerprint drift")

    if len(recovery_envelopes) < runtime.recovery_checkpoint_count:
        raise PermissionError("M194 production recovery envelopes do not cover recorded recovery checkpoints")
    if any(row.production_custody_fingerprint != custody.fingerprint for row in recovery_envelopes):
        raise PermissionError("M194 M190 recovery lineage mixes production custody identities")
    recovery_lineage = production_recovery_lineage_fingerprint(recovery_envelopes)
    if runtime.recovery_fingerprint != recovery_lineage:
        raise PermissionError("M194 runtime M190 recovery lineage fingerprint drift")

    if runtime.provider_fleet_fingerprint != provider_fleet.fingerprint:
        raise PermissionError("M194 runtime M191 provider fleet fingerprint drift")
    if drift.champion_fingerprint != champion.fingerprint:
        raise PermissionError("M194 M192 drift belongs to different Champion")
    if runtime.drift_fingerprint != drift.fingerprint or runtime.latest_drift_status is not drift.status:
        raise PermissionError("M194 runtime M192 drift identity/status drift")
    if suspension.champion_fingerprint != champion.fingerprint:
        raise PermissionError("M194 M193 suspension belongs to different Champion")
    if suspension.drift_fingerprint != drift.fingerprint:
        raise PermissionError("M194 M193 suspension is not bound to supplied M192 drift")
    if runtime.suspension_fingerprint != suspension.fingerprint:
        raise PermissionError("M194 runtime M193 suspension fingerprint drift")

    expected_attestation = production_runtime_attestation_fingerprint(
        custody_fingerprint=custody.fingerprint,
        runtime_admission_fingerprint=runtime_admission.fingerprint,
        execution_learning_fingerprint=execution_learning.fingerprint,
        recovery_lineage_fingerprint=recovery_lineage,
        provider_fleet_fingerprint=provider_fleet.fingerprint,
        drift_fingerprint=drift.fingerprint,
        suspension_fingerprint=suspension.fingerprint,
    )
    if runtime.runtime_attestation_fingerprint != expected_attestation:
        raise PermissionError("M194 runtime production attestation fingerprint drift")

    certification = certify_single_demo_desk(
        champion,
        prerequisites,
        runtime,
        exercises,
        policy=policy,
        current_source_commit=current_source_commit,
    )
    envelope = M194ProductionCertificationEnvelope(
        production_custody_fingerprint=custody.fingerprint,
        runtime_admission_fingerprint=runtime_admission.fingerprint,
        runtime_evidence_fingerprint=runtime.fingerprint,
        execution_learning_envelope_fingerprint=execution_learning.fingerprint,
        recovery_lineage_fingerprint=recovery_lineage,
        provider_fleet_fingerprint=provider_fleet.fingerprint,
        drift_fingerprint=drift.fingerprint,
        suspension_fingerprint=suspension.fingerprint,
        certification_fingerprint=certification.certification_fingerprint,
        certification_status=certification.status,
        pending_reasons=certification.pending_reasons,
        rejection_reasons=certification.rejection_reasons,
    )
    return envelope, certification
