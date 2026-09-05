from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.purged_validation import TemporalSample, build_purged_temporal_split


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _sample(label: str, feature: datetime, label_end: datetime) -> TemporalSample:
    return TemporalSample(_fp(label), feature, feature, label_end)


class M167PurgedTemporalValidationTests(unittest.TestCase):
    def test_training_label_horizon_touching_test_is_purged(self) -> None:
        start = datetime(2025, 2, 1, tzinfo=timezone.utc)
        end = datetime(2025, 3, 1, tzinfo=timezone.utc)
        rows = (
            _sample("safe", start - timedelta(days=10), start - timedelta(seconds=1)),
            _sample("leaky", start - timedelta(days=1), start + timedelta(days=2)),
            _sample("test", start + timedelta(days=2), start + timedelta(days=3)),
        )
        split = build_purged_temporal_split(rows, test_start=start, test_end=end)
        self.assertEqual([row.sample_fingerprint for row in split.training], [_fp("safe")])
        self.assertEqual([row.sample_fingerprint for row in split.test], [_fp("test")])
        self.assertIn(_fp("leaky"), {row.sample_fingerprint for row in split.purged})
        self.assertTrue(all(row.label_end < start for row in split.training))
        self.assertFalse(split.broker_write_authority)

    def test_test_label_that_realizes_after_window_is_not_scored(self) -> None:
        start = datetime(2025, 2, 1, tzinfo=timezone.utc)
        end = datetime(2025, 3, 1, tzinfo=timezone.utc)
        row = _sample("late-label", end - timedelta(hours=1), end + timedelta(hours=1))
        split = build_purged_temporal_split((row,), test_start=start, test_end=end)
        self.assertEqual(split.test, ())
        self.assertEqual(split.purged, (row,))

    def test_post_test_embargo_is_explicit_and_disjoint(self) -> None:
        start = datetime(2025, 2, 1, tzinfo=timezone.utc)
        end = datetime(2025, 3, 1, tzinfo=timezone.utc)
        embargoed = _sample("embargo", end + timedelta(hours=2), end + timedelta(hours=3))
        future = _sample("future", end + timedelta(days=2), end + timedelta(days=2, hours=1))
        split = build_purged_temporal_split(
            (embargoed, future),
            test_start=start,
            test_end=end,
            embargo_seconds=24 * 3600,
        )
        self.assertEqual(split.embargoed, (embargoed,))
        self.assertIn(future, split.purged)

    def test_split_identity_is_deterministic_under_input_order(self) -> None:
        start = datetime(2025, 2, 1, tzinfo=timezone.utc)
        end = datetime(2025, 3, 1, tzinfo=timezone.utc)
        rows = (
            _sample("a", start - timedelta(days=3), start - timedelta(days=2)),
            _sample("b", start + timedelta(days=1), start + timedelta(days=2)),
        )
        first = build_purged_temporal_split(rows, test_start=start, test_end=end)
        second = build_purged_temporal_split(reversed(rows), test_start=start, test_end=end)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_duplicate_identity_and_naive_timestamps_fail_closed(self) -> None:
        start = datetime(2025, 2, 1, tzinfo=timezone.utc)
        row = _sample("dup", start - timedelta(days=2), start - timedelta(days=1))
        with self.assertRaises(ValueError):
            build_purged_temporal_split((row, row), test_start=start, test_end=start + timedelta(days=1))
        with self.assertRaises(ValueError):
            TemporalSample(_fp("naive"), datetime(2025, 1, 1), datetime(2025, 1, 1), datetime(2025, 1, 2))


if __name__ == "__main__":
    unittest.main()
