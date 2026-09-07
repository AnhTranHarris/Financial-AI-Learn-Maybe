import unittest

from dusty.m199_six_desk_graduation import (
    SixDeskGraduationStatus,
    certify_six_desk_graduation,
)
from dusty.multi_desk_certification import CertifiedDeskEvidence
from dusty.single_desk_demo_certification import SingleDeskDemoStatus


def _sha(ch: str) -> str:
    return ch * 64


def _desk(index: int, generation: str, *, status: SingleDeskDemoStatus = SingleDeskDemoStatus.CERTIFIED, champion: str = "a") -> CertifiedDeskEvidence:
    alphabet = "bcdefghijklmnopqrstuvwxyz0123456789"
    return CertifiedDeskEvidence(
        desk_id=f"desk-{index}",
        generation_id=generation,
        single_desk_status=status,
        single_desk_fingerprint=_sha(alphabet[index]),
        champion_fingerprint=_sha(champion),
        account_fingerprint=_sha(alphabet[index + 6]),
        terminal_fingerprint=_sha("f"),
        broker_profile_fingerprint=_sha("e"),
        runtime_attestation_fingerprint=_sha(alphabet[index + 12]),
    )


class M199SixDeskGraduationTests(unittest.TestCase):
    def test_six_independent_desks_in_one_generation_graduate(self):
        result = certify_six_desk_graduation(tuple(_desk(i, "generation-a") for i in range(6)))
        self.assertIs(result.status, SixDeskGraduationStatus.GRADUATED)
        self.assertEqual(result.certified_desk_count, 6)
        self.assertEqual(len(result.generation_fingerprints), 1)
        self.assertEqual(result.blockers, ())

    def test_six_sequential_single_desk_generations_are_equivalent(self):
        result = certify_six_desk_graduation(tuple(_desk(i, f"generation-{i}") for i in range(6)))
        self.assertIs(result.status, SixDeskGraduationStatus.GRADUATED)
        self.assertEqual(result.certified_desk_count, 6)
        self.assertEqual(len(result.generation_fingerprints), 6)

    def test_five_desks_remain_pending(self):
        result = certify_six_desk_graduation(tuple(_desk(i, "generation-a") for i in range(5)))
        self.assertIs(result.status, SixDeskGraduationStatus.PENDING)
        self.assertIn("insufficient_independent_desks", result.blockers)

    def test_one_rejected_desk_rejects_graduation(self):
        rows = [_desk(i, "generation-a") for i in range(6)]
        rows[-1] = _desk(5, "generation-a", status=SingleDeskDemoStatus.REJECTED)
        result = certify_six_desk_graduation(tuple(rows))
        self.assertIs(result.status, SixDeskGraduationStatus.REJECTED)
        self.assertIn("rejected_generation_present", result.blockers)

    def test_reusing_account_across_generations_is_rejected(self):
        rows = [_desk(i, f"generation-{i}") for i in range(6)]
        duplicate = rows[-1]
        rows[-1] = CertifiedDeskEvidence(
            desk_id=duplicate.desk_id,
            generation_id=duplicate.generation_id,
            single_desk_status=duplicate.single_desk_status,
            single_desk_fingerprint=duplicate.single_desk_fingerprint,
            champion_fingerprint=duplicate.champion_fingerprint,
            account_fingerprint=rows[0].account_fingerprint,
            terminal_fingerprint=duplicate.terminal_fingerprint,
            broker_profile_fingerprint=duplicate.broker_profile_fingerprint,
            runtime_attestation_fingerprint=duplicate.runtime_attestation_fingerprint,
        )
        result = certify_six_desk_graduation(tuple(rows))
        self.assertIs(result.status, SixDeskGraduationStatus.REJECTED)
        self.assertIn("reused_account_identity", result.blockers)

    def test_champion_drift_is_rejected(self):
        rows = [_desk(i, "generation-a") for i in range(6)]
        rows[-1] = _desk(5, "generation-a", champion="d")
        result = certify_six_desk_graduation(tuple(rows))
        self.assertIs(result.status, SixDeskGraduationStatus.REJECTED)
        self.assertIn("champion_drift_across_graduation", result.blockers)

    def test_constitutional_count_cannot_be_weakened(self):
        with self.assertRaises(ValueError):
            certify_six_desk_graduation(tuple(_desk(i, "generation-a") for i in range(5)), required_desk_count=5)

    def test_result_never_grants_operational_authority(self):
        result = certify_six_desk_graduation(tuple(_desk(i, "generation-a") for i in range(6)))
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_fingerprint_is_deterministic(self):
        rows = tuple(_desk(i, "generation-a") for i in range(6))
        self.assertEqual(certify_six_desk_graduation(rows).fingerprint, certify_six_desk_graduation(rows).fingerprint)


if __name__ == "__main__":
    unittest.main()
