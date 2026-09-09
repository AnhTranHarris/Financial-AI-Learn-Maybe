from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from dusty.m190_production_recovery import plan_production_execution_recovery

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "dusty" / "m190_production_recovery.py"


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M190ProductionRecoveryTests(unittest.TestCase):
    def test_recovery_binds_checkpoint_custody_and_m188(self):
        intent = fp("intent")
        custody = SimpleNamespace(fingerprint=fp("custody"))
        checkpoint = SimpleNamespace(fingerprint=fp("checkpoint"), execution_intent_hashes=(intent,))
        record = SimpleNamespace(intent_hash=intent)
        reconciliation = SimpleNamespace(fingerprint=fp("reconciliation"), intent_hash=intent)
        production_reconciliation = SimpleNamespace(
            fingerprint=fp("m188-production"),
            production_custody_fingerprint=custody.fingerprint,
            reconciliation_fingerprint=reconciliation.fingerprint,
        )
        plan = SimpleNamespace(fingerprint=fp("plan"), intent_hash=intent)
        with patch("dusty.m190_production_recovery.plan_execution_recovery", return_value=plan) as core:
            envelope, observed = plan_production_execution_recovery(
                custody=custody,
                checkpoint=checkpoint,
                record=record,
                admission_artifact_fingerprint=fp("admission-artifact"),
                production_reconciliation=production_reconciliation,
                reconciliation=reconciliation,
            )
        self.assertIs(observed, plan)
        self.assertEqual(envelope.production_custody_fingerprint, custody.fingerprint)
        self.assertEqual(envelope.checkpoint_fingerprint, checkpoint.fingerprint)
        self.assertEqual(envelope.m188_production_reconciliation_fingerprint, production_reconciliation.fingerprint)
        self.assertFalse(envelope.resend_authority)
        self.assertFalse(envelope.retry_authority)
        core.assert_called_once()

    def test_missing_checkpoint_intent_or_wrong_custody_blocks(self):
        intent = fp("intent")
        custody = SimpleNamespace(fingerprint=fp("custody"))
        record = SimpleNamespace(intent_hash=intent)
        with self.assertRaisesRegex(PermissionError, "not present"):
            plan_production_execution_recovery(
                custody=custody,
                checkpoint=SimpleNamespace(fingerprint=fp("checkpoint"), execution_intent_hashes=()),
                record=record,
            )
        reconciliation = SimpleNamespace(fingerprint=fp("reconciliation"), intent_hash=intent)
        production_reconciliation = SimpleNamespace(
            fingerprint=fp("m188-production"),
            production_custody_fingerprint=fp("wrong-custody"),
            reconciliation_fingerprint=reconciliation.fingerprint,
        )
        with self.assertRaisesRegex(PermissionError, "different production custody"):
            plan_production_execution_recovery(
                custody=custody,
                checkpoint=SimpleNamespace(fingerprint=fp("checkpoint"), execution_intent_hashes=(intent,)),
                record=record,
                production_reconciliation=production_reconciliation,
                reconciliation=reconciliation,
            )

    def test_module_has_no_direct_broker_send_surface(self):
        self.assertNotIn("order_send(", MODULE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
