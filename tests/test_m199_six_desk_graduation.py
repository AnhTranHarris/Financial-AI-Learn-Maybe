import unittest

from dusty.m199_six_desk_graduation import (
    SixDeskGraduationStatus,
    certify_six_desk_graduation,
)


class M199SixDeskGraduationTests(unittest.TestCase):
    def test_placeholder(self):
        self.assertTrue(callable(certify_six_desk_graduation))
        self.assertEqual(SixDeskGraduationStatus.GRADUATED.value, "graduated")


if __name__ == "__main__":
    unittest.main()
