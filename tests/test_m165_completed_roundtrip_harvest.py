from __future__ import annotations

from pathlib import Path
import unittest


class M165CompletedRoundtripHarvestTests(unittest.TestCase):
    def test_harvest_operator_has_zero_broker_write_surface(self) -> None:
        text = Path("tools/harvest_m165_completed_roundtrip.py").read_text(encoding="utf-8")
        self.assertNotIn("order_send(", text)
        self.assertNotIn("recover_m165_open_calibration_position", text)
        self.assertIn("extract_m165_native_observations.py", text)
        self.assertIn("import_m165_observations.py", text)
        self.assertIn('"broker_write": False', text)
        self.assertIn('"new_entry": False', text)
        self.assertIn('"recovery_close": False', text)

    def test_harvest_operator_requires_exact_watermarks(self) -> None:
        text = Path("tools/harvest_m165_completed_roundtrip.py").read_text(encoding="utf-8")
        self.assertIn("--expected-start-count", text)
        self.assertIn("--expected-final-count", text)
        self.assertIn("--expected-distinct-days", text)
        self.assertIn("duplicates", text)
        self.assertIn("inserted", text)


if __name__ == "__main__":
    unittest.main()
