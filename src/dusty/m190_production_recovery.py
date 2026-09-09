from __future__ import annotations

"""Production provenance envelope for M190 restart/crash recovery."""

from dataclasses import dataclass
from hashlib import sha256
import json

from .execution_lifecycle import ExecutionRecord
from .execution_reconciliation import ExecutionReconciliation
from .m185_production_custody import ProductionChampionCustodyEnvelope
from .m188_production_reconciliation import M188ProductionReconciliationEnvelope
from .restart_recovery import RecoveryCheckpoint, RecoveryPlan, plan_execution_recovery


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class M190ProductionRecoveryEnvelope:
    production_custody_fingerprint: str
    checkpoint_fingerprint: str
    intent_hash: str
    recovery_plan_fingerprint: str
    m188_production_reconciliation_fingerprint: str | None

    broker_write_authority = False
    live_write_authority = False
    resend_authority = False
    retry_authority = False
    position_mutation_authority = False
    promotion_authority = False
    risk_override_authority = False

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m190-production-recovery-v1",
            "production_custody_fingerprint": self.production_custody_fingerprint,
            "checkpoint_fingerprint": self.checkpoint_fingerprint,
            "intent_hash": self.intent_hash,
            "recovery_plan_fingerprint": self.recovery_plan_fingerprint,
            "m188_production_reconciliation_fingerprint": self.m188_production_reconciliation_fingerprint,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "resend": False,
                "retry": False,
                "position_mutation": False,
                "promotion": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def plan_production_execution_recovery(
    *,
    custody: ProductionChampionCustodyEnvelope,
    checkpoint: RecoveryCheckpoint,
    record: ExecutionRecord,
    admission_artifact_fingerprint: str | None = None,
    production_reconciliation: M188ProductionReconciliationEnvelope | None = None,
    reconciliation: ExecutionReconciliation | None = None,
) -> tuple[M190ProductionRecoveryEnvelope, RecoveryPlan]:
    if record.intent_hash not in checkpoint.execution_intent_hashes:
        raise PermissionError("M190 execution intent is not present in production recovery checkpoint")
    if (production_reconciliation is None) != (reconciliation is None):
        raise ValueError("M190 production/core reconciliation evidence must appear together")
    production_fp: str | None = None
    if production_reconciliation is not None and reconciliation is not None:
        if production_reconciliation.production_custody_fingerprint != custody.fingerprint:
            raise PermissionError("M190 M188 reconciliation belongs to different production custody")
        if production_reconciliation.reconciliation_fingerprint != reconciliation.fingerprint:
            raise PermissionError("M190 M188 production/core reconciliation identity drift")
        if reconciliation.intent_hash != record.intent_hash:
            raise PermissionError("M190 reconciliation intent does not match recovery record")
        production_fp = production_reconciliation.fingerprint

    plan = plan_execution_recovery(
        record,
        admission_artifact_fingerprint=admission_artifact_fingerprint,
        reconciliation=reconciliation,
    )
    if plan.intent_hash != record.intent_hash:
        raise RuntimeError("M190 recovery core returned different intent identity")
    envelope = M190ProductionRecoveryEnvelope(
        production_custody_fingerprint=custody.fingerprint,
        checkpoint_fingerprint=checkpoint.fingerprint,
        intent_hash=record.intent_hash,
        recovery_plan_fingerprint=plan.fingerprint,
        m188_production_reconciliation_fingerprint=production_fp,
    )
    return envelope, plan
