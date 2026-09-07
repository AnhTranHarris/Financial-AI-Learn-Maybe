from __future__ import annotations

import unittest

from dusty.multi_desk_certification import (
    CertifiedDeskEvidence,
    MultiDeskGenerationStatus,
    certify_multi_desk_generation,
)
from dusty.single_desk_demo_certification import SingleDeskDemoStatus
from dusty.six_desk_graduation import (
    GraduationGenerationEvidence,
    SixDeskGraduationStatus,
    certify_six_desk_graduation,
)


def h(ch: str) -> str:
    return ch * 64


CHAMPION = h("a")


def desk(index: int, generation: str) -> CertifiedDeskEvidence:
    alphabet = "bcdef0123456789"
    cert = alphabet[index % len(alphabet)]
    account = alphabet[(index + 1) % len(alphabet)]
    terminal = alphabet[(index + 2) % len(alphabet)]
    runtime = alphabet[(index + 3) % len(alphabet)]
    return CertifiedDeskEvidence(
        desk_id=f"desk-{index}",
        generation_id=generation,
        single_desk_status=SingleDeskDemoStatus.CERTIFIED,
        single_desk_fingerprint=h(cert),
        champion_fingerprint=CHAMPION,
        account_fingerprint=h(account),
        terminal_fingerprint=h(terminal),
        broker_profile_fingerprint=h("9"),
        runtime_attestation_fingerprint=h(runtime),
    )


def generation(name: str, indices: tuple[int, ...]) -> GraduationGenerationEvidence:
    desks = tuple(desk(i, name) for i in indices)
    certification = certify_multi_desk_generation(name, desks)
    return GraduationGenerationEvidence(certification=certification, desks=desks)


