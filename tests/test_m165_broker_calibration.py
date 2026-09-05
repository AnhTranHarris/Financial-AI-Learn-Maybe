from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.broker_calibration import (
    BrokerCalibrationPolicy,
    BrokerExecutionObservation,
    CalibrationStatus,
    TradeSide,
    calibrate_broker_economics,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _observation(index: int, *, broker: str | None = None, symbol: str = "EURUSD") -> BrokerExecutionObservation:
    side = TradeSide.BUY if index % 2 == 0 else TradeSide.SELL
    point = 0.00001
    requested = 1.10000 + index * 0.000001
    adverse = (index % 5) * point * 0.25
    fill = requested + adverse if side is TradeSide.BUY else requested - adverse
    spread_points = 8 + (index % 7)
    bid = 1.09990
    ask = bid + spread_points * point
    return BrokerExecutionObservation(
        broker_profile_fingerprint=broker or _fp("coinexx-profile"),
        symbol=symbol,
        side=side,
        observed_at=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc) + timedelta(hours=index * 4),
        point_size=point,
        bid=bid,
        ask=ask,
        requested_price=requested,
        fill_price=fill,
        volume_lots=0.1,
        commission=-0.35,
        fee=-0.02 if index % 3 == 0 else 0.0,
        swap=-0.01 if index % 4 == 0 else 0.0,
        evidence_fingerprint=_fp(f"evidence-{index}"),
    )


class M165BrokerEconomicsCalibrationTests(unittest.TestCase):
    def test_no_observations_remains_explicitly_uncalibrated(self) -> None:
        result = calibrate_broker_economics(
            (),
            broker_profile_fingerprint=_fp("coinexx-profile"),
            symbol="EURUSD",
        )
        self.assertEqual(result.status, CalibrationStatus.UNCALIBRATED)
        self.assertIsNone(result.spread_p95_points)
        self.assertFalse(result.broker_write_authority)

    def test_insufficient_observations_never_expose_synthetic_cost_metrics(self) -> None:
        result = calibrate_broker_economics(
            tuple(_observation(i) for i in range(10)),
            broker_profile_fingerprint=_fp("coinexx-profile"),
            symbol="eurusd",
        )
        self.assertEqual(result.status, CalibrationStatus.INSUFFICIENT)
        self.assertTrue(all(value is None for value in (
            result.spread_p95_points,
            result.adverse_slippage_p95_points,
            result.commission_fee_p95_per_lot,
        )))

    def test_sufficient_multi_day_both_side_observations_calibrate_percentiles(self) -> None:
        observations = tuple(_observation(i) for i in range(40))
        result = calibrate_broker_economics(
            observations,
            broker_profile_fingerprint=_fp("coinexx-profile"),
            symbol="EURUSD",
        )
        self.assertEqual(result.status, CalibrationStatus.CALIBRATED)
        self.assertEqual(result.observation_count, 40)
        self.assertGreaterEqual(result.distinct_days, 3)
        self.assertGreater(result.spread_p99_points, result.spread_p50_points)
        self.assertGreaterEqual(result.adverse_slippage_p99_points, result.adverse_slippage_p50_points)
        self.assertGreater(result.commission_fee_p95_per_lot, 0.0)
        self.assertEqual(len(result.observation_fingerprints), 40)

    def test_adverse_slippage_is_side_aware_and_favorable_fill_does_not_become_cost(self) -> None:
        buy = BrokerExecutionObservation(
            _fp("broker"), "EURUSD", TradeSide.BUY,
            datetime.now(timezone.utc), 0.00001,
            1.0, 1.0001, 1.00005, 1.00003, 0.1, -0.1,
        )
        sell = BrokerExecutionObservation(
            _fp("broker"), "EURUSD", TradeSide.SELL,
            datetime.now(timezone.utc), 0.00001,
            1.0, 1.0001, 1.00005, 1.00007, 0.1, -0.1,
        )
        self.assertEqual(buy.adverse_slippage_points, 0.0)
        self.assertEqual(sell.adverse_slippage_points, 0.0)

    def test_calibration_rejects_mixed_broker_or_symbol_evidence(self) -> None:
        rows = (_observation(0), _observation(1, broker=_fp("other")))
        with self.assertRaises(ValueError):
            calibrate_broker_economics(
                rows,
                broker_profile_fingerprint=_fp("coinexx-profile"),
                symbol="EURUSD",
                policy=BrokerCalibrationPolicy(minimum_observations=1, minimum_distinct_days=1),
            )

    def test_observation_rejects_crossed_quote(self) -> None:
        with self.assertRaises(ValueError):
            BrokerExecutionObservation(
                _fp("broker"), "EURUSD", TradeSide.BUY,
                datetime.now(timezone.utc), 0.00001,
                1.2, 1.1, 1.1, 1.1, 0.1, -0.1,
            )


if __name__ == "__main__":
    unittest.main()
