from __future__ import annotations

from pathlib import Path
import unittest

from tools.run_m165_day1_calibration_batch import (
    CHILD_CONFIRMATION,
    CONFIRMATION,
    EXPECTED_START_OBSERVATIONS,
    MAX_ROUNDTRIPS,
    TARGET_OBSERVATIONS,
)


class M165Day1CalibrationBatchTests(unittest.TestCase):
    def test_batch_bounds_are_fixed_for_day_one(self) -> None:
        self.assertEqual(EXPECTED_START_OBSERVATIONS, 4)
        self.assertEqual(MAX_ROUNDTRIPS, 3)
        self.assertEqual(TARGET_OBSERVATIONS, 10)
        self.assertEqual(CONFIRMATION, "M165-DEMO-DAY1-BATCH-3")
        self.assertEqual(CHILD_CONFIRMATION, "M165-DEMO-ONE-SHOT")

    def test_batch_has_no_direct_broker_send_surface(self) -> None:
        text = Path("tools/run_m165_day1_calibration_batch.py").read_text(encoding="utf-8")
        self.assertNotIn("order_send(", text)
        self.assertIn("execute_m165_calibration_roundtrip_v3.py", text)
        self.assertIn("plan_m165_calibration_roundtrip.py", text)
        self.assertIn("reconcile_m165_ambiguous_order.py", text)
        self.assertIn("extract_m165_native_observations.py", text)
        self.assertIn("import_m165_observations.py", text)
        self.assertIn('"retry": False', text)
        self.assertIn("_require_flat_demo", text)

    def test_batch_stops_on_nonzero_child_instead_of_retrying(self) -> None:
        text = Path("tools/run_m165_day1_calibration_batch.py").read_text(encoding="utf-8")
        self.assertIn("if v3_exit != 0", text)
        self.assertIn('master["status"] = "stopped_no_retry"', text)
        self.assertNotIn("while True", text)
        self.assertNotIn("retry_count", text)


if __name__ == "__main__":
    unittest.main()
