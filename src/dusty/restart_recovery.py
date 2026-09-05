from __future__ import annotations

"""M190 deterministic restart/crash recovery.

Recovery is intentionally read/reconcile-first. Process death never restores
broker-write authority and never grants resend permission. Ambiguous execution
must be resolved from broker evidence before any later human/governance action.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json

from .execution_lifecycle import ExecutionRecord, ExecutionState
from .execution_reconciliation import ExecutionReconciliation, ReconciliationStatus


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


@dataclass(frozen=True, slots=True)
class RecoveryCheckpoint:
    source_commit: str
    session_fingerprint: str
    execution_intent_hashes: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commit", _sha(self.source_commit, "recovery source commit"))
        object.__setattr__(self, "session_fingerprint", _sha(self.session_fingerprint, "recovery session"))
        identities = tuple(sorted(_sha(row, "recovery intent") for row in self.execution_intent_hashes))
        if len(identities) != len(set(identities)):
            raise ValueError("recovery checkpoint cannot contain duplicate intents")
        object.__setattr__(self, "execution_intent_hashes", identities)
        object.__setattr__(self, "created_at", _aware(self.created_at, "recovery checkpoint timestamp"))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m190-recovery-checkpoint-v1",
            "source_commit": self.source_commit,
            "session_fingerprint": self.session_fingerprint,
            "execution_intent_hashes": list(self.execution_intent_hashes),
            "created_at": self.created_at.isoformat(),
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    def validate_runtime(self, *, source_commit: str, session_fingerprint: str) -> None:
        if self.source_commit != _sha(source_commit, "runtime source commit"):
            raise ValueError("recovery checkpoint software identity drift")
        if self.session_fingerprint != _sha(session_fingerprint, "runtime session"):
            raise ValueError("recovery checkpoint session identity drift")


class RecoveryDisposition(StrEnum):
    ABANDON_PRE_SEND = "abandon_pre_send"
    RECONCILE_REQUIRED = "reconcile_required"
    RESUME_ORDER_SUPERVISION = "resume_order_supervision"
    RESUME_POSITION_SUPERVISION = "resume_position_supervision"
    TERMINAL = "terminal"
    HALT = "halt"


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    intent_hash: str
    ledger_state: ExecutionState
    disposition: RecoveryDisposition
    reasons: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_hash", _sha(self.intent_hash, "recovery plan intent"))
        if not self.reasons:
            raise ValueError("recovery plan requires reason")
        evidence = tuple(sorted({_sha(row, "recovery evidence") for row in self.evidence_fingerprints}))
        object.__setattr__(self, "evidence_fingerprints", evidence)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m190-recovery-plan-v1",
            self.intent_hash,
            self.ledger_state.value,
            self.disposition.value,
            self.reasons,
            self.evidence_fingerprints,
        ))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def resend_authority(self) -> bool:
        return False

    @property
    def position_mutation_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def _record_fingerprint(record: ExecutionRecord) -> str:
    return _digest((
        "dusty-execution-record-snapshot-v1",
        record.intent_hash,
        record.client_tag,
        record.state.value,
        record.order_ticket,
        record.deal_ticket,
        record.position_ticket,
        record.updated_at.isoformat() if record.updated_at else None,
        record.note,
    ))


def plan_execution_recovery(
    record: ExecutionRecord,
    *,
    admission_artifact_fingerprint: str | None = None,
    reconciliation: ExecutionReconciliation | None = None,
) -> RecoveryPlan:
    """Determine the only safe next operational class after process restart."""

    intent = _sha(record.intent_hash, "execution record intent")
    evidence = {_record_fingerprint(record)}
    if admission_artifact_fingerprint is not None:
        evidence.add(_sha(admission_artifact_fingerprint, "M187 admission artifact"))
    if reconciliation is not None:
        if reconciliation.intent_hash != intent:
            raise ValueError("recovery reconciliation intent drift")
        evidence.add(reconciliation.fingerprint)

    if record.state is ExecutionState.AUTHORIZED:
        return RecoveryPlan(
            intent,
            record.state,
            RecoveryDisposition.ABANDON_PRE_SEND,
            ("authorized_record_has_no_reserved_broker_send", "fresh_intent_and_permit_required"),
            tuple(evidence),
        )

    if reconciliation is not None:
        if reconciliation.status is ReconciliationStatus.INCONSISTENT:
            return RecoveryPlan(
                intent, record.state, RecoveryDisposition.HALT,
                ("M188_broker_state_inconsistent",), tuple(evidence)
            )
        if reconciliation.status is ReconciliationStatus.INCOMPLETE:
            return RecoveryPlan(
                intent, record.state, RecoveryDisposition.RECONCILE_REQUIRED,
                ("M188_broker_state_incomplete", "automatic_resend_prohibited"), tuple(evidence)
            )
        if reconciliation.status is ReconciliationStatus.PENDING:
            return RecoveryPlan(
                intent, record.state, RecoveryDisposition.RESUME_ORDER_SUPERVISION,
                ("M188_active_order_confirmed",), tuple(evidence)
            )
        if reconciliation.status in {ReconciliationStatus.PARTIAL, ReconciliationStatus.FILLED}:
            return RecoveryPlan(
                intent, record.state, RecoveryDisposition.RESUME_POSITION_SUPERVISION,
                ("M188_fill_evidence_confirmed",), tuple(evidence)
            )
        if reconciliation.status is ReconciliationStatus.REJECTED:
            if record.state not in {ExecutionState.REJECTED, ExecutionState.SENT_UNKNOWN, ExecutionState.ACCEPTED}:
                return RecoveryPlan(
                    intent, record.state, RecoveryDisposition.HALT,
                    ("ledger_and_M188_rejection_state_conflict",), tuple(evidence)
                )
            return RecoveryPlan(
                intent, record.state, RecoveryDisposition.TERMINAL,
                ("M188_broker_rejection_confirmed",), tuple(evidence)
            )

    if record.state in {ExecutionState.SENT_UNKNOWN, ExecutionState.ACCEPTED, ExecutionState.PARTIAL}:
        return RecoveryPlan(
            intent,
            record.state,
            RecoveryDisposition.RECONCILE_REQUIRED,
            ("nonterminal_execution_requires_fresh_broker_reconciliation", "automatic_resend_prohibited"),
            tuple(evidence),
        )
    if record.state in {ExecutionState.FILLED, ExecutionState.PROTECTED, ExecutionState.CLOSING}:
        return RecoveryPlan(
            intent,
            record.state,
            RecoveryDisposition.RECONCILE_REQUIRED,
            ("position_lifecycle_requires_fresh_broker_state_before_supervision_resume",),
            tuple(evidence),
        )
    if record.state in {ExecutionState.CLOSED, ExecutionState.REJECTED}:
        return RecoveryPlan(
            intent, record.state, RecoveryDisposition.TERMINAL,
            ("execution_ledger_terminal_state",), tuple(evidence)
        )
    if record.state is ExecutionState.FAULT:
        return RecoveryPlan(
            intent, record.state, RecoveryDisposition.HALT,
            ("execution_ledger_fault_state",), tuple(evidence)
        )
    raise ValueError(f"unsupported execution state: {record.state}")
