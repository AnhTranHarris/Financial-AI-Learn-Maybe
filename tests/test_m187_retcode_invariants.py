from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.broker_deviation import BrokerDeviationPolicy, BrokerDeviationStatus, classify_broker_deviation
from dusty.cognition import CognitionAssessment, RoleJustification
from dusty.core import AnalystState, Cognition, GuardianState, PatienceState, SkepticState
from dusty.demo_execution import DemoExecutionResult
from dusty.execution_lifecycle import ExecutionState
from dusty.experience import TradeSide
from dusty.order_intent import OrderIntent
from dusty.shadow_trade import ObservedBrokerFill, build_shadow_trade, compare_shadow_to_fills
from dusty.strategy_v3 import OrderStyle


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 20, 30, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def shadow():
    cognition = Cognition(AnalystState.LONG, SkepticState.CLEAR, PatienceState.READY, GuardianState.NORMAL)
    assessment = CognitionAssessment(
        cognition,
        (
            RoleJustification("analyst", "long", ("entry_rules_met",)),
            RoleJustification("skeptic", "clear", ("clear",)),
            RoleJustification("patience", "ready", ("ready",)),
            RoleJustification("guardian", "normal", ("normal",)),
        ),
        fp("cognition"),
    )
    intent = OrderIntent(
        fp("strategy"), fp("session"), "EURUSD", TradeSide.LONG, 0.10,
        1.1000, 1.0950, 1.1100, 0.0025, 50.0, True, 1.0, True, True,
        NOW, NOW + timedelta(minutes=5), 0, order_style=OrderStyle.MARKET,
    )
    return build_shadow_trade(
        intent, assessment, recorded_at=NOW + timedelta(seconds=1), contract_size=100_000,
        spread_points=10, decision_latency_ms=10, stage="demo", shadow_reason="retcode test",
    )


class M187MetaQuotesRetcodeInvariantTests(unittest.TestCase):
    def test_success_states_cannot_carry_contradictory_mt5_retcodes(self) -> None:
        row = shadow()
        comparison = compare_shadow_to_fills(
            row,
            (ObservedBrokerFill(10, 20, NOW + timedelta(seconds=2), 0.10, 1.1001, fp("history")),),
            observed_at=NOW + timedelta(seconds=3),
        )
        policy = BrokerDeviationPolicy(1.0, 0.001, 2_000, 3_000)
        cases = (
            (ExecutionState.ACCEPTED, 10010, "accepted_state_conflicts_with_mt5_retcode"),
            (ExecutionState.PARTIAL, 10009, "partial_state_conflicts_with_mt5_retcode"),
            (ExecutionState.FILLED, 10010, "filled_state_conflicts_with_mt5_retcode"),
            (ExecutionState.REJECTED, 10009, "failure_state_conflicts_with_mt5_success_retcode"),
        )
        for state, retcode, reason in cases:
            with self.subTest(state=state, retcode=retcode):
                result = classify_broker_deviation(
                    row,
                    comparison,
                    DemoExecutionResult(row.intent_hash, state, retcode, 20, 10, "synthetic"),
                    policy=policy,
                )
                self.assertEqual(result.status, BrokerDeviationStatus.INCONSISTENT)
                self.assertIn(reason, result.reasons)

    def test_placed_and_done_are_both_valid_accepted_server_outcomes_without_fill_history_yet(self) -> None:
        row = shadow()
        comparison = compare_shadow_to_fills(row, (), observed_at=NOW + timedelta(seconds=3))
        policy = BrokerDeviationPolicy(1.0, 0.001, 2_000, 3_000)
        for retcode in (10008, 10009):
            with self.subTest(retcode=retcode):
                result = classify_broker_deviation(
                    row,
                    comparison,
                    DemoExecutionResult(row.intent_hash, ExecutionState.ACCEPTED, retcode, 20, 0, "accepted"),
                    policy=policy,
                )
                self.assertEqual(result.status, BrokerDeviationStatus.INCOMPLETE)
                self.assertIn("broker_history_fill_evidence_missing", result.reasons)


if __name__ == "__main__":
    unittest.main()
