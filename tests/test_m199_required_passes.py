import unittest

from dusty.m199_six_desk_graduation import certify_six_desk_graduation


class M199RequiredPassesTests(unittest.TestCase):
    def test_required_passes_must_be_positive(self):
        with self.assertRaises(ValueError):
            certify_six_desk_graduation((), required_passes=0)


if __name__ == "__main__":
    unittest.main()
