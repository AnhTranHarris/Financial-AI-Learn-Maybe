from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

from dusty.artifact_vault import ArtifactKind
from dusty.m194_production_certification import (
    M194ProductionCertificationEnvelope,
    PRODUCTION_CERTIFICATION_CONTENT_TYPE,
    certification_source_fingerprints,
    certify_production_single_demo_desk,
    persist_production_certification,
    production_recovery_lineage_fingerprint,
    production_runtime_attestation_fingerprint,
)
from dusty.single_desk_demo_certification import SingleDeskDemoStatus
from dusty.strategy_drift import StrategyDriftStatus

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "dusty" / "m194_production_certification.py"
UTC = timezone.utc


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M194ProductionCertificationTests(unittest.TestCase):
    def fixtures(self):
        champion = SimpleNamespace(fingerprint=fp("champion"), lane_id="eurusd:m15:unit")
        custody = SimpleNamespace(fingerprint=fp("custody"), champion_record=champion)
        runtime_admission = SimpleNamespace(
            fingerprint=fp("runtime-admission"), production_custody_fingerprint=custody.fingerprint,
            champion_fingerprint=champion.fingerprint, lane_id=champion.lane_id, source_commit="1" * 40,
        )
        learning = SimpleNamespace(
            fingerprint=fp("m189-envelope"), production_custody_fingerprint=custody.fingerprint,
            learning_fingerprint=fp("m189-learning"),
        )
        recovery = tuple(
            SimpleNamespace(fingerprint=fp(f"m190-{i}"), production_custody_fingerprint=custody.fingerprint)
            for i in range(3)
        )
        recovery_lineage = production_recovery_lineage_fingerprint(recovery)
        provider = SimpleNamespace(fingerprint=fp("m191"))
        drift = SimpleNamespace(
            fingerprint=fp("m192"), champion_fingerprint=champion.fingerprint,
            status=StrategyDriftStatus.STABLE,
        )
        suspension = SimpleNamespace(
            fingerprint=fp("m193"), champion_fingerprint=champion.fingerprint,
            drift_fingerprint=drift.fingerprint,
        )
        attestation = production_runtime_attestation_fingerprint(
            custody_fingerprint=custody.fingerprint,
            runtime_admission_fingerprint=runtime_admission.fingerprint,
            execution_learning_fingerprint=learning.fingerprint,
            recovery_lineage_fingerprint=recovery_lineage,
            provider_fleet_fingerprint=provider.fingerprint,
            drift_fingerprint=drift.fingerprint,
            suspension_fingerprint=suspension.fingerprint,
        )
        runtime = SimpleNamespace(
            fingerprint=fp("runtime"), champion_fingerprint=champion.fingerprint,
            lane_id=champion.lane_id, source_commit=runtime_admission.source_commit,
            execution_learning_fingerprint=learning.learning_fingerprint,
            recovery_checkpoint_count=3, recovery_fingerprint=recovery_lineage,
            provider_fleet_fingerprint=provider.fingerprint,
            drift_fingerprint=drift.fingerprint, latest_drift_status=drift.status,
            suspension_fingerprint=suspension.fingerprint,
            runtime_attestation_fingerprint=attestation,
        )
        certification = SimpleNamespace(
            certification_fingerprint=fp("certification"),
            status=SingleDeskDemoStatus.CERTIFIED,
            pending_reasons=(),
            rejection_reasons=(),
        )
        return custody, runtime_admission, learning, recovery, provider, drift, suspension, runtime, certification

    def call_wrapper(self, *, certification=None):
        custody, runtime_admission, learning, recovery, provider, drift, suspension, runtime, default = self.fixtures()
        result = default if certification is None else certification
        with patch("dusty.m194_production_certification.certify_single_demo_desk", return_value=result) as core:
            envelope, observed = certify_production_single_demo_desk(
                custody=custody, runtime_admission=runtime_admission,
                prerequisites=(), runtime=runtime, exercises=(), policy=SimpleNamespace(),
                current_source_commit="1" * 40, execution_learning=learning,
                recovery_envelopes=recovery, provider_fleet=provider, drift=drift, suspension=suspension,
            )
        return envelope, observed, core

    def test_final_wrapper_binds_entire_production_runtime_lineage(self):
        envelope, certification, core = self.call_wrapper()
        custody = self.fixtures()[0]
        runtime = self.fixtures()[7]
        self.assertEqual(envelope.production_custody_fingerprint, custody.fingerprint)
        self.assertEqual(envelope.runtime_evidence_fingerprint, runtime.fingerprint)
        self.assertEqual(envelope.certification_fingerprint, certification.certification_fingerprint)
        self.assertEqual(envelope.certification_status, SingleDeskDemoStatus.CERTIFIED)
        self.assertTrue(envelope.certified)
        self.assertTrue(envelope.production_activation_eligible)
        self.assertFalse(envelope.broker_write_authority)
        self.assertFalse(envelope.live_write_authority)
        core.assert_called_once()

    def test_pending_core_result_cannot_look_production_eligible(self):
        certification = SimpleNamespace(
            certification_fingerprint=fp("pending-certification"),
            status=SingleDeskDemoStatus.PENDING,
            pending_reasons=("runtime_duration_insufficient",),
            rejection_reasons=(),
        )
        envelope, observed, _ = self.call_wrapper(certification=certification)
        self.assertIs(observed, certification)
        self.assertEqual(envelope.certification_status, SingleDeskDemoStatus.PENDING)
        self.assertFalse(envelope.certified)
        self.assertFalse(envelope.production_activation_eligible)
        self.assertEqual(envelope.pending_reasons, ("runtime_duration_insufficient",))
        self.assertEqual(envelope.payload["production_activation_eligible"], False)

    def test_rejected_core_result_cannot_look_production_eligible(self):
        certification = SimpleNamespace(
            certification_fingerprint=fp("rejected-certification"),
            status=SingleDeskDemoStatus.REJECTED,
            pending_reasons=(),
            rejection_reasons=("unauthorized_broker_write_detected",),
        )
        envelope, observed, _ = self.call_wrapper(certification=certification)
        self.assertIs(observed, certification)
        self.assertEqual(envelope.certification_status, SingleDeskDemoStatus.REJECTED)
        self.assertFalse(envelope.certified)
        self.assertFalse(envelope.production_activation_eligible)
        self.assertEqual(envelope.rejection_reasons, ("unauthorized_broker_write_detected",))

    def test_envelope_status_reason_invariants_fail_closed(self):
        kwargs = dict(
            production_custody_fingerprint=fp("custody"),
            runtime_admission_fingerprint=fp("admission"),
            runtime_evidence_fingerprint=fp("runtime"),
            execution_learning_envelope_fingerprint=fp("learning"),
            recovery_lineage_fingerprint=fp("recovery"),
            provider_fleet_fingerprint=fp("provider"),
            drift_fingerprint=fp("drift"),
            suspension_fingerprint=fp("suspension"),
            certification_fingerprint=fp("certification"),
        )
        with self.assertRaisesRegex(ValueError, "PENDING.*requires pending"):
            M194ProductionCertificationEnvelope(
                **kwargs,
                certification_status=SingleDeskDemoStatus.PENDING,
                pending_reasons=(), rejection_reasons=(),
            )
        with self.assertRaisesRegex(ValueError, "CERTIFIED.*cannot carry"):
            M194ProductionCertificationEnvelope(
                **kwargs,
                certification_status=SingleDeskDemoStatus.CERTIFIED,
                pending_reasons=("should-not-exist",), rejection_reasons=(),
            )

    def test_production_envelope_persists_through_m164_vault_contract(self):
        envelope, _, _ = self.call_wrapper()
        record = SimpleNamespace(record_fingerprint=fp("artifact-record"))
        vault = Mock()
        vault.store_bytes.return_value = record
        now = datetime(2026, 9, 9, 3, 10, tzinfo=UTC)
        observed = persist_production_certification(
            vault,
            envelope,
            producer_fingerprint=fp("producer"),
            now=now,
        )
        self.assertIs(observed, record)
        kwargs = vault.store_bytes.call_args.kwargs
        self.assertEqual(kwargs["kind"], ArtifactKind.EVALUATION)
        self.assertEqual(kwargs["content_type"], PRODUCTION_CERTIFICATION_CONTENT_TYPE)
        self.assertEqual(kwargs["subject_fingerprint"], envelope.certification_fingerprint)
        self.assertEqual(kwargs["producer_fingerprint"], fp("producer"))
        self.assertEqual(kwargs["source_fingerprints"], certification_source_fingerprints(envelope))
        self.assertEqual(kwargs["now"], now)
        self.assertIn(b'"protocol":"dusty-m194-production-certification-v2"', vault.store_bytes.call_args.args[0])

    def test_wrong_custody_or_incomplete_recovery_lineage_blocks(self):
        custody, runtime_admission, learning, recovery, provider, drift, suspension, runtime, certification = self.fixtures()
        bad_learning = SimpleNamespace(
            fingerprint=learning.fingerprint,
            production_custody_fingerprint=fp("wrong-custody"),
            learning_fingerprint=learning.learning_fingerprint,
        )
        with self.assertRaisesRegex(PermissionError, "M189 learning belongs"):
            certify_production_single_demo_desk(
                custody=custody, runtime_admission=runtime_admission,
                prerequisites=(), runtime=runtime, exercises=(), policy=SimpleNamespace(),
                current_source_commit="1" * 40, execution_learning=bad_learning,
                recovery_envelopes=recovery, provider_fleet=provider, drift=drift, suspension=suspension,
            )
        with self.assertRaisesRegex(PermissionError, "do not cover"):
            certify_production_single_demo_desk(
                custody=custody, runtime_admission=runtime_admission,
                prerequisites=(), runtime=runtime, exercises=(), policy=SimpleNamespace(),
                current_source_commit="1" * 40, execution_learning=learning,
                recovery_envelopes=recovery[:2], provider_fleet=provider, drift=drift, suspension=suspension,
            )

    def test_attestation_drift_blocks_before_core_certification(self):
        custody, runtime_admission, learning, recovery, provider, drift, suspension, runtime, certification = self.fixtures()
        runtime.runtime_attestation_fingerprint = fp("wrong-attestation")
        with patch("dusty.m194_production_certification.certify_single_demo_desk", return_value=certification) as core:
            with self.assertRaisesRegex(PermissionError, "attestation"):
                certify_production_single_demo_desk(
                    custody=custody, runtime_admission=runtime_admission,
                    prerequisites=(), runtime=runtime, exercises=(), policy=SimpleNamespace(),
                    current_source_commit="1" * 40, execution_learning=learning,
                    recovery_envelopes=recovery, provider_fleet=provider, drift=drift, suspension=suspension,
                )
        core.assert_not_called()

    def test_module_has_no_direct_broker_send_surface(self):
        self.assertNotIn("order_send(", MODULE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
