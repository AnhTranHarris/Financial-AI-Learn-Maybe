from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from dusty.m194_production_certification import (
    certify_production_single_demo_desk,
    production_recovery_lineage_fingerprint,
    production_runtime_attestation_fingerprint,
)
from dusty.strategy_drift import StrategyDriftStatus

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "dusty" / "m194_production_certification.py"


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
        certification = SimpleNamespace(certification_fingerprint=fp("certification"))
        return custody, runtime_admission, learning, recovery, provider, drift, suspension, runtime, certification

    def test_final_wrapper_binds_entire_production_runtime_lineage(self):
        custody, runtime_admission, learning, recovery, provider, drift, suspension, runtime, certification = self.fixtures()
        with patch("dusty.m194_production_certification.certify_single_demo_desk", return_value=certification) as core:
            envelope, observed = certify_production_single_demo_desk(
                custody=custody, runtime_admission=runtime_admission,
                prerequisites=(), runtime=runtime, exercises=(), policy=SimpleNamespace(),
                current_source_commit="1" * 40, execution_learning=learning,
                recovery_envelopes=recovery, provider_fleet=provider, drift=drift, suspension=suspension,
            )
        self.assertIs(observed, certification)
        self.assertEqual(envelope.production_custody_fingerprint, custody.fingerprint)
        self.assertEqual(envelope.runtime_evidence_fingerprint, runtime.fingerprint)
        self.assertEqual(envelope.certification_fingerprint, certification.certification_fingerprint)
        self.assertFalse(envelope.broker_write_authority)
        self.assertFalse(envelope.live_write_authority)
        core.assert_called_once()

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
