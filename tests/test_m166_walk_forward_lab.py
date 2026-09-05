from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import unittest

from dusty.walk_forward_lab import (
    WalkForwardFoldResult,
    WalkForwardMode,
    build_walk_forward_plan,
    summarize_walk_forward,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


class M166WalkForwardLaboratoryTests(unittest.TestCase):
    def test_anchored_plan_freezes_identity_and_nonoverlapping_test_windows(self) -> None:
        plan = build_walk_forward_plan(
            strategy_execution_fingerprint=_fp("strategy"),
            parameter_fingerprint=_fp("params"),
            dataset_fingerprint=_fp("dataset"),
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 7, 1, tzinfo=timezone.utc),
            train_days=60,
            test_days=30,
            mode=WalkForwardMode.ANCHORED,
        )
        self.assertGreaterEqual(len(plan.windows), 3)
        self.assertTrue(all(row.train_start == plan.windows[0].train_start for row in plan.windows))
        for previous, current in zip(plan.windows, plan.windows[1:]):
            self.assertGreaterEqual(current.test_start, previous.test_end)
        self.assertFalse(plan.broker_write_authority)

    def test_rolling_plan_moves_training_window_without_crossing_test(self) -> None:
        plan = build_walk_forward_plan(
            strategy_execution_fingerprint=_fp("strategy"),
            parameter_fingerprint=_fp("params"),
            dataset_fingerprint=_fp("dataset"),
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 6, 1, tzinfo=timezone.utc),
            train_days=60,
            test_days=30,
            mode=WalkForwardMode.ROLLING,
        )
        self.assertGreater(plan.windows[1].train_start, plan.windows[0].train_start)
        self.assertTrue(all(row.train_end == row.test_start for row in plan.windows))

    def test_summary_requires_every_exact_fold_and_keeps_worst_fold_visible(self) -> None:
        plan = build_walk_forward_plan(
            strategy_execution_fingerprint=_fp("strategy"),
            parameter_fingerprint=_fp("params"),
            dataset_fingerprint=_fp("dataset"),
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 5, 1, tzinfo=timezone.utc),
            train_days=60,
            test_days=30,
        )
        returns = [0.04, -0.03]
        rows = tuple(
            WalkForwardFoldResult(
                plan.fingerprint,
                window.fingerprint,
                window.fold,
                returns[index],
                0.02 + index * 0.01,
                20 + index,
                returns[index] > 0,
            )
            for index, window in enumerate(plan.windows)
        )
        summary = summarize_walk_forward(plan, rows)
        self.assertEqual(summary.fold_count, 2)
        self.assertEqual(summary.pass_count, 1)
        self.assertEqual(summary.worst_net_return, -0.03)
        self.assertEqual(summary.worst_max_drawdown, 0.03)
        self.assertFalse(summary.broker_write_authority)

        with self.assertRaises(ValueError):
            summarize_walk_forward(plan, rows[:-1])

    def test_result_identity_drift_is_rejected(self) -> None:
        plan = build_walk_forward_plan(
            strategy_execution_fingerprint=_fp("strategy"),
            parameter_fingerprint=_fp("params"),
            dataset_fingerprint=_fp("dataset"),
            start=datetime(2025, 1, 1, tzinfo=timezone.utc),
            end=datetime(2025, 4, 1, tzinfo=timezone.utc),
            train_days=60,
            test_days=30,
        )
        window = plan.windows[0]
        bad = WalkForwardFoldResult(_fp("wrong"), window.fingerprint, 1, 0.01, 0.01, 10, True)
        with self.assertRaises(ValueError):
            summarize_walk_forward(plan, (bad,))

    def test_range_must_fit_at_least_one_complete_fold(self) -> None:
        with self.assertRaises(ValueError):
            build_walk_forward_plan(
                strategy_execution_fingerprint=_fp("strategy"),
                parameter_fingerprint=_fp("params"),
                dataset_fingerprint=_fp("dataset"),
                start=datetime(2025, 1, 1, tzinfo=timezone.utc),
                end=datetime(2025, 2, 1, tzinfo=timezone.utc),
                train_days=30,
                test_days=30,
            )


if __name__ == "__main__":
    unittest.main()
