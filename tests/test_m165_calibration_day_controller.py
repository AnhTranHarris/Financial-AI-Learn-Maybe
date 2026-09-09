from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_m165_calibration_day.py"


class M165CalibrationDayControllerTests(unittest.TestCase):
    def test_controller_has_no_direct_broker_send_surface(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertNotIn("order_send(", text)
        self.assertIn("execute_m165_calibration_roundtrip_v3.py", text)
        self.assertIn("recover_m165_open_calibration_position.py", text)

    def test_controller_is_fixed_to_day2_day3_and_five_roundtrips(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn('choices=(2, 3)', text)
        self.assertIn('M165-DEMO-DAY{day}-BATCH-5', text)
        self.assertIn("policy.maximum_roundtrips", text)

    def test_controller_requires_flat_demo_and_utc_date_lock(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("_require_flat_demo", text)
        self.assertIn("utc_date() != campaign_date", text)
        self.assertIn("extracted observations are outside the authorized campaign UTC date", text)

    def test_controller_routes_open_position_to_governed_recovery_only(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("position_open_governed_close_required", text)
        self.assertIn("already_closed_no_send", text)
        self.assertIn("RECOVERY_CONFIRMATION", text)
        self.assertIn("retry\": False", text)

    def test_day3_requires_calibrated_final_state(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn('expected_status = "calibrated" if policy.day_number == 3 else "insufficient"', text)


if __name__ == "__main__":
    unittest.main()
