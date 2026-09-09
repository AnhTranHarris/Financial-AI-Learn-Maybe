from __future__ import annotations

"""Production M188 reconciliation envelope.

Binds broker-history truth to the exact M187 production admission before
reusing the mature reconciliation engine.  No broker mutation or retry authority.
"""

from dataclasses import dataclass
from hashlib import sha256
import json

from .demo_execution_bridge import DemoBridgeExecutionReceipt
from .execution_reconciliation import BrokerExecutionEvidence, ExecutionReconciliation, reconcile_execution
from .m187_production_admission import M187ProductionExecutionAdmission
from .shadow_execution import ShadowExecutionIntent


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class M188ProductionReconciliationEnvelope:
    production_custody_fingerprint: str
    m187_production_admission_fingerprint: str
    m187_receipt_fingerprint: str
    reconciliation_fingerprint: str
    broker_evidence_fingerprint: str

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    position_mutation_authority = False
    promotion_authority = False
    risk_override_authority = False

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m188-production-reconciliation-v1",
            "production_custody_fingerprint": self.production_custody_fingerprint,
            "m187_production_admission_fingerprint": self.m187_production_admission_fingerprint,
            "m187_receipt_fingerprint": self.m187_receipt_fingerprint,
            "reconciliation_fingerprint": self.reconciliation_fingerprint,
            "broker_evidence_fingerprint": self.broker_evidence_fingerprint,
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


def reconcile_production_execution(
    *,
    production_admission: M187ProductionExecutionAdmission,
    shadow: ShadowExecutionIntent,
    receipt: DemoBridgeExecutionReceipt,
    broker: BrokerExecutionEvidence,
) -> tuple[M188ProductionReconciliationEnvelope, ExecutionReconciliation]:
    if production_admission.champion_fingerprint != receipt.admission.champion_fingerprint:
        raise PermissionError("M188 M187 production/core Champion identity drift")
    if production_admission.shadow_fingerprint != shadow.fingerprint:
        raise PermissionError("M188 M187 production/shadow identity drift")
    if production_admission.shadow_fingerprint != receipt.admission.shadow_fingerprint:
        raise PermissionError("M188 M187 production/core shadow identity drift")
    if production_admission.intent_hash != shadow.intent_hash or production_admission.intent_hash != receipt.admission.intent_hash:
        raise PermissionError("M188 M187 production/core intent identity drift")
    if production_admission.permit_fingerprint != receipt.admission.permit_fingerprint:
        raise PermissionError("M188 M187 production/core permit identity drift")

    reconciliation = reconcile_execution(shadow, receipt, broker)
    envelope = M188ProductionReconciliationEnvelope(
        production_custody_fingerprint=production_admission.production_custody_fingerprint,
        m187_production_admission_fingerprint=production_admission.fingerprint,
        m187_receipt_fingerprint=receipt.fingerprint,
        reconciliation_fingerprint=reconciliation.fingerprint,
        broker_evidence_fingerprint=broker.fingerprint,
    )
    return envelope, reconciliation
