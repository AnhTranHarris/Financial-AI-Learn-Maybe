from __future__ import annotations

from pathlib import Path
import unittest

from tools.run_m165_day1_calibration_resume_v2 import (
    CHILD_CONFIRMATION,
    CONFIRMATION,
    EXPECTED_START_OBSERVATIONS,
    MAX_NEW_ROUNDTRIPS,
    RECOVERY_CONFIRMATION,
    TARGET_OBSERVATIONS,
)


class M165Day1CalibrationResumeV2Tests(unittest.TestCase):
    def test_resume_bounds_are_fixed(self) -> None:
        self.assertEqual(EXPECTED_START_OBSERVATIONS, 4)
        self.assertEqual(MAX_NEW_ROUNDTRIPS, 2)
        self.assertEqual(TARGET_OBSERVATIONS, 10)
        self.assertEqual(CONFIRMATION, "M165-DEMO-DAY1-RESUME-2")
        self.assertEqual(CHILD_CONFIRMATION, "M165-DEMO-ONE-SHOT")
        self.assertEqual(RECOVERY_CONFIRMATION, "M165-DEMO-RECOVERY-CLOSE")

    def test_resume_has_no_direct_broker_send_surface(self) -> None:
        text = Path("tools/run_m165_day1_calibration_resume_v2.py").read_text(encoding="utf-8")
        self.assertNotIn("order_send(", text)
        self.assertIn("recover_m165_open_calibration_position.py", text)
        self.assertIn("execute_m165_calibration_roundtrip_v3.py", text)
        self.assertIn("reconcile_m165_ambiguous_order.py", text)
        self.assertIn("extract_m165_native_observations.py", text)
        self.assertIn("import_m165_observations.py", text)
        self.assertIn('"retry": False', text)

    def test_previous_child_is_harvested_before_new_entries(self) -> None:
        text = Path("tools/run_m165_day1_calibration_resume_v2.py").read_text(encoding="utf-8")
        harvest_index = text.index('output_dir=output_root / "previous-child-harvest"')
        loop_index = text.index("for index in range(1, MAX_NEW_ROUNDTRIPS + 1)")
        self.assertLess(harvest_index, loop_index)

    def test_position_open_state_routes_to_exact_recovery_close(self) -> None:
        text = Path("tools/run_m165_day1_calibration_resume_v2.py").read_text(encoding="utf-8")
        self.assertIn('v3_status != "position_open_governed_close_required"', text)
        self.assertIn('"--confirm-demo-write", RECOVERY_CONFIRMATION', text)
        self.assertNotIn("while True", text)
        self.assertNotIn("retry_count", text)


if __name__ == "__main__":
    unittest.main()
