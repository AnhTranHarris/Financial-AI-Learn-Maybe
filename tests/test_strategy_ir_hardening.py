from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import (
    EligibilityStatus,
    ExitPlan,
    RuleGroup,
    StrategySpecV2,
    assess_observed_entry_frequency,
)


T0 = datetime(2026, 8, 31, 10, 0, tzinfo=UTC)


class StrategyIRHardeningTests(unittest.TestCase):
    def test_commutative_or_group_order_does_not_create_fake_new_strategy(self) -> None:
        a = RuleGroup((Clause("trend", RuleOp.EQ, "up"),))
        b = RuleGroup((Clause("vol", RuleOp.GE, 2.0),))
        first = StrategySpecV2(
            "first",
            TradeSide.LONG,
            (a, b),
            ExitPlan("stop"),
            5,
            30,
        )
        second = StrategySpecV2(
            "second",
            TradeSide.LONG,
            (b, a, a),
            ExitPlan("stop"),
            5,
            30,
        )
        self.assertEqual(first.strategy_hash, second.strategy_hash)

    def test_observed_entry_frequency_catches_accidental_machine_scalping(self) -> None:
        too_fast = tuple(T0 + timedelta(minutes=10 * index) for index in range(4))
        result = assess_observed_entry_frequency(too_fast)
        self.assertIs(result.status, EligibilityStatus.PROHIBITED)
        self.assertIn("entry_frequency_prohibited", result.reasons)

        acceptable = (T0, T0 + timedelta(minutes=20), T0 + timedelta(minutes=40))
        self.assertIs(
            assess_observed_entry_frequency(acceptable).status,
            EligibilityStatus.ALLOWED,
        )


if __name__ == "__main__":
    unittest.main()
