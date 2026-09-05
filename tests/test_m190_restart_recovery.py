from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import unittest

from dusty.execution_lifecycle import ExecutionRecord, ExecutionState
from dusty.execution_reconciliation import ExecutionReconciliation, ReconciliationStatus
from dusty.restart_recovery import (
    RecoveryCheckpoint,
    RecoveryDisposition,
    plan_execution_recovery,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 6, 2, 0, tzinfo=UTC)
INTENT = sha256(b"intent").hexdigest()
SESSION = sha256(b"session").hexdigest()
COMMIT = sha256(b"commit").hexdigest()


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def record(state: ExecutionState) -> ExecutionRecord:
    return ExecutionRecord(INTENT, "DD-ABC", state, 700, 800, 900, NOW, "fixture")


def reconciliation(status: ReconciliationStatus) -> ExecutionReconciliation:
    fill = status in {ReconciliationStatus.PARTIAL, ReconciliationStatus.FILLED}
    fraction = 0.5 if status is ReconciliationStatus.PARTIAL else (1.0 if fill else 0.0)
    return ExecutionReconciliation(
        status,
        INTENT,
        fp("shadow"),
        fp("m187-receipt"),
        fp("broker"),
        NOW,
        1.1002,
        0.10 * fraction,
        fraction,
        1.1003 if fill else None,
        0.0001 if fill else None,
        0.0001 / 1.1002 if fill else None,
        20.0 if fill else None,
        30.0 if fill else None,
        -0.35 if fill else 0.0,
        0.0,
        0.0,
        (700,),
        (800,) if fill else (),
        (900,) if fill else (),
        ("fixture",),
        (fp("evidence"),),
    )


class M190RestartRecoveryTests(unittest.TestCase):
    def test_checkpoint_requires_exact_software_and_session_identity(self) -> None:
        checkpoint = RecoveryCheckpoint(COMMIT, SESSION, (INTENT,), NOW)
        checkpoint.validate_runtime(source_commit=COMMIT, session_fingerprint=SESSION)
        with self.assertRaisesRegex(ValueError, "software identity drift"):
            checkpoint.validate_runtime(source_commit=fp("other"), session_fingerprint=SESSION)
        with self.assertRaisesRegex(ValueError, "session identity drift"):
            checkpoint.validate_runtime(source_commit=COMMIT, session_fingerprint=fp("other-session"))

    def test_authorized_pre_send_is_abandoned_not_replayed(self) -> None:
        plan = plan_execution_recovery(record(ExecutionState.AUTHORIZED))
        self.assertEqual(plan.disposition, RecoveryDisposition.ABANDON_PRE_SEND)
        self.assertFalse(plan.resend_authority)
        self.assertIn("fresh_intent", plan.reasons[1])

    def test_sent_unknown_accepted_or_partial_requires_fresh_reconciliation(self) -> None:
        for state in (ExecutionState.SENT_UNKNOWN, ExecutionState.ACCEPTED, ExecutionState.PARTIAL):
            with self.subTest(state=state):
                plan = plan_execution_recovery(record(state), admission_artifact_fingerprint=fp("admission"))
                self.assertEqual(plan.disposition, RecoveryDisposition.RECONCILE_REQUIRED)
                self.assertFalse(plan.resend_authority)

    def test_even_filled_or_protected_ledger_requires_fresh_broker_state_after_restart(self) -> None:
        for state in (ExecutionState.FILLED, ExecutionState.PROTECTED, ExecutionState.CLOSING):
            plan = plan_execution_recovery(record(state))
            self.assertEqual(plan.disposition, RecoveryDisposition.RECONCILE_REQUIRED)

    def test_m188_pending_and_fill_evidence_resume_only_supervision(self) -> None:
        pending = plan_execution_recovery(
            record(ExecutionState.ACCEPTED), reconciliation=reconciliation(ReconciliationStatus.PENDING)
        )
        self.assertEqual(pending.disposition, RecoveryDisposition.RESUME_ORDER_SUPERVISION)
        filled = plan_execution_recovery(
            record(ExecutionState.FILLED), reconciliation=reconciliation(ReconciliationStatus.FILLED)
        )
        self.assertEqual(filled.disposition, RecoveryDisposition.RESUME_POSITION_SUPERVISION)
        self.assertFalse(filled.broker_write_authority)
        self.assertFalse(filled.position_mutation_authority)

    def test_incomplete_or_inconsistent_M188_state_never_resumes_or_retries(self) -> None:
        incomplete = plan_execution_recovery(
            record(ExecutionState.SENT_UNKNOWN), reconciliation=reconciliation(ReconciliationStatus.INCOMPLETE)
        )
        self.assertEqual(incomplete.disposition, RecoveryDisposition.RECONCILE_REQUIRED)
        inconsistent = plan_execution_recovery(
            record(ExecutionState.SENT_UNKNOWN), reconciliation=reconciliation(ReconciliationStatus.INCONSISTENT)
        )
        self.assertEqual(inconsistent.disposition, RecoveryDisposition.HALT)
        self.assertFalse(inconsistent.resend_authority)

    def test_terminal_and_fault_ledgers_remain_terminal_or_halted(self) -> None:
        self.assertEqual(
            plan_execution_recovery(record(ExecutionState.CLOSED)).disposition,
            RecoveryDisposition.TERMINAL,
        )
        self.assertEqual(
            plan_execution_recovery(record(ExecutionState.REJECTED)).disposition,
            RecoveryDisposition.TERMINAL,
        )
        self.assertEqual(
            plan_execution_recovery(record(ExecutionState.FAULT)).disposition,
            RecoveryDisposition.HALT,
        )

    def test_recovery_never_gains_execution_or_governance_authority(self) -> None:
        plan = plan_execution_recovery(
            record(ExecutionState.FILLED), reconciliation=reconciliation(ReconciliationStatus.FILLED)
        )
        self.assertFalse(plan.broker_write_authority)
        self.assertFalse(plan.resend_authority)
        self.assertFalse(plan.position_mutation_authority)
        self.assertFalse(plan.risk_override_authority)
        self.assertFalse(plan.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
