from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.broker_research import (
    BrokerEstimateRequest,
    MT5ResearchCalculator,
    TradeParityRecord,
    parse_trade_parity_csv,
    reconcile_trade_parity,
)
from dusty.experience import TradeSide
from dusty.pit_memory import SQLitePITKnowledge, TemporalKnowledge
from dusty.research import Clause, RuleOp
from dusty.runtime import RuntimeBar, compile_strategy, generate_runtime_trades
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)


def spec() -> StrategySpecV2:
    return StrategySpecV2(
        strategy_id="m66",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((Clause("trend", RuleOp.GT, 0),)),),
        exit_plan=ExitPlan(
            stop_rule="pct:0.01",
            target_rule="rr:2",
            trailing_rule="off",
            breakeven_rule="rr:1",
            max_hold_steps=3,
        ),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
    )


class RuntimeTests(unittest.TestCase):
    def test_typed_runtime_rejects_opaque_exit_rules(self):
        broken = StrategySpecV2(
            strategy_id="opaque",
            direction=TradeSide.LONG,
            entry_groups=(RuleGroup((Clause("trend", RuleOp.GT, 0),)),),
            exit_plan=ExitPlan(stop_rule="some prose"),
            decision_timeframe_minutes=15,
            intended_horizon_minutes=60,
        )
        with self.assertRaises(ValueError):
            compile_strategy(broken)

    def test_same_compiled_strategy_produces_deterministic_trade(self):
        compiled = compile_strategy(spec())
        bars = (
            RuntimeBar.of(NOW, open=100, high=101, low=99.5, close=100, features={"trend": 1}),
            RuntimeBar.of(NOW + timedelta(minutes=15), open=100, high=101.5, low=99.2, close=101, features={"trend": 1}),
            RuntimeBar.of(NOW + timedelta(minutes=30), open=101, high=102.1, low=100.5, close=102, features={"trend": 1}),
        )
        left = generate_runtime_trades(compiled, bars)
        right = generate_runtime_trades(compiled, bars)
        self.assertEqual(left, right)
        self.assertEqual(len(left), 1)
        self.assertEqual(left[0].exit_reason, "target")
        self.assertAlmostEqual(left[0].exit_price, 102.0)

    def test_stop_wins_ambiguous_bar(self):
        compiled = compile_strategy(spec())
        bars = (
            RuntimeBar.of(NOW, open=100, high=100, low=100, close=100, features={"trend": 1}),
            RuntimeBar.of(NOW + timedelta(minutes=15), open=100, high=103, low=98, close=101, features={"trend": 1}),
        )
        trade = generate_runtime_trades(compiled, bars)[0]
        self.assertEqual(trade.exit_reason, "stop")
        self.assertAlmostEqual(trade.exit_price, 99.0)


class PITMemoryTests(unittest.TestCase):
    def test_retrieval_cannot_see_future_known_record(self):
        db = SQLitePITKnowledge()
        try:
            db.remember(
                TemporalKnowledge.of(
                    record_id="past",
                    kind="event",
                    text="known",
                    tags=("eurusd",),
                    known_at=NOW,
                    effective_at=NOW + timedelta(hours=1),
                )
            )
            db.remember(
                TemporalKnowledge.of(
                    record_id="future",
                    kind="event",
                    text="not yet known",
                    tags=("eurusd",),
                    known_at=NOW + timedelta(hours=2),
                    effective_at=NOW,
                )
            )
            rows = db.retrieve_as_of(("eurusd",), as_of=NOW + timedelta(minutes=30))
            self.assertEqual(tuple(row.record_id for row in rows), ("past",))
            self.assertTrue(db.integrity_ok())
        finally:
            db.close()


class FakeMT5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self):
        self.calls = []

    def initialize(self, path):
        self.calls.append(("initialize", path))
        return True

    def shutdown(self):
        self.calls.append(("shutdown",))

    def order_calc_profit(self, action, symbol, volume, entry, stop):
        self.calls.append(("profit", action, symbol, volume, entry, stop))
        return -25.0

    def order_calc_margin(self, action, symbol, volume, entry):
        self.calls.append(("margin", action, symbol, volume, entry))
        return 42.0


class BrokerResearchTests(unittest.TestCase):
    def test_broker_native_estimate_has_no_send_surface(self):
        fake = FakeMT5()
        calc = MT5ResearchCalculator("terminal.exe", fake)
        estimate = calc.estimate(BrokerEstimateRequest(TradeSide.LONG, "EURUSD", 0.1, 1.10, 1.095))
        self.assertEqual(estimate.loss_at_stop, 25.0)
        self.assertEqual(estimate.required_margin, 42.0)
        self.assertFalse(calc.broker_write_authorized)
        self.assertFalse(hasattr(calc, "order_send"))
        self.assertEqual(fake.calls[-1], ("shutdown",))

    def test_trade_by_trade_parity(self):
        csv_text = (
            "strategy_hash,trade_id,entry_at,exit_at,side,volume,entry_price,exit_price,pnl\n"
            "abc,t1,2026-08-31T10:00:00+00:00,2026-08-31T11:00:00+00:00,long,0.1,1.1,1.11,100\n"
        )
        observed = parse_trade_parity_csv(csv_text)
        expected = (
            TradeParityRecord("abc", "t1", NOW, NOW + timedelta(hours=1), TradeSide.LONG, 0.1, 1.1, 1.11, 100.0),
        )
        assessment = reconcile_trade_parity(expected, observed)
        self.assertTrue(assessment.passed)
        self.assertEqual(assessment.matched, 1)

        bad = (TradeParityRecord("abc", "t1", NOW, NOW + timedelta(hours=1), TradeSide.LONG, 0.1, 1.1, 1.11, 80.0),)
        failed = reconcile_trade_parity(expected, bad)
        self.assertFalse(failed.passed)
        self.assertIn("trade:0:pnl_mismatch", failed.reasons)


if __name__ == "__main__":
    unittest.main()
