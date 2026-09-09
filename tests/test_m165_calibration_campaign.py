from __future__ import annotations

from datetime import datetime, timezone
import unittest

from dusty.broker_calibration import BrokerExecutionObservation, TradeSide
from dusty.m165_calibration_campaign import (
    calibration_day_policy,
    validate_campaign_progress,
    validate_campaign_start,
)


BROKER = "1" * 64


def _row(when: str, side: TradeSide, suffix: str) -> BrokerExecutionObservation:
    stamp = datetime.fromisoformat(when)
    if side is TradeSide.BUY:
        requested = fill = 1.10002
    else:
        requested = fill = 1.10000
    return BrokerExecutionObservation(
        broker_profile_fingerprint=BROKER,
        symbol="EURUSD",
        side=side,
        observed_at=stamp,
        point_size=0.00001,
        bid=1.10000,
        ask=1.10002,
        requested_price=requested,
        fill_price=fill,
        volume_lots=0.01,
        commission=-0.01,
        evidence_fingerprint=(suffix * 64)[:64],
    )


def _day_rows(day: int, count: int = 10) -> list[BrokerExecutionObservation]:
    result: list[BrokerExecutionObservation] = []
    for index in range(count):
        side = TradeSide.BUY if index % 2 == 0 else TradeSide.SELL
        suffix = format((day * 16 + index) % 16, "x")
        result.append(_row(f"2026-09-{day:02d}T12:{index:02d}:00+00:00", side, suffix))
    return result


class M165CalibrationCampaignTests(unittest.TestCase):
    def test_day_policies_are_fixed(self) -> None:
        day2 = calibration_day_policy(2)
        self.assertEqual((day2.starting_observations, day2.target_observations), (10, 20))
        self.assertEqual((day2.starting_distinct_days, day2.target_distinct_days), (1, 2))
        self.assertEqual(day2.maximum_roundtrips, 5)
        day3 = calibration_day_policy(3)
        self.assertEqual((day3.starting_observations, day3.target_observations), (20, 30))
        self.assertEqual((day3.starting_distinct_days, day3.target_distinct_days), (2, 3))
        self.assertEqual(day3.maximum_roundtrips, 5)
        with self.assertRaises(ValueError):
            calibration_day_policy(4)

    def test_day2_requires_new_utc_date(self) -> None:
        rows = _day_rows(9)
        policy = calibration_day_policy(2)
        with self.assertRaisesRegex(RuntimeError, "new UTC observation date"):
            validate_campaign_start(
                rows,
                policy=policy,
                now=datetime(2026, 9, 9, 23, 59, tzinfo=timezone.utc),
            )
        self.assertEqual(
            validate_campaign_start(
                rows,
                policy=policy,
                now=datetime(2026, 9, 10, 0, 1, tzinfo=timezone.utc),
            ).isoformat(),
            "2026-09-10",
        )

    def test_campaign_start_rejects_wrong_count_or_day_count(self) -> None:
        policy = calibration_day_policy(2)
        with self.assertRaises(RuntimeError):
            validate_campaign_start(
                _day_rows(9, 8),
                policy=policy,
                now=datetime(2026, 9, 10, 1, tzinfo=timezone.utc),
            )
        mixed = _day_rows(8, 5) + _day_rows(9, 5)
        with self.assertRaises(RuntimeError):
            validate_campaign_start(
                mixed,
                policy=policy,
                now=datetime(2026, 9, 10, 1, tzinfo=timezone.utc),
            )

    def test_progress_is_locked_to_authorized_utc_date(self) -> None:
        policy = calibration_day_policy(2)
        rows = _day_rows(9) + _day_rows(10, 2)
        validate_campaign_progress(
            rows,
            policy=policy,
            campaign_date=datetime(2026, 9, 10, tzinfo=timezone.utc).date(),
            expected_observations=12,
        )
        with self.assertRaises(RuntimeError):
            validate_campaign_progress(
                _day_rows(9) + _day_rows(11, 2),
                policy=policy,
                campaign_date=datetime(2026, 9, 10, tzinfo=timezone.utc).date(),
                expected_observations=12,
            )

    def test_campaign_policy_never_grants_authority(self) -> None:
        policy = calibration_day_policy(2)
        self.assertFalse(policy.broker_write_authority)
        self.assertFalse(policy.live_write_authority)
        self.assertFalse(policy.retry_authority)
        self.assertFalse(policy.promotion_authority)


if __name__ == "__main__":
    unittest.main()
