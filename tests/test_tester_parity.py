from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.experience import TradeSide
from dusty.features import FeatureBar
from dusty.runtime import RuntimeTrade
from dusty.tester_parity import (
    ExpectedExitKind,
    ExpectedExecutionEnvelope,
    TesterTrade,
    expected_execution_envelopes,
    normalize_tester_trades,
    parse_tester_deals_csv,
    reconcile_execution_envelopes,
)


UTC = timezone.utc
T0 = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)


class TesterParityTests(unittest.TestCase):
    def test_semantic_deal_export_normalizes_one_position(self) -> None:
        entry_ms = int((T0 + timedelta(seconds=1)).timestamp() * 1000)
        exit_ms = int((T0 + timedelta(minutes=7)).timestamp() * 1000)
        text = (
            "strategy_hash,position_id,deal_id,time_msc,deal_type,deal_type_name,entry_type,entry_type_name,volume,price,commission,swap,profit,fee,reason,reason_name,sl,tp,comment\n"
            f"hash,55,101,{entry_ms},0,buy,0,in,0.01,1.1001,-0.04,0,0,0,3,expert,1.09,1.12,DDT:t1\n"
            f"hash,55,102,{exit_ms},1,sell,1,out,0.01,1.09,-0.04,0,-10.1,0,4,sl,1.09,1.12,[sl]\n"
        )
        trades = normalize_tester_trades(parse_tester_deals_csv(text))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].trade_id, "t1")
        self.assertIs(trades[0].side, TradeSide.LONG)
        self.assertEqual(trades[0].exit_reason, "sl")
        self.assertAlmostEqual(trades[0].net_pnl, -10.18)

    def test_stop_exit_uses_intrabar_window_not_fake_exact_bar_close_time(self) -> None:
        expected = (
            ExpectedExecutionEnvelope(
                strategy_hash="hash",
                trade_id="t1",
                side=TradeSide.LONG,
                volume=0.01,
                entry_signal_at=T0,
                entry_reference_price=1.1000,
                exit_not_before=T0 + timedelta(minutes=15),
                exit_not_after=T0 + timedelta(minutes=30),
                exit_kind=ExpectedExitKind.STOP,
                exit_reference_price=1.0900,
                initial_sl=1.0900,
                initial_tp=0.0,
            ),
        )
        observed = (
            TesterTrade(
                "hash",
                "t1",
                55,
                TradeSide.LONG,
                0.01,
                T0 + timedelta(seconds=2),
                T0 + timedelta(minutes=21, seconds=3),
                1.1001,
                1.0899,
                -10.2,
                "sl",
                1.0900,
                0.0,
            ),
        )
        result = reconcile_execution_envelopes(
            expected,
            observed,
            max_entry_delay_seconds=10,
            max_entry_price_gap=0.0002,
            max_exit_price_gap=0.0002,
        )
        self.assertTrue(result.passed, result.reasons)
        self.assertEqual(result.matched, 1)

    def test_cash_parity_is_independent_of_price_path_parity(self) -> None:
        expected = (
            ExpectedExecutionEnvelope(
                "hash",
                "t1",
                TradeSide.LONG,
                0.01,
                T0,
                1.1,
                T0 + timedelta(minutes=15),
                T0 + timedelta(minutes=30),
                ExpectedExitKind.TIME,
                1.11,
                1.09,
                0.0,
                expected_net_pnl=9.5,
            ),
        )
        observed = (
            TesterTrade(
                "hash",
                "t1",
                55,
                TradeSide.LONG,
                0.01,
                T0,
                T0 + timedelta(minutes=30),
                1.1,
                1.11,
                8.0,
                "expert",
                1.09,
                0.0,
            ),
        )
        execution_only = reconcile_execution_envelopes(
            expected,
            observed,
            max_entry_delay_seconds=0,
            max_entry_price_gap=0,
            max_exit_price_gap=0,
        )
        self.assertTrue(execution_only.passed)
        cash_checked = reconcile_execution_envelopes(
            expected,
            observed,
            max_entry_delay_seconds=0,
            max_entry_price_gap=0,
            max_exit_price_gap=0,
            max_net_pnl_gap=0.5,
        )
        self.assertFalse(cash_checked.passed)
        self.assertIn("trade:t1:net_pnl_gap", cash_checked.reasons)

    def test_cash_parity_requires_expected_value_when_enabled(self) -> None:
        expected = (
            ExpectedExecutionEnvelope(
                "hash", "t1", TradeSide.LONG, 0.01, T0, 1.1,
                T0 + timedelta(minutes=15), T0 + timedelta(minutes=30),
                ExpectedExitKind.TIME, 1.11, 1.09, 0.0,
            ),
        )
        observed = (
            TesterTrade(
                "hash", "t1", 55, TradeSide.LONG, 0.01,
                T0, T0 + timedelta(minutes=30), 1.1, 1.11, 9.0,
                "expert", 1.09, 0.0,
            ),
        )
        result = reconcile_execution_envelopes(
            expected,
            observed,
            max_entry_delay_seconds=0,
            max_entry_price_gap=0,
            max_exit_price_gap=0,
            max_net_pnl_gap=1.0,
        )
        self.assertFalse(result.passed)
        self.assertIn("trade:t1:expected_net_pnl_missing", result.reasons)

    def test_exit_reason_and_window_fail_closed(self) -> None:
        expected = (
            ExpectedExecutionEnvelope(
                "hash", "t1", TradeSide.LONG, 0.01, T0, 1.1,
                T0 + timedelta(minutes=15), T0 + timedelta(minutes=30),
                ExpectedExitKind.STOP, 1.09, 1.09, 0.0,
            ),
        )
        observed = (
            TesterTrade(
                "hash", "t1", 55, TradeSide.LONG, 0.01,
                T0 + timedelta(seconds=1), T0 + timedelta(minutes=31),
                1.1, 1.09, -10.0, "expert", 1.09, 0.0,
            ),
        )
        result = reconcile_execution_envelopes(
            expected, observed,
            max_entry_delay_seconds=10,
            max_entry_price_gap=0.0002,
            max_exit_price_gap=0.0002,
        )
        self.assertFalse(result.passed)
        self.assertIn("trade:t1:exit_reason_not_sl", result.reasons)
        self.assertIn("trade:t1:stop_exit_outside_bar_window", result.reasons)

    def test_runtime_trade_builds_stop_window_from_completed_bar_provenance(self) -> None:
        bars = (
            FeatureBar(T0, 1.10, 1.11, 1.09, 1.10, source_open_at=T0 - timedelta(minutes=15)),
            FeatureBar(T0 + timedelta(minutes=15), 1.10, 1.11, 1.08, 1.09, source_open_at=T0),
        )
        runtime = (
            RuntimeTrade(
                strategy_hash="hash",
                entry_at=T0,
                exit_at=T0 + timedelta(minutes=15),
                side=TradeSide.LONG,
                entry_price=1.10,
                exit_price=1.09,
                stop_price=1.09,
                target_price=None,
                exit_reason="stop",
                exit_stop_price=1.09,
            ),
        )
        envelope = expected_execution_envelopes(
            runtime,
            bars,
            strategy_hash="hash",
            trade_ids=("t1",),
            volumes=(0.01,),
            expected_net_pnls=(-10.5,),
        )[0]
        self.assertEqual(envelope.exit_not_before, T0)
        self.assertEqual(envelope.exit_not_after, T0 + timedelta(minutes=15))
        self.assertIs(envelope.exit_kind, ExpectedExitKind.STOP)
        self.assertEqual(envelope.expected_net_pnl, -10.5)


if __name__ == "__main__":
    unittest.main()
