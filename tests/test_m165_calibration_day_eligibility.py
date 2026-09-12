from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "check_m165_calibration_day_eligibility.py"


class M165CalibrationDayEligibilityTests(unittest.TestCase):
    def test_probe_is_read_only_and_day2_day3_only(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn('choices=(2, 3)', text)
        self.assertIn('"broker_write": False', text)
        self.assertIn('"custody_write": False', text)
        self.assertIn('"new_entry": False', text)
        self.assertIn('"recovery_close": False', text)
        self.assertNotIn("order_send(", text)

    def test_probe_reuses_campaign_start_validator_and_broker_clock(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("validate_campaign_start", text)
        self.assertIn("symbol_info_tick", text)
        self.assertIn("broker_instant.date()", text)
        self.assertIn("MAX_EVIDENCE_CLOCK_OFFSET_SECONDS", text)

    def test_probe_requires_exact_clean_head_and_flat_demo_state(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn('"rev-parse", "HEAD"', text)
        self.assertIn('"status", "--porcelain=v1"', text)
        self.assertIn("ACCOUNT_TRADE_MODE_DEMO", text)
        self.assertIn("positions_get", text)
        self.assertIn("orders_get", text)

    def test_probe_reports_precise_native_permission_blockers(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("_native_blockers", text)
        self.assertIn("terminal_trade_permission_disabled", text)
        self.assertIn("terminal_trade_api_disabled", text)
        self.assertIn("account_trade_permission_disabled", text)
        self.assertIn("account_expert_trading_disabled", text)
        self.assertIn("symbol_position_open", text)
        self.assertIn("symbol_order_open", text)
        self.assertIn('"permission_blockers": native_blockers', text)

    def test_probe_blocks_obvious_fx_weekend_session_before_send(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn("_fx_session_blockers", text)
        self.assertIn("fx_weekend_session_closed", text)
        self.assertIn('"session_blockers": session_blockers', text)
        self.assertIn('weekday == 4 and hour >= 22', text)
        self.assertIn('weekday == 5', text)
        self.assertIn('weekday == 6 and hour < 22', text)
        self.assertNotIn("order_send(", text)


if __name__ == "__main__":
    unittest.main()
