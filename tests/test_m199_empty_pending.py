import unittest

from dusty.m199_six_desk_graduation import (
    SixDeskGraduationStatus,
    certify_six_desk_graduation,
)


class M199EmptyPendingTests(unittest.TestCase):
    def test_empty_evidence_is_pending(self):
        result = certify_six_desk_graduation(())
        self.assertIs(result.status, SixDeskGraduationStatus.PENDING)
        self.assertIn("no_generation_evidence", result.blockers)


if __name__ == "__main__":
    unittest.main()
