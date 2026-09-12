from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from dusty.experience import TradeSide
from dusty.mt5worker import MT5Bar
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.m166_research_identity import (
    M166ResearchIdentity,
    dataset_fingerprint,
    dataset_payload,
    parameter_fingerprint,
)


UTC = timezone.utc


def spec(value: float = 30.0) -> StrategySpecV2:
    return StrategySpecV2(
        strategy_id="m166-test",
        direction=TradeSide.BUY,
        entry_groups=(RuleGroup((Clause("rsi", RuleOp.GT, value),)),),
        exit_plan=ExitPlan("atr:2", "rr:2", max_hold_steps=8),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
        cooldown_steps=1,
    )


def bars() -> tuple[MT5Bar, ...]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    return tuple(
        MT5Bar(start + timedelta(minutes=15 * i), 1.10 + i * 0.001, 1.11 + i * 0.001,
               1.09 + i * 0.001, 1.105 + i * 0.001, 100 + i, 2, 0)
        for i in range(4)
    )


class M166ResearchIdentityTests(unittest.TestCase):
    def test_parameter_identity_is_deterministic_and_changes_with_tuning(self) -> None:
        self.assertEqual(parameter_fingerprint(spec()), parameter_fingerprint(spec()))
        self.assertNotEqual(parameter_fingerprint(spec()), parameter_fingerprint(spec(31.0)))
        self.assertNotEqual(parameter_fingerprint(spec()), spec().strategy_hash)

    def test_dataset_identity_is_ordered_and_content_addressed(self) -> None:
        rows = bars()
        first = dataset_fingerprint(symbol="EURUSD", timeframe="M15", bars=rows)
        self.assertEqual(first, dataset_fingerprint(symbol="eurusd", timeframe="m15", bars=rows))
        changed = list(rows)
        row = changed[-1]
        changed[-1] = MT5Bar(row.at, row.open, row.high, row.low, row.close + 0.0001, row.tick_volume, row.spread, row.real_volume)
        self.assertNotEqual(first, dataset_fingerprint(symbol="EURUSD", timeframe="M15", bars=changed))

    def test_dataset_rejects_nonchronological_or_invalid_ohlc(self) -> None:
        rows = bars()
        with self.assertRaises(ValueError):
            dataset_payload(symbol="EURUSD", timeframe="M15", bars=(rows[1], rows[0]))
        bad = MT5Bar(rows[0].at, 1.10, 1.09, 1.08, 1.10, 1, 1, 0)
        with self.assertRaises(ValueError):
            dataset_payload(symbol="EURUSD", timeframe="M15", bars=(bad,))

    def test_identity_is_explicit_and_authority_free(self) -> None:
        rows = bars()
        meta = dataset_payload(symbol="EURUSD", timeframe="M15", bars=rows)
        identity = M166ResearchIdentity(
            "eurusd:m15:test",
            spec().strategy_hash,
            dataset_fingerprint(symbol="EURUSD", timeframe="M15", bars=rows),
            parameter_fingerprint(spec()),
            meta,
        )
        payload = identity.payload
        self.assertEqual(payload["strategy_fingerprint"], spec().strategy_hash)
        self.assertFalse(payload["authority"]["broker_write"])
        self.assertFalse(payload["authority"]["research_execution"])
        self.assertEqual(len(identity.fingerprint), 64)


if __name__ == "__main__":
    unittest.main()
