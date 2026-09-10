from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import unittest

from tools.execute_m165_calibration_roundtrip_v2_floating_stop import (
    FLOATING_STOP_SPREAD_MULTIPLIER,
    _floating_safe_long_stop,
)


class M165FloatingStopWrapperTests(unittest.TestCase):
    def test_exact_rejected_coinexx_geometry_is_widened(self) -> None:
        stop = _floating_safe_long_stop(
            ask=1.16348,
            bid=1.16347,
            planned_distance=0.00002,
            tick_size=0.00001,
        )
        self.assertEqual(FLOATING_STOP_SPREAD_MULTIPLIER, 3)
        self.assertAlmostEqual(stop, 1.16345)
        self.assertLess(stop, 1.16346)

    def test_two_tick_spread_remains_conservative(self) -> None:
        stop = _floating_safe_long_stop(
            ask=1.16351,
            bid=1.16349,
            planned_distance=0.00002,
            tick_size=0.00001,
        )
        self.assertAlmostEqual(stop, 1.16345)
        self.assertLess(stop, 1.16348)

    def test_larger_planned_distance_is_preserved(self) -> None:
        stop = _floating_safe_long_stop(
            ask=1.20005,
            bid=1.20003,
            planned_distance=0.00010,
            tick_size=0.00001,
        )
        self.assertAlmostEqual(stop, 1.19995)

    def test_direct_script_execution_resolves_tools_package(self) -> None:
        wrapper = Path("tools/execute_m165_calibration_roundtrip_v2_floating_stop.py")
        proc = subprocess.run(
            [sys.executable, str(wrapper), "--help"],
            cwd=Path.cwd(),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr or proc.stdout)
        self.assertIn("usage:", proc.stdout.lower())

    def test_wrapper_has_no_raw_send_or_retry_authority(self) -> None:
        path = Path("tools/execute_m165_calibration_roundtrip_v2_floating_stop.py")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("order_send(", text)
        self.assertIn("execute_m165_calibration_roundtrip_v2", text)
        self.assertNotIn("retry_authority = True", text)
        self.assertNotIn("live_write\": True", text)

    def test_v3_routes_through_floating_guard(self) -> None:
        text = Path("tools/execute_m165_calibration_roundtrip_v3.py").read_text(encoding="utf-8")
        self.assertIn("execute_m165_calibration_roundtrip_v2_floating_stop.py", text)
        self.assertNotIn("order_send(", text)


if __name__ == "__main__":
    unittest.main()
