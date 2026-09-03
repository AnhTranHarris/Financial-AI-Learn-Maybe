from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp
from dusty.runtime import RuntimeBar, compile_strategy, generate_runtime_trades
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2


class ElapsedHoldingTests(unittest.TestCase):
    def strategy(self, *, elapsed: int | None) -> object:
        return compile_strategy(
            StrategySpecV2(
                strategy_id="elapsed-hold-test",
                direction=TradeSide.LONG,
                entry_groups=(RuleGroup((Clause("close", RuleOp.GT, 0.0),)),),
                exit_plan=ExitPlan(
                    "pct:0.5",
                    target_rule="off",
                    max_hold_steps=16,
                    max_elapsed_minutes=elapsed,
                ),
                decision_timeframe_minutes=15,
                intended_horizon_minutes=240,
            )
        )

    @staticmethod
    def bar(at: datetime, price: float = 1.10) -> RuntimeBar:
        return RuntimeBar.of(
            at,
            open=price,
            high=price + 0.001,
            low=price - 0.001,
            close=price,
            features={"close": price},
            execution_price=price,
        )

    def test_weekend_gap_exits_only_at_first_observed_bar_after_elapsed_ceiling(self) -> None:
        friday = datetime(2026, 8, 14, 23, 0, tzinfo=UTC)
        monday = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)
        bars = (
            self.bar(friday),
            self.bar(friday + timedelta(minutes=15), 1.101),
            self.bar(friday + timedelta(minutes=30), 1.102),
            self.bar(monday, 1.103),
        )

        trades = generate_runtime_trades(self.strategy(elapsed=240), bars)

        self.assertEqual(len(trades), 1)
        trade = trades[0]
        self.assertEqual(trade.entry_at, friday)
        self.assertEqual(trade.exit_at, monday)
        self.assertEqual(trade.exit_reason, "max_elapsed_hold")
        self.assertEqual(trade.exit_price, 1.103)
        self.assertEqual(trade.exit_at - trade.entry_at, timedelta(hours=53))
        self.assertNotIn(
            trade.exit_at,
            (friday + timedelta(hours=4), friday + timedelta(minutes=16 * 15)),
        )

    def test_continuous_m15_data_preserves_existing_max_hold_reason(self) -> None:
        start = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
        bars = tuple(self.bar(start + timedelta(minutes=15 * index), 1.10 + index * 0.0001) for index in range(17))

        trade = generate_runtime_trades(self.strategy(elapsed=240), bars)[0]

        self.assertEqual(trade.exit_at, start + timedelta(hours=4))
        self.assertEqual(trade.exit_reason, "max_hold")

    def test_elapsed_ceiling_is_explicit_and_legacy_step_semantics_remain_available(self) -> None:
        friday = datetime(2026, 8, 14, 23, 0, tzinfo=UTC)
        monday = datetime(2026, 8, 17, 4, 0, tzinfo=UTC)
        bars = (self.bar(friday), self.bar(monday, 1.103))

        self.assertEqual(generate_runtime_trades(self.strategy(elapsed=None), bars), ())
        self.assertNotEqual(
            self.strategy(elapsed=None).strategy_hash,
            self.strategy(elapsed=240).strategy_hash,
        )

    def test_elapsed_ceiling_rejects_nonpositive_and_boolean_values(self) -> None:
        for value in (0, -1, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "max_elapsed_minutes"):
                ExitPlan("pct:0.5", max_elapsed_minutes=value)


if __name__ == "__main__":
    unittest.main()
