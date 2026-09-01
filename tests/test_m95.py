from __future__ import annotations

import unittest

from dusty.forecast_certification import (
    REQUIRED_FORECAST_MILESTONES,
    ForecastMilestoneEvidence,
    NativeForecastOperationalProof,
    certify_forecast_phase,
)
from dusty.forecast_demo import ForecastDeskEvidence, certify_forecast_demo_campaign
from dusty.trust_review import ProofLevel


COMMIT = "a" * 40


def evidence(commit: str = COMMIT):
    return tuple(ForecastMilestoneEvidence(row, True, f"artifact-{row}", f"data-{row}", f"config-{row}", f"test-{row}", commit) for row in REQUIRED_FORECAST_MILESTONES)


def native(*, passed: bool = True) -> NativeForecastOperationalProof:
    return NativeForecastOperationalProof(5000, "Broker", "Broker-Demo", "EURUSD", "M15", "a" * 64, "b" * 64, "c" * 64, "d" * 64, "e" * 64, passed, passed, passed)


def demo():
    rows = tuple(ForecastDeskEvidence(f"desk-{index}", "c" * 64, f"session-{index}", 40, 0.04, 100, 0.03) for index in range(6))
    return certify_forecast_demo_campaign(rows)


class M95ForecastCertificationTests(unittest.TestCase):
    def test_software_complete_package_states_operational_evidence_boundary(self):
        result = certify_forecast_phase(evidence(), current_commit_sha=COMMIT)
        self.assertTrue(result.software_package_certified)
        self.assertEqual(result.level, ProofLevel.OPERATIONAL_EVIDENCE_REQUIRED)
        self.assertFalse(result.live_write_authorized)

    def test_stale_commit_cannot_certify_current_forecasting(self):
        result = certify_forecast_phase(evidence("b" * 40), current_commit_sha=COMMIT)
        self.assertEqual(result.level, ProofLevel.FAILED)
        self.assertTrue(any(reason.startswith("commit_mismatch") for reason in result.reasons))

    def test_complete_native_and_six_desk_evidence_can_operationally_certify_not_live(self):
        result = certify_forecast_phase(
            evidence(),
            current_commit_sha=COMMIT,
            native_proof=native(),
            demo_certification=demo(),
            m75_operational_proof_hash="f" * 64,
            m85_analysis_certification_hash="1" * 64,
        )
        self.assertEqual(result.level, ProofLevel.OPERATIONALLY_PROVEN)
        self.assertTrue(result.operational_forecasting_certified)
        self.assertFalse(result.live_write_authorized)

    def test_failed_native_proof_fails_instead_of_widening_rules(self):
        result = certify_forecast_phase(evidence(), current_commit_sha=COMMIT, native_proof=native(passed=False))
        self.assertEqual(result.level, ProofLevel.FAILED)
        self.assertIn("native_forecast_operational_proof_failed", result.reasons)


if __name__ == "__main__":
    unittest.main()
