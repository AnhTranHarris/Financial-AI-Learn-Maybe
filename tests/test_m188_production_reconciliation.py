from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from dusty.m188_production_reconciliation import reconcile_production_execution

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "dusty" / "m188_production_reconciliation.py"


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M188ProductionReconciliationTests(unittest.TestCase):
    def fixtures(self):
        production = SimpleNamespace(
            production_custody_fingerprint=fp("custody"),
            fingerprint=fp("m187-production"),
            champion_fingerprint=fp("champion"),
            shadow_fingerprint=fp("shadow"),
            intent_hash=fp("intent"),
            permit_fingerprint=fp("permit"),
        )
        shadow = SimpleNamespace(fingerprint=fp("shadow"), intent_hash=fp("intent"))
        receipt = SimpleNamespace(
            fingerprint=fp("receipt"),
            admission=SimpleNamespace(
                champion_fingerprint=fp("champion"),
                shadow_fingerprint=fp("shadow"),
                intent_hash=fp("intent"),
                permit_fingerprint=fp("permit"),
            ),
        )
        broker = SimpleNamespace(fingerprint=fp("broker"))
        reconciliation = SimpleNamespace(fingerprint=fp("reconciliation"))
        return production, shadow, receipt, broker, reconciliation

    def test_envelope_binds_production_admission_receipt_and_broker_truth(self):
        production, shadow, receipt, broker, reconciliation = self.fixtures()
        with patch("dusty.m188_production_reconciliation.reconcile_execution", return_value=reconciliation) as core:
            envelope, result = reconcile_production_execution(
                production_admission=production,
                shadow=shadow,
                receipt=receipt,
                broker=broker,
            )
        core.assert_called_once_with(shadow, receipt, broker)
        self.assertIs(result, reconciliation)
        self.assertEqual(envelope.production_custody_fingerprint, fp("custody"))
        self.assertEqual(envelope.m187_production_admission_fingerprint, fp("m187-production"))
        self.assertEqual(envelope.m187_receipt_fingerprint, fp("receipt"))
        self.assertEqual(envelope.reconciliation_fingerprint, fp("reconciliation"))
        self.assertFalse(envelope.broker_write_authority)
        self.assertFalse(envelope.retry_authority)

    def test_identity_drift_blocks_before_core_reconciliation(self):
        production, shadow, receipt, broker, reconciliation = self.fixtures()
        receipt.admission.shadow_fingerprint = fp("wrong-shadow")
        with patch("dusty.m188_production_reconciliation.reconcile_execution", return_value=reconciliation) as core:
            with self.assertRaisesRegex(PermissionError, "shadow identity drift"):
                reconcile_production_execution(
                    production_admission=production,
                    shadow=shadow,
                    receipt=receipt,
                    broker=broker,
                )
        core.assert_not_called()

    def test_module_has_no_direct_broker_send_surface(self):
        self.assertNotIn("order_send(", MODULE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
