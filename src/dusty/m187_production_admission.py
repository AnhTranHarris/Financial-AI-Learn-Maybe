from __future__ import annotations

"""Production admission boundary for normal M187 Demo execution.

The mature DemoExecutionBridge remains the sole normal Champion execution bridge
and DemoMT5ExecutionAdapter remains the sole raw order_send owner.  This wrapper
prevents production callers from bypassing the stronger M185 custody and M186
production-shadow envelopes.  The special pre-Champion M165 calibration bridge
is intentionally separate and unaffected.
"""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json

from .artifact_vault import ResearchArtifactRecord
from .demo_execution_bridge import (
    DemoBridgeExecutionReceipt,
    DemoBridgePermit,
    DemoExecutionBridge,
)
from .m185_production_custody import ProductionChampionCustodyEnvelope
from .m186_production_admission import M186ProductionShadowAdmission
from .order_intent import BrokerPreflight
from .shadow_execution import ShadowExecutionIntent


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class M187ProductionExecutionAdmission:
    production_custody_fingerprint: str
    m186_production_admission_fingerprint: str
    champion_fingerprint: str
    shadow_fingerprint: str
    intent_hash: str
    permit_fingerprint: str

    live_write_authority = False
    retry_authority = False
    promotion_authority = False
    strategy_mutation_authority = False
    risk_override_authority = False
    raw_order_send_authority = False

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m187-production-execution-admission-v1",
            "production_custody_fingerprint": self.production_custody_fingerprint,
            "m186_production_admission_fingerprint": self.m186_production_admission_fingerprint,
            "champion_fingerprint": self.champion_fingerprint,
            "shadow_fingerprint": self.shadow_fingerprint,
            "intent_hash": self.intent_hash,
            "permit_fingerprint": self.permit_fingerprint,
            "authority": {
                "demo_write": True,
                "live_write": False,
                "retry": False,
                "promotion": False,
                "strategy_mutation": False,
                "risk_override": False,
                "raw_order_send": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def execute_production_demo(
    *,
    bridge: DemoExecutionBridge,
    custody: ProductionChampionCustodyEnvelope,
    shadow_admission: M186ProductionShadowAdmission,
    shadow: ShadowExecutionIntent,
    shadow_artifact: ResearchArtifactRecord,
    preflight: BrokerPreflight,
    permit: DemoBridgePermit,
    at: datetime,
) -> tuple[M187ProductionExecutionAdmission, DemoBridgeExecutionReceipt]:
    champion = custody.champion_record
    if shadow_admission.production_custody_fingerprint != custody.fingerprint:
        raise PermissionError("M187 production shadow admission does not match M185 custody")
    if shadow_admission.champion_fingerprint != champion.fingerprint:
        raise PermissionError("M187 production shadow admission Champion drift")
    if shadow_admission.deployment_fingerprint != champion.deployment_fingerprint:
        raise PermissionError("M187 production shadow admission deployment drift")
    if shadow_admission.lane_id != champion.lane_id:
        raise PermissionError("M187 production shadow admission lane drift")
    if shadow_admission.strategy_fingerprint != champion.strategy_fingerprint:
        raise PermissionError("M187 production shadow admission strategy drift")
    if shadow_admission.shadow_intent_fingerprint != shadow.fingerprint:
        raise PermissionError("M187 production shadow admission intent drift")
    if shadow.champion_fingerprint != champion.fingerprint:
        raise PermissionError("M187 production shadow Champion drift")
    if shadow.intent_hash != preflight.intent.intent_hash:
        raise PermissionError("M187 production BrokerPreflight intent drift")

    required_authorization = {
        custody.fingerprint,
        shadow_admission.fingerprint,
    }
    if not required_authorization.issubset(set(permit.authorization_evidence_fingerprints)):
        raise PermissionError("M187 Demo permit lacks M185/M186 production authorization evidence")
    if permit.champion_fingerprint != champion.fingerprint:
        raise PermissionError("M187 production permit Champion drift")

    production_admission = M187ProductionExecutionAdmission(
        production_custody_fingerprint=custody.fingerprint,
        m186_production_admission_fingerprint=shadow_admission.fingerprint,
        champion_fingerprint=champion.fingerprint,
        shadow_fingerprint=shadow.fingerprint,
        intent_hash=preflight.intent.intent_hash,
        permit_fingerprint=permit.fingerprint,
    )
    receipt = bridge.execute(
        champion=champion,
        shadow=shadow,
        shadow_artifact=shadow_artifact,
        preflight=preflight,
        permit=permit,
        at=at,
    )
    if receipt.admission.champion_fingerprint != champion.fingerprint:
        raise RuntimeError("M187 core receipt Champion drifted from production custody")
    if receipt.admission.shadow_fingerprint != shadow.fingerprint:
        raise RuntimeError("M187 core receipt shadow drifted from production admission")
    if receipt.admission.intent_hash != preflight.intent.intent_hash:
        raise RuntimeError("M187 core receipt intent drifted from production admission")
    return production_admission, receipt
