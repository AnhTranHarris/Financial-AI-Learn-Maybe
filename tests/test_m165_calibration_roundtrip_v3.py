from __future__ import annotations

from pathlib import Path
import unittest

from tools.execute_m165_calibration_roundtrip_v3 import _entry_order_ticket, _parse_time


class M165CalibrationRoundTripV3Tests(unittest.TestCase):
    def test_extracts_positive_order_ticket_from_child_receipt(self) -> None:
        receipt = {"entry": {"execution": {"order_ticket": 36923813}}}
        self.assertEqual(_entry_order_ticket(receipt), 36923813)
        self.assertEqual(_entry_order_ticket({}), 0)
        self.assertEqual(_entry_order_ticket({"entry": {"execution": {"order_ticket": 0}}}), 0)

    def test_receipt_time_requires_timezone(self) -> None:
        parsed = _parse_time("2026-09-08T22:40:18.139652+00:00")
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)
        with self.assertRaises(ValueError):
            _parse_time("2026-09-08T22:40:18")

    def test_v3_has_no_direct_broker_send_surface(self) -> None:
        path = Path("tools/execute_m165_calibration_roundtrip_v3.py")
        text = path.read_text(encoding="utf-8")
        self.assertNotIn("order_send(", text)
        self.assertIn("execute_m165_calibration_roundtrip_v2.py", text)
        self.assertIn("capture_broker_forensics", text)
        self.assertIn("retry\": False", text)


if __name__ == "__main__":
    unittest.main()
