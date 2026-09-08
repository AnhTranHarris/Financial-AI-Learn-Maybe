from __future__ import annotations

"""M187 pre-Champion calibration admission for genuine M165 broker evidence.

This is a deliberately narrow sub-surface of the M187 Demo execution boundary.
It never imports MetaTrader5 and never calls order_send.  Every send requires a
short-lived one-shot permit bound to one exact preflight intent, qualification
manifest, Strategy Estate plan, DemoSession and native symbol specification.
The existing DemoMT5ExecutionAdapter remains the sole order_send owner.

The surface exists only to break the otherwise circular dependency where M165
requires observed executions, while the normal M187 Champion route requires an
M185 Champion whose qualification itself requires M165.  It has no Champion,
promotion, strategy-mutation, live-write, retry, sizing-override or guardian-
override authority.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math

from .artifact_vault import ArtifactKind, ResearchArtifactRecord, ResearchArtifactVault
from .demo_execution import DemoExecutionResult, DemoMT5ExecutionAdapter
from .demo_session import AccountMode, DemoSession
from .order_intent import BrokerPreflight
from .position_actions import PositionActionKind, PositionActionPreflight
from .strategy_v3 import OrderStyle


M187_CALIBRATION_ADMISSION_CONTENT_TYPE = "application/vnd.dusty.m187-calibration-admission+json"


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


def _finite_positive(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or rendered <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return rendered


@dataclass(frozen=True, slots=True)
class M187CalibrationPermit:
    qualification_plan_fingerprint: str
    qualification_manifest_fingerprint: str
    strategy_hash: str
    symbol_spec_fingerprint: str
    session_fingerprint: str
    symbol: str
    intent_hash: str
    action_kind: str
    volume_lots: float
    maximum_allowed_loss: float
    sequence_number: int
    valid_from: datetime
    valid_until: datetime
    purpose: str = "m165_broker_calibration"

    def __post_init__(self) -> None:
        for field, label in (
            ("qualification_plan_fingerprint", "qualification plan"),
            ("qualification_manifest_fingerprint", "qualification manifest"),
            ("strategy_hash", "qualification strategy"),
            ("symbol_spec_fingerprint", "symbol specification"),
            ("session_fingerprint", "Demo session"),
            ("intent_hash", "calibration intent"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        symbol = str(self.symbol).strip().upper()
        if not symbol or len(symbol) > 64 or "\n" in symbol or "\r" in symbol:
            raise ValueError("calibration permit symbol is invalid")
        object.__setattr__(self, "symbol", symbol)
        if self.action_kind not in {"open", "full_close"}:
            raise ValueError("calibration action must be open or full_close")
        object.__setattr__(self, "volume_lots", _finite_positive(self.volume_lots, "calibration volume"))
        object.__setattr__(
            self,
            "maximum_allowed_loss",
            _finite_positive(self.maximum_allowed_loss, "calibration loss ceiling"),
        )
        if isinstance(self.sequence_number, bool) or not 1 <= int(self.sequence_number) <= 10_000:
            raise ValueError("calibration sequence number out of range")
        start = _aware(self.valid_from, "calibration valid_from")
        end = _aware(self.valid_until, "calibration valid_until")
        if end <= start or end - start > timedelta(minutes=15):
            raise ValueError("calibration permit lifetime must be >0 and <=15 minutes")
        object.__setattr__(self, "valid_from", start)
        object.__setattr__(self, "valid_until", end)
        if self.purpose != "m165_broker_calibration":
            raise ValueError("calibration permit purpose is fixed")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m187-m165-calibration-permit-v1",
            "qualification_plan_fingerprint": self.qualification_plan_fingerprint,
            "qualification_manifest_fingerprint": self.qualification_manifest_fingerprint,
            "strategy_hash": self.strategy_hash,
            "symbol_spec_fingerprint": self.symbol_spec_fingerprint,
            "session_fingerprint": self.session_fingerprint,
            "symbol": self.symbol,
            "intent_hash": self.intent_hash,
            "action_kind": self.action_kind,
            "volume_lots": self.volume_lots,
            "maximum_allowed_loss": self.maximum_allowed_loss,
            "sequence_number": int(self.sequence_number),
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat(),
            "purpose": self.purpose,
            "authority": {
                "demo_write": True,
                "live_write": False,
                "promotion": False,
                "strategy_mutation": False,
                "risk_override": False,
                "guardian_override": False,
                "retry": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    def active_at(self, at: datetime) -> bool:
        observed = _aware(at, "calibration permit evaluation")
        return self.valid_from <= observed <= self.valid_until

    demo_write_authority = True
    live_write_authority = False
    promotion_authority = False
    strategy_mutation_authority = False
    risk_override_authority = False
    guardian_override_authority = False
    retry_authority = False


@dataclass(frozen=True, slots=True)
class M187CalibrationAdmission:
    permit_fingerprint: str
    intent_hash: str
    session_fingerprint: str
    qualification_plan_fingerprint: str
    qualification_manifest_fingerprint: str
    symbol_spec_fingerprint: str
    action_kind: str
    sequence_number: int
    admitted_at: datetime

    def __post_init__(self) -> None:
        for field, label in (
            ("permit_fingerprint", "calibration admission permit"),
            ("intent_hash", "calibration admission intent"),
            ("session_fingerprint", "calibration admission session"),
            ("qualification_plan_fingerprint", "calibration admission plan"),
            ("qualification_manifest_fingerprint", "calibration admission manifest"),
            ("symbol_spec_fingerprint", "calibration admission symbol specification"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        if self.action_kind not in {"open", "full_close"}:
            raise ValueError("calibration admission action invalid")
        if isinstance(self.sequence_number, bool) or self.sequence_number < 1:
            raise ValueError("calibration admission sequence invalid")
        object.__setattr__(self, "admitted_at", _aware(self.admitted_at, "calibration admission time"))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m187-m165-calibration-admission-v1",
            "permit_fingerprint": self.permit_fingerprint,
            "intent_hash": self.intent_hash,
            "session_fingerprint": self.session_fingerprint,
            "qualification_plan_fingerprint": self.qualification_plan_fingerprint,
            "qualification_manifest_fingerprint": self.qualification_manifest_fingerprint,
            "symbol_spec_fingerprint": self.symbol_spec_fingerprint,
            "action_kind": self.action_kind,
            "sequence_number": self.sequence_number,
            "admitted_at": self.admitted_at.isoformat(),
            "authority": {"live_write": False, "promotion": False, "retry": False},
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    live_write_authority = False
    promotion_authority = False
    retry_authority = False


@dataclass(frozen=True, slots=True)
class M187CalibrationExecutionReceipt:
    admission: M187CalibrationAdmission
    admission_artifact_record_fingerprint: str
    execution: DemoExecutionResult

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "admission_artifact_record_fingerprint",
            _sha(self.admission_artifact_record_fingerprint, "calibration admission artifact"),
        )
        if self.execution.intent_hash != self.admission.intent_hash:
            raise ValueError("calibration execution intent does not match admission")

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m187-m165-calibration-receipt-v1",
                self.admission.fingerprint,
                self.admission_artifact_record_fingerprint,
                self.execution.intent_hash,
                self.execution.state.value,
                int(self.execution.retcode),
                int(self.execution.order_ticket),
                int(self.execution.deal_ticket),
            )
        )

    live_write_authority = False
    promotion_authority = False
    retry_authority = False


class M187CalibrationExecutionBridge:
    """One-shot deterministic M165 calibration admission inside M187."""

    def __init__(
        self,
        *,
        vault: ResearchArtifactVault,
        session: DemoSession,
        adapter: DemoMT5ExecutionAdapter,
        producer_fingerprint: str,
    ) -> None:
        if session.identity.account_mode is not AccountMode.DEMO:
            raise ValueError("calibration bridge accepts DEMO sessions only")
        self._vault = vault
        self._session = session
        self._adapter = adapter
        self._producer = _sha(producer_fingerprint, "calibration bridge producer")

    @property
    def demo_write_authorized(self) -> bool:
        return False

    @property
    def live_write_authorized(self) -> bool:
        return False

    @property
    def order_send_owner(self) -> str:
        return "DemoMT5ExecutionAdapter"

    def admit(
        self,
        *,
        preflight: BrokerPreflight | PositionActionPreflight,
        permit: M187CalibrationPermit,
        current_symbol_spec_fingerprint: str,
        at: datetime,
    ) -> M187CalibrationAdmission:
        observed = _aware(at, "calibration admission time")
        if not permit.active_at(observed):
            raise PermissionError("calibration permit is not active")
        if permit.session_fingerprint != self._session.identity.fingerprint:
            raise PermissionError("calibration permit session drift")
        if not self._session.broker_write_authorized:
            raise PermissionError("latched DemoSession is not write-authorized")
        if _sha(current_symbol_spec_fingerprint, "current symbol specification") != permit.symbol_spec_fingerprint:
            raise PermissionError("calibration native symbol specification drift")
        intent = preflight.intent
        if intent.intent_hash != permit.intent_hash:
            raise PermissionError("calibration permit is not bound to this intent")
        if intent.session_fingerprint != self._session.identity.fingerprint:
            raise PermissionError("calibration intent session drift")
        if _sha(intent.strategy_hash, "calibration intent strategy") != permit.strategy_hash:
            raise PermissionError("calibration intent strategy drift")
        if intent.symbol.strip().upper() != permit.symbol:
            raise PermissionError("calibration intent symbol drift")
        if not preflight.passed:
            raise PermissionError("calibration broker preflight did not pass")

        if permit.action_kind == "open":
            if not isinstance(preflight, BrokerPreflight):
                raise PermissionError("open calibration permit requires entry preflight")
            if intent.order_style is not OrderStyle.MARKET:
                raise PermissionError("calibration entries must be market orders")
            if not math.isclose(intent.volume, permit.volume_lots, rel_tol=1e-12, abs_tol=1e-12):
                raise PermissionError("calibration entry volume drift")
            if intent.allowed_loss > permit.maximum_allowed_loss + 1e-9:
                raise PermissionError("calibration entry exceeds permit loss ceiling")
            if intent.target_price is not None:
                raise PermissionError("calibration entry cannot carry a profit target")
        else:
            if not isinstance(preflight, PositionActionPreflight):
                raise PermissionError("full-close calibration permit requires position-action preflight")
            if intent.kind is not PositionActionKind.FULL_CLOSE:
                raise PermissionError("calibration exit must fully close the position")
            if not math.isclose(intent.action_volume, permit.volume_lots, rel_tol=1e-12, abs_tol=1e-12):
                raise PermissionError("calibration close volume drift")
            if not math.isclose(intent.current_volume, permit.volume_lots, rel_tol=1e-12, abs_tol=1e-12):
                raise PermissionError("calibration position volume drift")

        return M187CalibrationAdmission(
            permit.fingerprint,
            permit.intent_hash,
            permit.session_fingerprint,
            permit.qualification_plan_fingerprint,
            permit.qualification_manifest_fingerprint,
            permit.symbol_spec_fingerprint,
            permit.action_kind,
            permit.sequence_number,
            observed,
        )

    def _persist(
        self,
        admission: M187CalibrationAdmission,
        permit: M187CalibrationPermit,
    ) -> ResearchArtifactRecord:
        payload = {
            "protocol": "dusty-m187-m165-calibration-envelope-v1",
            "admission": admission.payload,
            "admission_fingerprint": admission.fingerprint,
            "permit": permit.payload,
            "permit_fingerprint": permit.fingerprint,
        }
        return self._vault.store_bytes(
            _canonical(payload).encode("utf-8"),
            kind=ArtifactKind.OTHER,
            content_type=M187_CALIBRATION_ADMISSION_CONTENT_TYPE,
            producer_fingerprint=self._producer,
            subject_fingerprint=admission.intent_hash,
            source_fingerprints=tuple(
                sorted(
                    {
                        permit.fingerprint,
                        permit.qualification_plan_fingerprint,
                        permit.qualification_manifest_fingerprint,
                        permit.strategy_hash,
                        permit.symbol_spec_fingerprint,
                        permit.session_fingerprint,
                    }
                )
            ),
            now=admission.admitted_at,
        )

    def execute(
        self,
        *,
        preflight: BrokerPreflight | PositionActionPreflight,
        permit: M187CalibrationPermit,
        current_symbol_spec_fingerprint: str,
        at: datetime,
    ) -> M187CalibrationExecutionReceipt:
        admission = self.admit(
            preflight=preflight,
            permit=permit,
            current_symbol_spec_fingerprint=current_symbol_spec_fingerprint,
            at=at,
        )
        record = self._persist(admission, permit)
        result = self._adapter.send(preflight, at=admission.admitted_at)
        return M187CalibrationExecutionReceipt(admission, record.record_fingerprint, result)
