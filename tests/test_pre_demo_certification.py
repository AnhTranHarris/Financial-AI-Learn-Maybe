from __future__ import annotations

import unittest

from dusty.pre_demo_certification import (
    REQUIRED_MILESTONES,
    MilestoneEvidence,
    certify_pre_demo,
)


COMMIT = "a" * 40


def evidence(milestone: str, *, passed: bool = True, commit: str = COMMIT) -> MilestoneEvidence:
    return MilestoneEvidence(
        milestone=milestone,
        passed=passed,
        artifact_hash=f"artifact-{milestone}",
        data_fingerprint=f"data-{milestone}",
        config_fingerprint=f"config-{milestone}",
        test_fingerprint=f"tests-{milestone}",
        commit_sha=commit,
    )


class PreDemoCertificationTests(unittest.TestCase):
    def test_complete_bundle_only_authorizes_next_engineering_phase(self) -> None:
        rows = tuple(evidence(milestone) for milestone in REQUIRED_MILESTONES)
        first = certify_pre_demo(rows, current_commit_sha=COMMIT)
        second = certify_pre_demo(rows, current_commit_sha=COMMIT)
        self.assertTrue(first.ready_for_demo_execution_engineering)
        self.assertFalse(first.broker_write_authorized)
        self.assertEqual(first.certification_hash, second.certification_hash)
        self.assertEqual(first.reasons, ())
        self.assertIn('"broker_write_authorized":false', first.checkpoint_payload())

    def test_missing_failed_or_stale_commit_evidence_blocks_readiness(self) -> None:
        missing = tuple(evidence(milestone) for milestone in REQUIRED_MILESTONES if milestone != "M60")
        result = certify_pre_demo(missing, current_commit_sha=COMMIT)
        self.assertFalse(result.ready_for_demo_execution_engineering)
        self.assertIn("missing_evidence:M60", result.reasons)

        failed = tuple(
            evidence(milestone, passed=milestone != "M61")
            for milestone in REQUIRED_MILESTONES
        )
        result = certify_pre_demo(failed, current_commit_sha=COMMIT)
        self.assertIn("milestone_failed:M61", result.reasons)

        stale = tuple(
            evidence(milestone, commit="b" * 40 if milestone == "M64" else COMMIT)
            for milestone in REQUIRED_MILESTONES
        )
        result = certify_pre_demo(stale, current_commit_sha=COMMIT)
        self.assertIn("commit_mismatch:M64", result.reasons)

    def test_evidence_requires_fingerprints(self) -> None:
        with self.assertRaises(ValueError):
            MilestoneEvidence("M56", True, "", "data", "config", "tests", COMMIT)


if __name__ == "__main__":
    unittest.main()
