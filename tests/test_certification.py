from __future__ import annotations

import unittest

from dusty.certification import (
    ResearchGateInput,
    certify_reasoning_core,
    qualify_research,
)


class ReasoningCertificationTests(unittest.TestCase):
    def test_m12_exhaustive_reasoning_certification(self):
        report = certify_reasoning_core()
        self.assertTrue(report.passed, report.failures)
        self.assertEqual(report.cases, 6480)
        self.assertGreater(report.legal_transitions, 0)
        self.assertGreater(report.illegal_transitions, 0)
        self.assertEqual(len(report.fingerprint), 64)
        self.assertEqual(report, certify_reasoning_core())

    def test_m23_gate_can_never_authorize_broker_writes(self):
        report = qualify_research(
            ResearchGateInput(True, True, True, True, True, True)
        )
        self.assertTrue(report.ready_for_shadow_research)
        self.assertFalse(report.broker_write_authorized)

    def test_m23_gate_fails_closed(self):
        report = qualify_research(
            ResearchGateInput(True, False, True, True, True, False)
        )
        self.assertFalse(report.ready_for_shadow_research)
        self.assertIn("provenance_incomplete", report.reasons)
        self.assertIn("forecast_comparison_incomplete", report.reasons)
        self.assertFalse(report.broker_write_authorized)


if __name__ == "__main__":
    unittest.main()
