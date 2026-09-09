from __future__ import annotations

from pathlib import Path
import unittest


class M165RecoveryCloseOperatorTests(unittest.TestCase):
    def test_recovery_operator_has_no_direct_order_send_call(self) -> None:
        source = Path("tools/recover_m165_open_calibration_position.py").read_text(encoding="utf-8")
        self.assertNotIn("order_send(", source)
        self.assertIn("DemoMT5ExecutionAdapter", source)
        self.assertIn("M187CalibrationExecutionBridge", source)

    def test_recovery_operator_does_not_create_new_entry_intent(self) -> None:
        source = Path("tools/recover_m165_open_calibration_position.py").read_text(encoding="utf-8")
        self.assertNotIn("OrderIntent(", source)
        self.assertIn("PositionActionKind.FULL_CLOSE", source)
        self.assertIn('"new_entry": False', source)

    def test_recovery_operator_reconciles_history_after_close(self) -> None:
        source = Path("tools/recover_m165_open_calibration_position.py").read_text(encoding="utf-8")
        self.assertIn("history_deals_get(position=position_id)", source)
        self.assertIn("positions_get(ticket=position_id)", source)
        self.assertIn('status="close_unresolved_no_retry"', source)
        self.assertIn('status="already_closed_no_send"', source)


if __name__ == "__main__":
    unittest.main()
