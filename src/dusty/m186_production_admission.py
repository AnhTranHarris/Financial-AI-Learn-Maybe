from __future__ import annotations

"""Production admission boundary for M186 Shadow Execution Mode.

The reusable M186 shadow engine accepts a FrozenChampionRecord directly.  The
production path must not: it requires the stronger M185 production custody
envelope so a software-only or fixture Champion cannot bypass M165-M174 custody.
This wrapper grants no execution authority and delegates only to the no-send
shadow capture primitive.
"""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json

from .champion_registry import ChampionLifecycleState, FrozenChampionRegistry
from .cognition import CognitionAssessment
from .m185_production_custody import ProductionChampionCustodyEnvelope
from .order_intent import OrderIntent
from .shadow_execution import (
    ShadowCapturePolicy,
    ShadowExecutionIntent,
    ShadowMarketQuote,
    capture_shadow_intent,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class M186ProductionShadowAdmission:
    production_custody_fingerprint: str
    champion_fingerprint: str
    deployment_fingerprint: str
    lane_id: str
    strategy_fingerprint: str
    shadow_intent_fingerprint: str

    broker_write_authority = False
    live_write_authority = False
    order_send_authority = False
    retry_authority = False
    position_mutation_authority = False
    promotion_authority = False
    risk_override_authority = False

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m186-production-shadow-admission-v1",
            "production_custody_fingerprint": self.production_custody_fingerprint,
            "champion_fingerprint": self.champion_fingerprint,
            "deployment_fingerprint": self.deployment_fingerprint,
            "lane_id": self.lane_id,
            "strategy_fingerprint": self.strategy_fingerprint,
            "shadow_intent_fingerprint": self.shadow_intent_fingerprint,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "order_send": False,
                "retry": False,
                "position_mutation": False,
                "promotion": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def capture_production_shadow_intent(
    *,
    registry: FrozenChampionRegistry,
    custody: ProductionChampionCustodyEnvelope,
    intent: OrderIntent,
    cognition: CognitionAssessment,
    capture_quote: ShadowMarketQuote,
    captured_at: datetime,
    policy: ShadowCapturePolicy,
) -> tuple[M186ProductionShadowAdmission, ShadowExecutionIntent]:
    champion = custody.champion_record
    if registry.state(champion.fingerprint) is not ChampionLifecycleState.ACTIVE:
        raise PermissionError("M186 production shadow requires ACTIVE production Champion")
    active = registry.active_for_lane(champion.lane_id)
    if active is None or active.fingerprint != champion.fingerprint:
        raise PermissionError("M186 production custody Champion is not unique active Champion for lane")
    if active.deployment_fingerprint != champion.deployment_fingerprint:
        raise ValueError("M186 production Champion deployment identity drift")
    if active.strategy_fingerprint != champion.strategy_fingerprint:
        raise ValueError("M186 production Champion strategy identity drift")
    if intent.strategy_hash != champion.strategy_fingerprint:
        raise ValueError("M186 production OrderIntent strategy does not match custody Champion")
    if custody.selection_evidence_fingerprint != champion.selection_evidence_fingerprint:
        raise ValueError("M186 production custody selection identity drift")

    shadow = capture_shadow_intent(
        registry,
        champion,
        intent,
        cognition,
        capture_quote,
        captured_at=captured_at,
        policy=policy,
    )
    admission = M186ProductionShadowAdmission(
        production_custody_fingerprint=custody.fingerprint,
        champion_fingerprint=champion.fingerprint,
        deployment_fingerprint=champion.deployment_fingerprint,
        lane_id=champion.lane_id,
        strategy_fingerprint=champion.strategy_fingerprint,
        shadow_intent_fingerprint=shadow.fingerprint,
    )
    return admission, shadow
