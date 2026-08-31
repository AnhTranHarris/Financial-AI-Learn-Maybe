from __future__ import annotations

import unittest

from dusty.backtest import purged_walk_forward_ranges


class BacktestHardeningTests(unittest.TestCase):
    def test_post_test_embargo_is_not_reused_by_next_training_fold(self) -> None:
        folds = purged_walk_forward_ranges(
            100,
            train_rows=20,
            test_rows=10,
            purge_rows=2,
            embargo_rows=3,
        )
        first, second = folds[:2]
        self.assertEqual(first.test_end, 32)
        self.assertEqual(second.test_start, 35)
        self.assertEqual(second.train_end, first.test_end)
        self.assertLessEqual(second.train_end, second.test_start - 3)


if __name__ == "__main__":
    unittest.main()
