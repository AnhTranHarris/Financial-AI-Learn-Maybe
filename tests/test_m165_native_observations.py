from __future__ import annotations

import unittest

from dusty.broker_calibration import TradeSide
from dusty.m165_native_observations import broker_profile_fingerprint, build_observation


class M165NativeObservationTests(unittest.TestCase):
    def test_builds_buy_and_sell_observations_without_authority(self) -> None:
        broker = broker_profile_fingerprint(
            server="Coinexx-Demo",
            currency="USD",
            leverage=500,
            symbol="EURUSD",
            symbol_spec_fingerprint="a" * 64,
        )
        buy = build_observation(
            broker_profile=broker,
            symbol="EURUSD",
            side=TradeSide.BUY,
            time_msc=1788918019931,
            point_size=0.00001,
            bid=1.16261,
            ask=1.16262,
            requested_price=1.16262,
            deal={"price": 1.16262, "volume": 0.01, "commission": -0.01, "fee": 0.0, "swap": 0.0},
            evidence={"deal": 1},
        )
        sell = build_observation(
            broker_profile=broker,
            symbol="EURUSD",
            side=TradeSide.SELL,
            time_msc=1788918021186,
            point_size=0.00001,
            bid=1.16260,
            ask=1.16261,
            requested_price=1.16261,
            deal={"price": 1.16260, "volume": 0.01, "commission": -0.01, "fee": 0.0, "swap": 0.0},
            evidence={"deal": 2},
        )
        self.assertEqual(buy.side, TradeSide.BUY)
        self.assertEqual(sell.side, TradeSide.SELL)
        self.assertAlmostEqual(buy.spread_points, 1.0)
        self.assertAlmostEqual(sell.adverse_slippage_points, 1.0)
        self.assertAlmostEqual(buy.commission_fee_per_lot, 1.0)

    def test_broker_identity_requires_exact_symbol_spec(self) -> None:
        with self.assertRaises(ValueError):
            broker_profile_fingerprint(server="Coinexx-Demo", currency="USD", leverage=500, symbol="EURUSD", symbol_spec_fingerprint="bad")


if __name__ == "__main__":
    unittest.main()
