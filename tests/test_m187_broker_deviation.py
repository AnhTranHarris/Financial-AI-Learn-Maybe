from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.broker_deviation import (
    BrokerDeviationPolicy,
    BrokerDeviationStatus,
    classify_broker_deviation,
)
from dusty.cognition import CognitionAssessment, RoleJustification
from dusty.core import AnalystState, Cognition, GuardianState, PatienceState, SkepticState
from dusty.demo_execution import DemoExecutionResult
from dusty.execution_lifecycle import ExecutionState
from dusty.experience import TradeSide
from dusty.order_intent import OrderIntent
from dusty.shadow_trade import ObservedBrokerFill, build_shadow_trade, compare_shadow_to_fills
from dusty.strategy_v3 import OrderStyle


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 20, 0, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M187BrokerDeviationClassifierTests(unittest.TestCase):
    def cognition(self) -> CognitionAssessment:
        cognition = Cognition(AnalystState.LONG, SkepticState.CLEAR, PatienceState.READY, GuardianState.NORMAL)
        reasons = (
            RoleJustification("analyst", "long", ("entry_rules_met",)),
            RoleJustification("skeptic", "clear", ("no_material_counterevidence",)),
            RoleJustification("patience", "ready", ("setup_temporally_ready",)),
            RoleJustification("guardian", "normal", ("execution_and_risk_normal",)),
        )
        return CognitionAssessment(cognition, reasons, fp("cognition"))

    def shadow(self):
        intent = OrderIntent(
            fp("strategy"),
            fp("session"),
            "EURUSD",
            TradeSide.LONG,
            0.10,
            1.1000,
            1.0950,
            1.1100,
            0.0025,
            50.0,
            True,
            1.0,
            True,
            True,
            NOW,
            NOW + timedelta(minutes=5),
            0,
            order_style=OrderStyle.MARKET,
        )
        return build_shadow_trade(
            intent,
            self.cognition(),
            recorded_at=NOW + timedelta(seconds=1),
            contract_size=100_000,
            spread_points=10,
            decision_latency_ms=20,
            stage="demo",
            shadow_reason="pre-send capture",
        )

    def fill(self, deal: int, *, order: int = 700, seconds: float = 2.0, volume: float = 0.10, price: float = 1.1002):
        return ObservedBrokerFill(
            deal,
            order,
            NOW + timedelta(seconds=seconds),
            volume,
            price,
            fp("broker-history"),
        )

    def policy(
        self,
        *,
        minimum_fill: float = 1.0,
        max_slippage: float = 0.001,
        first_ms: float = 2_000,
        last_ms: float = 3_000,
    ) -> BrokerDeviationPolicy:
        return BrokerDeviationPolicy(minimum_fill, max_slippage, first_ms, last_ms)

    def comparison(self, fills=(), *, observed_seconds: float = 5.0):
        return compare_shadow_to_fills(
            self.shadow(),
            fills,
            observed_at=NOW + timedelta(seconds=observed_seconds),
        )

    def test_full_fill_within_explicit_policy(self) -> None:
        shadow = self.shadow()
        comparison = compare_shadow_to_fills(
            shadow,
            (self.fill(101),),
            observed_at=NOW + timedelta(seconds=5),
        )
        execution = DemoExecutionResult(shadow.intent_hash, ExecutionState.FILLED, 10009, 700, 101, "done")
        result = classify_broker_deviation(shadow, comparison, execution, policy=self.policy())
        self.assertEqual(result.status, BrokerDeviationStatus.WITHIN_POLICY)
        self.assertEqual(result.reasons, ("observed_execution_within_explicit_policy",))
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.retry_authority)
        self.assertFalse(result.position_mutation_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_partial_fill_below_policy_is_deviation_not_invented_rejection(self) -> None:
        shadow = self.shadow()
        comparison = compare_shadow_to_fills(
            shadow,
            (self.fill(201, volume=0.04),),
            observed_at=NOW + timedelta(seconds=5),
        )
        execution = DemoExecutionResult(shadow.intent_hash, ExecutionState.PARTIAL, 10010, 700, 201, "partial")
        result = classify_broker_deviation(shadow, comparison, execution, policy=self.policy(minimum_fill=0.80))
        self.assertEqual(result.status, BrokerDeviationStatus.DEVIATED)
        self.assertIn("fill_fraction_below_policy", result.reasons)

    def test_adverse_slippage_and_latency_are_independent_policy_deviations(self) -> None:
        shadow = self.shadow()
        comparison = compare_shadow_to_fills(
            shadow,
            (self.fill(301, seconds=4.5, price=1.1020),),
            observed_at=NOW + timedelta(seconds=6),
        )
        execution = DemoExecutionResult(shadow.intent_hash, ExecutionState.FILLED, 10009, 700, 301, "done")
        result = classify_broker_deviation(
            shadow,
            comparison,
            execution,
            policy=self.policy(max_slippage=0.0005, first_ms=2_000, last_ms=3_000),
        )
        self.assertEqual(result.status, BrokerDeviationStatus.DEVIATED)
        self.assertIn("adverse_slippage_above_policy", result.reasons)
        self.assertIn("first_fill_latency_above_policy", result.reasons)
        self.assertIn("last_fill_latency_above_policy", result.reasons)

    def test_favorable_slippage_does_not_trigger_adverse_limit(self) -> None:
        shadow = self.shadow()
        comparison = compare_shadow_to_fills(
            shadow,
            (self.fill(401, price=1.0995),),
            observed_at=NOW + timedelta(seconds=5),
        )
        execution = DemoExecutionResult(shadow.intent_hash, ExecutionState.FILLED, 10009, 700, 401, "done")
        result = classify_broker_deviation(shadow, comparison, execution, policy=self.policy(max_slippage=0.0))
        self.assertEqual(result.status, BrokerDeviationStatus.WITHIN_POLICY)
        self.assertLess(result.adverse_slippage_fraction or 0.0, 0.0)

    def test_rejected_send_without_fills_is_broker_failure(self) -> None:
        shadow = self.shadow()
        comparison = compare_shadow_to_fills(shadow, (), observed_at=NOW + timedelta(seconds=5))
        execution = DemoExecutionResult(shadow.intent_hash, ExecutionState.REJECTED, 10006, 0, 0, "rejected")
        result = classify_broker_deviation(shadow, comparison, execution, policy=self.policy())
        self.assertEqual(result.status, BrokerDeviationStatus.BROKER_FAILURE)
        self.assertIn("execution_rejected", result.reasons)
        self.assertIn("retcode:10006", result.reasons)

    def test_accepted_or_filled_state_without_history_fill_is_incomplete(self) -> None:
        for state, retcode, deal in (
            (ExecutionState.ACCEPTED, 10008, 0),
            (ExecutionState.FILLED, 10009, 501),
            (ExecutionState.PARTIAL, 10010, 502),
        ):
            with self.subTest(state=state):
                shadow = self.shadow()
                comparison = compare_shadow_to_fills(shadow, (), observed_at=NOW + timedelta(seconds=5))
                execution = DemoExecutionResult(shadow.intent_hash, state, retcode, 700, deal, "server-result")
                result = classify_broker_deviation(shadow, comparison, execution, policy=self.policy())
                self.assertEqual(result.status, BrokerDeviationStatus.INCOMPLETE)
                self.assertIn("broker_history_fill_evidence_missing", result.reasons)

    def test_rejected_server_state_with_observed_deal_is_inconsistent(self) -> None:
        shadow = self.shadow()
        comparison = compare_shadow_to_fills(shadow, (self.fill(601),), observed_at=NOW + timedelta(seconds=5))
        execution = DemoExecutionResult(shadow.intent_hash, ExecutionState.REJECTED, 10006, 700, 0, "rejected")
        result = classify_broker_deviation(shadow, comparison, execution, policy=self.policy())
        self.assertEqual(result.status, BrokerDeviationStatus.INCONSISTENT)
        self.assertIn("failure_state_conflicts_with_observed_fill", result.reasons)

    def test_order_ticket_and_returned_deal_must_match_history(self) -> None:
        shadow = self.shadow()
        comparison = compare_shadow_to_fills(shadow, (self.fill(701, order=900),), observed_at=NOW + timedelta(seconds=5))
        execution = DemoExecutionResult(shadow.intent_hash, ExecutionState.FILLED, 10009, 700, 702, "done")
        result = classify_broker_deviation(shadow, comparison, execution, policy=self.policy())
        self.assertEqual(result.status, BrokerDeviationStatus.INCONSISTENT)
        self.assertIn("broker_history_order_ticket_mismatch", result.reasons)
        self.assertIn("returned_deal_ticket_missing_from_history", result.reasons)

    def test_execution_state_must_agree_with_observed_fill_fraction(self) -> None:
        shadow = self.shadow()
        full = compare_shadow_to_fills(shadow, (self.fill(801),), observed_at=NOW + timedelta(seconds=5))
        partial_state = DemoExecutionResult(shadow.intent_hash, ExecutionState.PARTIAL, 10010, 700, 801, "partial")
        result = classify_broker_deviation(shadow, full, partial_state, policy=self.policy())
        self.assertEqual(result.status, BrokerDeviationStatus.INCONSISTENT)
        self.assertIn("partial_state_conflicts_with_full_fill_history", result.reasons)

        partial = compare_shadow_to_fills(shadow, (self.fill(802, volume=0.04),), observed_at=NOW + timedelta(seconds=5))
        full_state = DemoExecutionResult(shadow.intent_hash, ExecutionState.FILLED, 10009, 700, 802, "done")
        result = classify_broker_deviation(shadow, partial, full_state, policy=self.policy(minimum_fill=0.1))
        self.assertEqual(result.status, BrokerDeviationStatus.INCONSISTENT)
        self.assertIn("filled_state_conflicts_with_partial_history", result.reasons)

    def test_shadow_comparison_and_execution_identity_drift_fail_closed(self) -> None:
        shadow = self.shadow()
        other_shadow = self.shadow()
        comparison = compare_shadow_to_fills(shadow, (self.fill(901),), observed_at=NOW + timedelta(seconds=5))
        bad_execution = DemoExecutionResult(fp("different-intent"), ExecutionState.FILLED, 10009, 700, 901, "done")
        with self.assertRaisesRegex(ValueError, "execution intent identity drift"):
            classify_broker_deviation(shadow, comparison, bad_execution, policy=self.policy())

        # A structurally identical independently-built shadow has the same fingerprint;
        # make the comparison belong to a genuinely different intent instead.
        different_intent = OrderIntent(
            fp("different-strategy"), fp("session"), "EURUSD", TradeSide.LONG, 0.10,
            1.1000, 1.0950, 1.1100, 0.0025, 50.0, True, 1.0, True, True,
            NOW, NOW + timedelta(minutes=5), 0, order_style=OrderStyle.MARKET,
        )
        different_shadow = build_shadow_trade(
            different_intent, self.cognition(), recorded_at=NOW + timedelta(seconds=1),
            contract_size=100_000, spread_points=10, decision_latency_ms=20,
            stage="demo", shadow_reason="other",
        )
        different_comparison = compare_shadow_to_fills(different_shadow, (), observed_at=NOW + timedelta(seconds=5))
        good_execution = DemoExecutionResult(shadow.intent_hash, ExecutionState.ACCEPTED, 10008, 700, 0, "placed")
        with self.assertRaisesRegex(ValueError, "intent identity drift"):
            classify_broker_deviation(shadow, different_comparison, good_execution, policy=self.policy())
        self.assertEqual(other_shadow.fingerprint, shadow.fingerprint)

    def test_policy_is_explicit_bounded_and_deterministic(self) -> None:
        with self.assertRaises(ValueError):
            BrokerDeviationPolicy(-0.1, 0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            BrokerDeviationPolicy(1.1, 0.0, 0.0, 0.0)
        with self.assertRaises(ValueError):
            BrokerDeviationPolicy(1.0, -0.1, 0.0, 0.0)
        with self.assertRaises(ValueError):
            BrokerDeviationPolicy(1.0, 0.0, 2_000, 1_000)
        first = self.policy()
        second = self.policy()
        self.assertEqual(first.fingerprint, second.fingerprint)


if __name__ == "__main__":
    unittest.main()
