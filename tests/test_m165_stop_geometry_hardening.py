from __future__ import annotations

from pathlib import Path
import unittest

from dusty.experience import TradeSide
from dusty.order_intent import _market_stop_geometry_valid
from tools.execute_m165_calibration_roundtrip_v2 import _safe_long_stop


class M165StopGeometryHardeningTests(unittest.TestCase):
    def test_reproduced_coinexx_spread_widening_moves_stop_below_bid(self) -> None:
        stop = _safe_long_stop(
            ask=1.16351,
            bid=1.16349,
            planned_distance=0.00002,
            tick_size=0.00001,
        )
        self.assertAlmostEqual(stop, 1.16348)
        self.assertLess(stop, 1.16349)

    def test_safe_long_stop_preserves_larger_planned_distance(self) -> None:
        stop = _safe_long_stop(
            ask=1.20005,
            bid=1.20003,
            planned_distance=0.00005,
            tick_size=0.00001,
        )
        self.assertAlmostEqual(stop, 1.20000)

    def test_safe_long_stop_rejects_bad_native_geometry(self) -> None:
        with self.assertRaises(ValueError):
            _safe_long_stop(ask=1.10000, bid=1.10001, planned_distance=0.00002, tick_size=0.00001)
        with self.assertRaises(ValueError):
            _safe_long_stop(ask=1.10002, bid=1.10000, planned_distance=0.000001, tick_size=0.00001)

    def test_generic_market_preflight_geometry_is_side_aware(self) -> None:
        self.assertTrue(_market_stop_geometry_valid(TradeSide.LONG, 1.09999, 1.10000, 1.10002))
        self.assertFalse(_market_stop_geometry_valid(TradeSide.LONG, 1.10000, 1.10000, 1.10002))
        self.assertFalse(_market_stop_geometry_valid(TradeSide.LONG, 1.10001, 1.10000, 1.10002))
        self.assertTrue(_market_stop_geometry_valid(TradeSide.SHORT, 1.10003, 1.10000, 1.10002))
        self.assertFalse(_market_stop_geometry_valid(TradeSide.SHORT, 1.10002, 1.10000, 1.10002))

    def test_hardening_adds_no_new_raw_send_owner(self) -> None:
        runtime = Path("tools/execute_m165_calibration_roundtrip_v2.py").read_text(encoding="utf-8")
        intent = Path("src/dusty/order_intent.py").read_text(encoding="utf-8")
        campaign = Path("src/dusty/m165_calibration_campaign.py").read_text(encoding="utf-8")
        self.assertNotIn("order_send(", runtime)
        self.assertNotIn("order_send(", intent)
        self.assertNotIn("order_send(", campaign)
        self.assertIn("_safe_long_stop", runtime)
        self.assertIn("market_stop_geometry_invalid", intent)


if __name__ == "__main__":
    unittest.main()
