from __future__ import annotations

import unittest

from dusty.statistical import assess_selection_bias


class StatisticalHardeningTests(unittest.TestCase):
    def test_identical_positive_returns_do_not_receive_infinite_confidence(self) -> None:
        result = assess_selection_bias((0.01, 0.01, 0.01, 0.01), trial_count=1)
        self.assertFalse(result.passed)
        self.assertIn("zero_variance_returns", result.reasons)
        self.assertEqual(result.raw_signal_score, 0.0)


if __name__ == "__main__":
    unittest.main()
