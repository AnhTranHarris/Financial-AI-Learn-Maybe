from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "run_m165_calibration_day_guarded.py"


class M165CalibrationDayGuardedRunnerTests(unittest.TestCase):
    def test_guarded_runner_has_no_direct_broker_send_surface(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertNotIn("order_send(", text)
        self.assertIn("check_m165_calibration_day_eligibility.py", text)
        self.assertIn("run_m165_calibration_day.py", text)

    def test_guarded_runner_blocks_before_controller_when_not_eligible(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn('"status": "blocked_no_send"', text)
        self.assertIn('eligibility.get("eligible") is not True', text)
        self.assertIn('eligibility.get("status") != "eligible"', text)
        self.assertIn('"broker_write": False', text)
        self.assertIn('"retry": False', text)

    def test_guarded_runner_preserves_existing_controller_contract(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        for token in (
            '"--day"',
            '"--repo"',
            '"--expected-head"',
            '"--terminal-path"',
            '"--qualification-plan"',
            '"--custody-root"',
            '"--database"',
            '"--output-root"',
            '"--summary"',
            '"--confirm-demo-day"',
        ):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
