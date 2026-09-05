from __future__ import annotations

"""M187 guarded Demo Execution Bridge.

M187 is an admission layer in front of the already-existing
DemoMT5ExecutionAdapter. It deliberately does not import MetaTrader5 and never
calls order_send itself. The bridge verifies the currently ACTIVE M185 Frozen
Champion, an exact persisted M186 shadow-intent artifact, the latched DemoSession,
a passed BrokerPreflight, and a finite DEMO-only permit before delegating exactly
once to the existing adapter.

The complete admission envelope is persisted before the broker call. Actual
order/deal/position state remains authoritative in the existing crash-safe
execution ledger and is reconciled in M188; M187 does not create a second broker
ledger.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json

from .artifact_vault import ArtifactKind, ResearchArtifactRecord, ResearchArtifactVault
from .champion_registry import ChampionLifecycleState, FrozenChampionRecord, FrozenChampionRegistry
from .demo_execution import DemoExecutionResult, DemoMT5ExecutionAdapter
from .demo_session import AccountMode, DemoSession
from .order_intent import BrokerPreflight, OrderIntent
from .shadow_execution import SHADOW_INTENT_CONTENT_TYPE, ShadowExecutionIntent


DEMO_BRIDGE_ADMISSION_CONTENT_TYPE = "application/vnd.dusty.m187-demo-admission+json"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
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


@dataclass(frozen=True, slots=True)
class DemoBridgePermit:
    champion_fingerprint: str
    lane_id: str
    session_fingerprint: str
    issuer_fingerprint: str
    authorization_evidence_fingerprints: tuple[str, ...]
    valid_from: datetime
    valid_until: datetime
    purpose: str = "demo_order_send"

    def __post_init__(self) -> None:
        for field, label in (
            ("champion_fingerprint", "permit Champion"),
            ("session_fingerprint", "permit session"),
            ("issuer_fingerprint", "permit issuer"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "lane_id", _text(self.lane_id, "permit lane", maximum=128).lower())
        evidence = tuple(sorted(_sha(value, "permit evidence") for value in self.authorization_evidence_fingerprints))
        if not evidence or len(evidence) != len(set(evidence)):
            raise ValueError("DEMO permit requires unique authorization evidence")
        object.__setattr__(self, "authorization_evidence_fingerprints", evidence)
        start = _aware(self.valid_from, "permit valid_from")
        end = _aware(self.valid_until, "permit valid_until")
        if end <= start:
            raise ValueError("DEMO permit valid_until must follow valid_from")
        object.__setattr__(self, "valid_from", start)
        object.__setattr__(self, "valid_until", end)
        if self.purpose != "demo_order_send":
            raise ValueError("M187 permit purpose is fixed to demo_order_send")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m187-demo-bridge-permit-v1",
            "champion_fingerprint": self.champion_fingerprint,
            "lane_id": self.lane_id,
            "session_fingerprint": self.session_fingerprint,
            "issuer_fingerprint": self.issuer_fingerprint,
            "authorization_evidence_fingerprints": list(self.authorization_evidence_fingerprints),
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "purpose": self.purpose,
            "live_write_authority": False,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def demo_write_authority(self) -> bool:
        return True

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def strategy_mutation_authority(self) -> bool:
        return False

    def active_at(self, at: datetime) -> bool:
        observed = _aware(at, "permit evaluation time")
        return self.valid_from <= observed <= self.valid_until


@dataclass(frozen=True, slots=True)
class DemoBridgeAdmission:
    permit_fingerprint: str
    champion_fingerprint: str
    shadow_fingerprint: str
    shadow_artifact_record_fingerprint: str
    intent_hash: str
    session_fingerprint: str
    admitted_at: datetime

    def __post_init__(self) -> None:
        for field, label in (
            ("permit_fingerprint", "admission permit"),
            ("champion_fingerprint", "admission Champion"),
            ("shadow_fingerprint", "admission shadow"),
            ("shadow_artifact_record_fingerprint", "admission shadow artifact"),
            ("intent_hash", "admission intent"),
            ("session_fingerprint", "admission session"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "admitted_at", _aware(self.admitted_at, "admitted_at"))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m187-demo-bridge-admission-v1",
            "permit_fingerprint": self.permit_fingerprint,
            "champion_fingerprint": self.champion_fingerprint,
            "shadow_fingerprint": self.shadow_fingerprint,
            "shadow_artifact_record_fingerprint": self.shadow_artifact_record_fingerprint,
            "intent_hash": self.intent_hash,
            "session_fingerprint": self.session_fingerprint,
            "admitted_at": self.admitted_at.isoformat(),
            "live_write_authority": False,
            "retry_authority": False,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def retry_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class DemoBridgeExecutionReceipt:
    admission: DemoBridgeAdmission
    admission_artifact_record_fingerprint: str
    execution: DemoExecutionResult

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "admission_artifact_record_fingerprint",
            _sha(self.admission_artifact_record_fingerprint, "admission artifact record"),
        )
        if _sha(self.execution.intent_hash, "execution result intent") != self.admission.intent_hash:
            raise ValueError("adapter execution result intent does not match bridge admission")

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m187-demo-bridge-receipt-v2",
                self.admission.fingerprint,
                self.admission_artifact_record_fingerprint,
                self.execution.intent_hash,
                self.execution.state.value,
                int(self.execution.retcode),
                int(self.execution.order_ticket),
                int(self.execution.deal_ticket),
                str(self.execution.comment),
            )
        )

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def retry_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


class DemoExecutionBridge:
    """Validate M185/M186/DEMO admission, then delegate to the sole send adapter."""

    def __init__(
        self,
        *,
        registry: FrozenChampionRegistry,
        vault: ResearchArtifactVault,
        session: DemoSession,
        adapter: DemoMT5ExecutionAdapter,
        producer_fingerprint: str,
    ) -> None:
        if session.identity.account_mode is not AccountMode.DEMO:
            raise ValueError("M187 bridge accepts DEMO sessions only")
        self._registry = registry
        self._vault = vault
        self._session = session
        self._adapter = adapter
        self._producer = _sha(producer_fingerprint, "M187 bridge producer")

    @property
    def demo_write_authorized(self) -> bool:
        """There is no ambient write authority without a specific active permit."""
        return False

    @property
    def live_write_authorized(self) -> bool:
        return False

    @property
    def order_send_owner(self) -> str:
        return "DemoMT5ExecutionAdapter"

    @staticmethod
    def _verify_shadow_intent_binding(shadow: ShadowExecutionIntent, intent: OrderIntent) -> None:
        if shadow.intent_hash != intent.intent_hash or shadow.client_tag != intent.client_tag:
            raise PermissionError("M186 shadow identity does not match BrokerPreflight OrderIntent")
        comparisons = (
            (shadow.strategy_fingerprint, _sha(intent.strategy_hash, "OrderIntent strategy"), "strategy"),
            (shadow.session_fingerprint, _sha(intent.session_fingerprint, "OrderIntent session"), "session"),
            (shadow.symbol, intent.symbol.strip().upper(), "symbol"),
            (shadow.side, intent.side, "side"),
            (shadow.order_style, intent.order_style, "order_style"),
            (shadow.volume, intent.volume, "volume"),
            (shadow.reference_price, intent.reference_price, "reference_price"),
            (shadow.stop_price, intent.stop_price, "stop_price"),
            (shadow.target_price, intent.target_price, "target_price"),
            (shadow.stop_limit_price, intent.stop_limit_price, "stop_limit_price"),
            (shadow.approved_risk_fraction, intent.approved_risk_fraction, "approved_risk_fraction"),
            (shadow.allowed_loss, intent.allowed_loss, "allowed_loss"),
            (shadow.max_price_drift_fraction, intent.max_price_drift_fraction, "max_price_drift_fraction"),
            (shadow.intent_expires_at, _aware(intent.expires_at, "OrderIntent expiry"), "intent_expiry"),
            (
                shadow.pending_expires_at,
                None if intent.pending_expiry is None else _aware(intent.pending_expiry, "OrderIntent pending expiry"),
                "pending_expiry",
            ),
        )
        for observed, expected, label in comparisons:
            if observed != expected:
                raise PermissionError(f"M186 shadow/{label} binding drift")

    def _verify_shadow_artifact(
        self,
        shadow: ShadowExecutionIntent,
        record: ResearchArtifactRecord,
    ) -> None:
        if record.content_type != SHADOW_INTENT_CONTENT_TYPE:
            raise ValueError("M187 requires an M186 shadow-intent artifact")
        if record.subject_fingerprint != shadow.intent_hash:
            raise ValueError("M186 artifact subject does not match shadow OrderIntent")
        required_sources = {
            shadow.champion_fingerprint,
            shadow.champion_deployment_fingerprint,
            shadow.cognition_fingerprint,
            shadow.capture_quote.fingerprint,
            shadow.capture_quote.source_fingerprint,
            shadow.capture_policy_fingerprint,
        }
        if not required_sources.issubset(set(record.source_fingerprints)):
            raise ValueError("M186 artifact provenance is incomplete")
        expected = _canonical(shadow.payload).encode("utf-8")
        actual = self._vault.read_bytes(record.record_fingerprint)
        if actual != expected:
            raise ValueError("M186 artifact bytes do not match supplied shadow intent")
        if record.created_at != shadow.captured_at:
            raise ValueError("M186 artifact timestamp does not match shadow capture")

    def _persist_admission(
        self,
        admission: DemoBridgeAdmission,
        permit: DemoBridgePermit,
    ) -> ResearchArtifactRecord:
        payload = {
            "protocol": "dusty-m187-demo-admission-envelope-v1",
            "admission": admission.payload,
            "admission_fingerprint": admission.fingerprint,
            "permit": permit.payload,
            "permit_fingerprint": permit.fingerprint,
        }
        return self._vault.store_bytes(
            _canonical(payload).encode("utf-8"),
            kind=ArtifactKind.OTHER,
            content_type=DEMO_BRIDGE_ADMISSION_CONTENT_TYPE,
            producer_fingerprint=self._producer,
            subject_fingerprint=admission.intent_hash,
            source_fingerprints=tuple(
                sorted(
                    {
                        admission.champion_fingerprint,
                        admission.shadow_fingerprint,
                        admission.shadow_artifact_record_fingerprint,
                        admission.session_fingerprint,
                        permit.fingerprint,
                        permit.issuer_fingerprint,
                        *permit.authorization_evidence_fingerprints,
                    }
                )
            ),
            now=admission.admitted_at,
        )

    def admit(
        self,
        *,
        champion: FrozenChampionRecord,
        shadow: ShadowExecutionIntent,
        shadow_artifact: ResearchArtifactRecord,
        preflight: BrokerPreflight,
        permit: DemoBridgePermit,
        at: datetime,
    ) -> DemoBridgeAdmission:
        observed = _aware(at, "M187 admission time")
        if not permit.active_at(observed):
            raise PermissionError("DEMO bridge permit is not active at execution time")
        if self._registry.state(champion.fingerprint) is not ChampionLifecycleState.ACTIVE:
            raise PermissionError("M187 requires ACTIVE Frozen Champion")
        active = self._registry.active_for_lane(champion.lane_id)
        if active is None or active.fingerprint != champion.fingerprint:
            raise PermissionError("M187 Champion is not unique active Champion for its lane")
        if permit.champion_fingerprint != champion.fingerprint or permit.lane_id != champion.lane_id:
            raise PermissionError("DEMO permit Champion/lane identity drift")
        if permit.session_fingerprint != self._session.identity.fingerprint:
            raise PermissionError("DEMO permit session identity drift")
        if not self._session.broker_write_authorized:
            raise PermissionError("latched DemoSession is not write-authorized")
        if shadow.champion_fingerprint != champion.fingerprint:
            raise PermissionError("M186 shadow does not belong to active Champion")
        if shadow.champion_deployment_fingerprint != champion.deployment_fingerprint:
            raise PermissionError("M186 shadow deployment identity drift")
        if shadow.strategy_fingerprint != champion.strategy_fingerprint:
            raise PermissionError("M186 shadow strategy identity drift")
        self._verify_shadow_intent_binding(shadow, preflight.intent)
        if preflight.intent.session_fingerprint != self._session.identity.fingerprint:
            raise PermissionError("BrokerPreflight OrderIntent session drift")
        if preflight.intent.strategy_hash != champion.strategy_fingerprint:
            raise PermissionError("BrokerPreflight strategy does not match active Champion")
        if not preflight.passed:
            raise PermissionError("broker preflight did not pass")
        if observed > preflight.intent.expires_at:
            raise PermissionError("OrderIntent expired before M187 admission")
        self._verify_shadow_artifact(shadow, shadow_artifact)
        if shadow_artifact.created_at > observed:
            raise PermissionError("M186 shadow evidence was recorded after execution admission")
        return DemoBridgeAdmission(
            permit.fingerprint,
            champion.fingerprint,
            shadow.fingerprint,
            shadow_artifact.record_fingerprint,
            shadow.intent_hash,
            self._session.identity.fingerprint,
            observed,
        )

    def execute(
        self,
        *,
        champion: FrozenChampionRecord,
        shadow: ShadowExecutionIntent,
        shadow_artifact: ResearchArtifactRecord,
        preflight: BrokerPreflight,
        permit: DemoBridgePermit,
        at: datetime,
    ) -> DemoBridgeExecutionReceipt:
        admission = self.admit(
            champion=champion,
            shadow=shadow,
            shadow_artifact=shadow_artifact,
            preflight=preflight,
            permit=permit,
            at=at,
        )
        admission_record = self._persist_admission(admission, permit)
        result = self._adapter.send(preflight, at=admission.admitted_at)
        return DemoBridgeExecutionReceipt(admission, admission_record.record_fingerprint, result)
