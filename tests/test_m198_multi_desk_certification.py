from __future__ import annotations

import unittest

from dusty.multi_desk_certification import (
    CertifiedDeskEvidence,
    MultiDeskGenerationStatus,
    certify_multi_desk_generation,
)
from dusty.single_desk_demo_certification import SingleDeskDemoStatus


def h(ch: str) -> str:
    return ch * 64


def desk(
    index: int,
    *,
    generation: str = "gen-1",
    status: SingleDeskDemoStatus = SingleDeskDemoStatus.CERTIFIED,
    champion: str = h("a"),
    account: str | None = None,
    terminal: str | None = None,
    certification: str | None = None,
    runtime: str | None = None,
) -> CertifiedDeskEvidence:
    chars = "bcdef123456789"
    c = chars[index % len(chars)]
    return CertifiedDeskEvidence(
        desk_id=f"desk-{index}",
        generation_id=generation,
        single_desk_status=status,
        single_desk_fingerprint=certification or h(c),
        champion_fingerprint=champion,
        account_fingerprint=account or h(chars[(index + 1) % len(chars)]),
        terminal_fingerprint=terminal or h(chars[(index + 2) % len(chars)]),
        broker_profile_fingerprint=h("9"),
        runtime_attestation_fingerprint=runtime or h(chars[(index + 3) % len(chars)]),
    )


class M198MultiDeskCertificationTests(unittest.TestCase):
    def test_empty_cohort_is_pending(self):
        result = certify_multi_desk_generation("gen-1", ())
        self.assertIs(result.status, MultiDeskGenerationStatus.PENDING)
        self.assertIn("no_desk_evidence", result.blockers)

    def test_all_certified_independent_desks_certify_generation(self):
        result = certify_multi_desk_generation("gen-1", (desk(0), desk(4), desk(8)))
        self.assertIs(result.status, MultiDeskGenerationStatus.CERTIFIED)
        self.assertEqual(result.desk_count, 3)
        self.assertEqual(result.blockers, ())

    def test_one_pending_desk_keeps_generation_pending(self):
        result = certify_multi_desk_generation(
            "gen-1",
            (desk(0), desk(4, status=SingleDeskDemoStatus.PENDING)),
        )
        self.assertIs(result.status, MultiDeskGenerationStatus.PENDING)
        self.assertIn("desk_not_certified", result.blockers)

    def test_one_rejected_desk_rejects_generation(self):
        result = certify_multi_desk_generation(
            "gen-1",
            (desk(0), desk(4, status=SingleDeskDemoStatus.REJECTED)),
        )
        self.assertIs(result.status, MultiDeskGenerationStatus.REJECTED)
        self.assertIn("desk_rejected", result.blockers)

    def test_reused_account_rejects_generation(self):
        account = h("c")
        result = certify_multi_desk_generation(
            "gen-1",
            (desk(0, account=account), desk(4, account=account)),
        )
        self.assertIs(result.status, MultiDeskGenerationStatus.REJECTED)
        self.assertIn("duplicate_account_identity", result.blockers)

    def test_reused_single_desk_certification_rejects_generation(self):
        cert = h("d")
        result = certify_multi_desk_generation(
            "gen-1",
            (desk(0, certification=cert), desk(4, certification=cert)),
        )
        self.assertIs(result.status, MultiDeskGenerationStatus.REJECTED)
        self.assertIn("reused_single_desk_certification", result.blockers)

    def test_reused_runtime_attestation_rejects_generation(self):
        runtime = h("e")
        result = certify_multi_desk_generation(
            "gen-1",
            (desk(0, runtime=runtime), desk(4, runtime=runtime)),
        )
        self.assertIs(result.status, MultiDeskGenerationStatus.REJECTED)
        self.assertIn("reused_runtime_attestation", result.blockers)

    def test_mixed_champion_generation_rejected(self):
        result = certify_multi_desk_generation(
            "gen-1",
            (desk(0, champion=h("a")), desk(4, champion=h("b"))),
        )
        self.assertIs(result.status, MultiDeskGenerationStatus.REJECTED)
        self.assertIn("mixed_champion_generation", result.blockers)

    def test_generation_mismatch_is_pending_not_fabricated_pass(self):
        result = certify_multi_desk_generation(
            "gen-1",
            (desk(0), desk(4, generation="gen-2")),
        )
        self.assertIs(result.status, MultiDeskGenerationStatus.PENDING)
        self.assertIn("generation_identity_mismatch", result.blockers)

    def test_framework_does_not_require_six_desks(self):
        result = certify_multi_desk_generation("gen-1", (desk(0), desk(4)))
        self.assertIs(result.status, MultiDeskGenerationStatus.CERTIFIED)
        self.assertEqual(result.desk_count, 2)

    def test_result_has_no_operational_authority(self):
        result = certify_multi_desk_generation("gen-1", (desk(0), desk(4)))
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_fingerprint_is_deterministic_and_order_independent(self):
        a = desk(0)
        b = desk(4)
        left = certify_multi_desk_generation("gen-1", (a, b))
        right = certify_multi_desk_generation("gen-1", (b, a))
        self.assertEqual(left.fingerprint, right.fingerprint)


if __name__ == "__main__":
    unittest.main()