class M199SixDeskGraduationTests(unittest.TestCase):
    def test_one_six_desk_generation_graduates(self):
        item = generation("gen-1", (0, 4, 8, 12, 16, 20))
        result = certify_six_desk_graduation(CHAMPION, (item,))
        self.assertIs(result.status, SixDeskGraduationStatus.GRADUATED)
        self.assertEqual(result.qualifying_desk_count, 6)
        self.assertEqual(result.qualifying_generation_count, 1)
        self.assertEqual(result.blockers, ())

    def test_six_one_desk_sequential_generations_graduate(self):
        rows = tuple(generation(f"gen-{i}", (i * 4,)) for i in range(6))
        result = certify_six_desk_graduation(CHAMPION, rows)
        self.assertIs(result.status, SixDeskGraduationStatus.GRADUATED)
        self.assertEqual(result.qualifying_desk_count, 6)
        self.assertEqual(result.qualifying_generation_count, 6)

    def test_five_independent_desks_remain_pending(self):
        rows = tuple(generation(f"gen-{i}", (i * 4,)) for i in range(5))
        result = certify_six_desk_graduation(CHAMPION, rows)
        self.assertIs(result.status, SixDeskGraduationStatus.PENDING)
        self.assertEqual(result.qualifying_desk_count, 5)
        self.assertIn("six_independent_desks_not_yet_proven", result.blockers)

    def test_rejected_generation_counts_zero_and_requests_another_round(self):
        good = generation("gen-good", (0, 4, 8, 12, 16))
        failed_desks = (desk(24, "gen-failed"),)
        failed_cert = certify_multi_desk_generation("gen-failed", failed_desks)
        object.__setattr__(failed_cert, "status", MultiDeskGenerationStatus.REJECTED)
        failed = GraduationGenerationEvidence(failed_cert, failed_desks)

        # Pairing a mutated summary with raw evidence must be rejected as a
        # certification mismatch rather than silently counted.
        result = certify_six_desk_graduation(CHAMPION, (good, failed))
        self.assertIs(result.status, SixDeskGraduationStatus.REJECTED)
        self.assertIn("generation_certification_mismatch", result.blockers)

    def test_naturally_pending_generation_counts_zero(self):
        good = generation("gen-good", (0, 4, 8, 12, 16))
        pending_cert = certify_multi_desk_generation("gen-pending", ())
        pending = GraduationGenerationEvidence(pending_cert, ())
        result = certify_six_desk_graduation(CHAMPION, (good, pending))
        self.assertIs(result.status, SixDeskGraduationStatus.PENDING)
        self.assertEqual(result.qualifying_desk_count, 5)
        self.assertIn("generation_not_certified", result.blockers)

    def test_reused_account_across_generations_is_hard_reject(self):
        first = generation("gen-1", (0,))
        reused = desk(4, "gen-2")
        object.__setattr__(reused, "account_fingerprint", first.desks[0].account_fingerprint)
        cert = certify_multi_desk_generation("gen-2", (reused,))
        second = GraduationGenerationEvidence(cert, (reused,))
        result = certify_six_desk_graduation(CHAMPION, (first, second))
        self.assertIs(result.status, SixDeskGraduationStatus.REJECTED)
        self.assertIn("reused_account_identity", result.blockers)

    def test_reused_single_desk_certification_across_generations_is_hard_reject(self):
        first = generation("gen-1", (0,))
        reused = desk(4, "gen-2")
        object.__setattr__(reused, "single_desk_fingerprint", first.desks[0].single_desk_fingerprint)
        cert = certify_multi_desk_generation("gen-2", (reused,))
        second = GraduationGenerationEvidence(cert, (reused,))
        result = certify_six_desk_graduation(CHAMPION, (first, second))
        self.assertIs(result.status, SixDeskGraduationStatus.REJECTED)
        self.assertIn("reused_single_desk_certification", result.blockers)

    def test_reused_runtime_attestation_across_generations_is_hard_reject(self):
        first = generation("gen-1", (0,))
        reused = desk(4, "gen-2")
        object.__setattr__(reused, "runtime_attestation_fingerprint", first.desks[0].runtime_attestation_fingerprint)
        cert = certify_multi_desk_generation("gen-2", (reused,))
        second = GraduationGenerationEvidence(cert, (reused,))
        result = certify_six_desk_graduation(CHAMPION, (first, second))
        self.assertIs(result.status, SixDeskGraduationStatus.REJECTED)
        self.assertIn("reused_runtime_attestation", result.blockers)

    def test_mixed_champion_is_hard_reject(self):
        first = generation("gen-1", (0,))
        second_desk = desk(4, "gen-2")
        object.__setattr__(second_desk, "champion_fingerprint", h("b"))
        second_cert = certify_multi_desk_generation("gen-2", (second_desk,))
        second = GraduationGenerationEvidence(second_cert, (second_desk,))
        result = certify_six_desk_graduation(CHAMPION, (first, second))
        self.assertIs(result.status, SixDeskGraduationStatus.REJECTED)
        self.assertIn("mixed_champion_graduation", result.blockers)

    def test_terminal_reuse_across_sequential_generations_is_allowed(self):
        first = generation("gen-1", (0,))
        second_desk = desk(4, "gen-2")
        object.__setattr__(second_desk, "terminal_fingerprint", first.desks[0].terminal_fingerprint)
        second_cert = certify_multi_desk_generation("gen-2", (second_desk,))
        second = GraduationGenerationEvidence(second_cert, (second_desk,))
        rows = (first, second) + tuple(generation(f"gen-{i}", (i * 4,)) for i in range(3, 7))
        result = certify_six_desk_graduation(CHAMPION, rows)
        self.assertIs(result.status, SixDeskGraduationStatus.GRADUATED)
        self.assertEqual(result.qualifying_desk_count, 6)

    def test_more_than_six_independent_desks_still_graduates(self):
        item = generation("gen-1", (0, 4, 8, 12, 16, 20, 24))
        result = certify_six_desk_graduation(CHAMPION, (item,))
        self.assertIs(result.status, SixDeskGraduationStatus.GRADUATED)
        self.assertEqual(result.qualifying_desk_count, 7)

    def test_result_never_grants_operational_authority(self):
        item = generation("gen-1", (0, 4, 8, 12, 16, 20))
        result = certify_six_desk_graduation(CHAMPION, (item,))
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_fingerprint_is_order_independent(self):
        rows = tuple(generation(f"gen-{i}", (i * 4,)) for i in range(6))
        left = certify_six_desk_graduation(CHAMPION, rows)
        right = certify_six_desk_graduation(CHAMPION, tuple(reversed(rows)))
        self.assertEqual(left.fingerprint, right.fingerprint)


if __name__ == "__main__":
    unittest.main()
