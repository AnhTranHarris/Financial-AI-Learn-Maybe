from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from dusty.m189_production_cost_learning import (
    learn_production_demo_execution_costs,
    sample_from_production_reconciliation,
)

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "dusty" / "m189_production_cost_learning.py"


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M189ProductionCostLearningTests(unittest.TestCase):
    def test_sample_binds_m188_production_reconciliation(self):
        production = SimpleNamespace(
            production_custody_fingerprint=fp("custody"),
            fingerprint=fp("m188-production"),
            reconciliation_fingerprint=fp("reconciliation"),
        )
        shadow = SimpleNamespace(fingerprint=fp("shadow"))
        reconciliation = SimpleNamespace(fingerprint=fp("reconciliation"), shadow_fingerprint=fp("shadow"))
        sample = SimpleNamespace(fingerprint=fp("sample"), reconciliation_fingerprint=fp("reconciliation"))
        with patch("dusty.m189_production_cost_learning.sample_from_reconciliation", return_value=sample) as core:
            envelope, result = sample_from_production_reconciliation(
                production_reconciliation=production,
                shadow=shadow,
                reconciliation=reconciliation,
                broker_profile_fingerprint=fp("broker"),
                point_size=0.00001,
            )
        self.assertIs(result, sample)
        self.assertEqual(envelope.production_custody_fingerprint, fp("custody"))
        self.assertEqual(envelope.sample_fingerprint, fp("sample"))
        self.assertFalse(envelope.broker_write_authority)
        core.assert_called_once()

    def test_learning_rejects_mixed_custody(self):
        reconciliation = SimpleNamespace(fingerprint=fp("r1"))
        sample = SimpleNamespace(fingerprint=fp("s1"), reconciliation_fingerprint=fp("r1"))
        e1 = SimpleNamespace(
            fingerprint=fp("e1"), production_custody_fingerprint=fp("c1"),
            sample_fingerprint=fp("s1"), reconciliation_fingerprint=fp("r1"),
        )
        e2 = SimpleNamespace(
            fingerprint=fp("e2"), production_custody_fingerprint=fp("c2"),
            sample_fingerprint=fp("s1"), reconciliation_fingerprint=fp("r1"),
        )
        with self.assertRaisesRegex(ValueError, "cannot mix Champion custody"):
            learn_production_demo_execution_costs(
                ((e1, sample, reconciliation), (e2, sample, reconciliation)),
                broker_profile_fingerprint=fp("broker"), symbol="EURUSD",
            )

    def test_learning_binds_samples_and_core_learning(self):
        reconciliation = SimpleNamespace(fingerprint=fp("r1"))
        sample = SimpleNamespace(fingerprint=fp("s1"), reconciliation_fingerprint=fp("r1"))
        envelope = SimpleNamespace(
            fingerprint=fp("e1"), production_custody_fingerprint=fp("custody"),
            sample_fingerprint=fp("s1"), reconciliation_fingerprint=fp("r1"),
        )
        learning = SimpleNamespace(fingerprint=fp("learning"))
        with patch("dusty.m189_production_cost_learning.learn_demo_execution_costs", return_value=learning) as core:
            result, observed = learn_production_demo_execution_costs(
                ((envelope, sample, reconciliation),),
                broker_profile_fingerprint=fp("broker"), symbol="EURUSD",
            )
        self.assertIs(observed, learning)
        self.assertEqual(result.production_custody_fingerprint, fp("custody"))
        self.assertEqual(result.learning_fingerprint, fp("learning"))
        self.assertFalse(result.retry_authority)
        core.assert_called_once()

    def test_module_has_no_direct_broker_send_surface(self):
        self.assertNotIn("order_send(", MODULE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
