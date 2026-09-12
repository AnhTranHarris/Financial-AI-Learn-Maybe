from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "check_m166_m174_production_readiness.py"


class M166M174ProductionReadinessTests(unittest.TestCase):
    def test_probe_is_read_only_and_authority_free(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn('"broker_write": False', text)
        self.assertIn('"live_write": False', text)
        self.assertIn('"custody_write": False', text)
        self.assertIn('"research_execution": False', text)
        self.assertIn('"promotion": False', text)
        self.assertNotIn("order_send(", text)

    def test_probe_requires_exact_clean_head_and_real_m165_breadth(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        self.assertIn('"rev-parse", "HEAD"', text)
        self.assertIn('"status", "--porcelain=v1"', text)
        self.assertIn('calibration_status != "calibrated"', text)
        self.assertIn("observation_count < 30", text)
        self.assertIn("distinct_days < 3", text)
        self.assertIn('{"buy", "sell"}', text)

    def test_probe_reports_entire_m166_m174_identity_contract(self) -> None:
        text = TOOL.read_text(encoding="utf-8")
        for stage in range(166, 175):
            self.assertIn(f'"m{stage}', text)
        self.assertIn("SERIOUS_CHALLENGER", text)
        self.assertIn("exact evidence-fingerprint set", text)
        self.assertIn("qualification_lane_missing", text)
        self.assertIn("strategy_estate_missing", text)


if __name__ == "__main__":
    unittest.main()
