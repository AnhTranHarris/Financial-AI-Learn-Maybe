from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dusty.broker_research import (
    DealParityRecord,
    manifest_rows,
    parse_deal_parity_csv,
    render_research_manifest,
)
from dusty.experience import TradeSide
from dusty.runtime import RuntimeTrade


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


class TesterManifestTests(unittest.TestCase):
    def test_runtime_trade_becomes_deterministic_tester_manifest(self):
        trade = RuntimeTrade(
            "a" * 64,
            NOW,
            NOW + timedelta(hours=1),
            TradeSide.LONG,
            1.1,
            1.11,
            1.095,
            1.12,
            "max_hold",
        )
        rows = manifest_rows((trade,), volume=0.1)
        text = render_research_manifest(rows)
        self.assertIn("trade_id,entry_time,exit_time,side,volume,stop_price,target_price", text)
        self.assertIn("long,0.1,1.095,1.12", text)
        self.assertIn("2026.08.31 10:00:00", text)

    def test_deal_export_parser_requires_normalized_contract(self):
        text = (
            "strategy_hash,position_id,deal_id,time_msc,deal_type,entry_type,volume,price,commission,swap,profit,reason,comment\n"
            "abc,10,20,1788170400000,0,0,0.1,1.1,-1.0,0.0,0.0,3,DDT:t1\n"
        )
        rows = parse_deal_parity_csv(text)
        self.assertEqual(rows, (DealParityRecord("abc", 10, 20, 1788170400000, 0, 0, 0.1, 1.1, -1.0, 0.0, 0.0, 3, "DDT:t1"),))


class MQL5ResearchEAContractTests(unittest.TestCase):
    def test_ea_is_hard_gated_to_strategy_tester_and_ticket_close(self):
        source = Path("mt5/DustyResearchEA.mq5").read_text(encoding="utf-8")
        self.assertIn("MQLInfoInteger(MQL_TESTER)", source)
        self.assertIn("return INIT_FAILED", source)
        self.assertIn("Trade.PositionClose(Plans[i].position_ticket", source)
        self.assertIn("DEAL_POSITION_ID", source)
        self.assertNotIn("AccountInfoInteger(ACCOUNT_TRADE_MODE_REAL)", source)

    def test_ea_uses_documented_common_file_bridge_for_tester_artifacts(self):
        source = Path("mt5/DustyResearchEA.mq5").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("FILE_COMMON"), 2)
        self.assertIn(
            "FILE_READ|FILE_CSV|FILE_ANSI|FILE_COMMON|FILE_SHARE_READ",
            source,
        )
        self.assertIn("FILE_WRITE|FILE_CSV|FILE_ANSI|FILE_COMMON", source)
        self.assertNotIn("#property tester_file", source)


if __name__ == "__main__":
    unittest.main()
