from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.strategy_drift import (
    ExecutionQuality,
    ForwardTradeDriftObservation,
    ReplayQuality,
    StrategyDriftBaseline,
    StrategyDriftPolicy,
    StrategyDriftStatus,
    assess_strategy_drift,
)


UTC = timezone.utc
BASE_END = datetime(2026, 8, 1, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M192StrategyDriftTests(unittest.TestCase):
    def baseline(self) -> StrategyDriftBaseline:
        return StrategyDriftBaseline(
            champion_fingerprint=fp("champion"),
            deployment_fingerprint=fp("deployment"),
            strategy_fingerprint=fp("strategy"),
            robustness_fingerprint=fp("m174"),
            reference_data_fingerprint=fp("reference-data"),
            source_commit="a" * 40,
            period_end=BASE_END,
            observation_count=400,
            expectancy_r=0.20,
            hit_rate=0.55,
            average_win_r=0.80,
            average_loss_r=0.50,
            profit_factor=1.95,
            median_holding_seconds=3600.0,
            evidence_fingerprints=(fp("oos"), fp("walk-forward")),
        )

    def policy(self, *, structural: int = 2, warning: int = 1) -> StrategyDriftPolicy:
        return StrategyDriftPolicy(
            minimum_observations=10,
            warning_signal_count=warning,
            structural_signal_count=structural,
            maximum_expectancy_drop_fraction=0.25,
            maximum_hit_rate_drop=0.10,
            maximum_profit_factor_drop_fraction=0.30,
            maximum_average_win_drop_fraction=0.30,
            maximum_average_loss_increase_fraction=0.30,
            maximum_holding_time_ratio_deviation=0.50,
            maximum_replay_actual_expectancy_gap_r=0.10,
            maximum_execution_deviation_fraction=0.20,
        )

    def observation(
        self,
        index: int,
        *,
        replay_r: float,
        actual_r: float | None = None,
        replay_holding: float = 3600.0,
        actual_holding: float | None = None,
        execution: ExecutionQuality = ExecutionQuality.WITHIN_CALIBRATED,
        replay_quality: ReplayQuality = ReplayQuality.MATCHED,
        data_ok: bool = True,
        violations: int = 0,
        champion: str | None = None,
    ) -> ForwardTradeDriftObservation:
        if actual_r is None and execution is not ExecutionQuality.INCOMPLETE:
            actual_r = replay_r
        if actual_holding is None and actual_r is not None:
            actual_holding = replay_holding
        replay_value = None if replay_quality is ReplayQuality.INCOMPLETE else replay_r
        replay_duration = None if replay_quality is ReplayQuality.INCOMPLETE else replay_holding
        return ForwardTradeDriftObservation(
            champion_fingerprint=champion or fp("champion"),
            trade_fingerprint=fp(f"trade-{index}"),
            observed_at=BASE_END + timedelta(days=index + 1),
            replay_fingerprint=fp(f"replay-{index}"),
            replay_quality=replay_quality,
            replay_net_r=replay_value,
            replay_holding_seconds=replay_duration,
            actual_net_r=actual_r,
            actual_holding_seconds=actual_holding,
            execution_quality=execution,
            execution_evidence_fingerprint=None if execution is ExecutionQuality.INCOMPLETE else fp(f"m188-m189-{index}"),
            data_integrity_ok=data_ok,
            rule_violations=violations,
        )

    def healthy_rows(self) -> tuple[ForwardTradeDriftObservation, ...]:
        returns = (0.8, 0.8, -0.5, 0.8, -0.5, 0.8, -0.5, 0.8, -0.5, 0.8)
        return tuple(self.observation(index, replay_r=value) for index, value in enumerate(returns))

    def test_too_little_forward_evidence_is_insufficient_not_stable(self) -> None:
        result = assess_strategy_drift(self.baseline(), self.healthy_rows()[:5], policy=self.policy())
        self.assertEqual(result.status, StrategyDriftStatus.INSUFFICIENT)
        self.assertIn("insufficient_forward_PIT_replay", result.data_signals)

    def test_healthy_forward_replay_is_stable(self) -> None:
        first = assess_strategy_drift(self.baseline(), self.healthy_rows(), policy=self.policy())
        second = assess_strategy_drift(self.baseline(), reversed(self.healthy_rows()), policy=self.policy())
        self.assertEqual(first.status, StrategyDriftStatus.STABLE)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertFalse(first.strategy_signals)
        self.assertFalse(first.execution_signals)

    def test_one_metric_shift_produces_watch_not_structural_drift(self) -> None:
        rows = tuple(replace(row, replay_holding_seconds=8000.0, actual_holding_seconds=8000.0) for row in self.healthy_rows())
        result = assess_strategy_drift(self.baseline(), rows, policy=self.policy(structural=2))
        self.assertEqual(result.status, StrategyDriftStatus.WATCH)
        self.assertEqual(result.strategy_signals, ("holding_time_distribution_shift",))

    def test_multiple_independent_edge_signals_confirm_structural_drift(self) -> None:
        returns = (0.8, 0.8, 0.8, -0.7, -0.7, -0.7, -0.7, -0.7, -0.7, -0.7)
        rows = tuple(self.observation(index, replay_r=value) for index, value in enumerate(returns))
        result = assess_strategy_drift(self.baseline(), rows, policy=self.policy())
        self.assertEqual(result.status, StrategyDriftStatus.STRUCTURAL_DRIFT)
        self.assertGreaterEqual(len(result.strategy_signals), 2)
        self.assertIn("expectancy_decay", result.strategy_signals)
        self.assertIn("hit_rate_decay", result.strategy_signals)

    def test_bad_actual_fills_with_healthy_PIT_replay_are_execution_drift_only(self) -> None:
        replay = (0.8, 0.8, -0.5, 0.8, -0.5, 0.8, -0.5, 0.8, -0.5, 0.8)
        rows = tuple(
            self.observation(
                index,
                replay_r=value,
                actual_r=value - 0.35,
                execution=ExecutionQuality.DEVIATED if index < 5 else ExecutionQuality.WITHIN_CALIBRATED,
            )
            for index, value in enumerate(replay)
        )
        result = assess_strategy_drift(self.baseline(), rows, policy=self.policy())
        self.assertEqual(result.status, StrategyDriftStatus.EXECUTION_DRIFT_ONLY)
        self.assertFalse(result.strategy_signals)
        self.assertIn("execution_deviation_rate_above_policy", result.execution_signals)
        self.assertIn("actual_expectancy_below_PIT_replay", result.execution_signals)

    def test_bad_replay_remains_strategy_drift_even_when_execution_is_also_bad(self) -> None:
        returns = (0.5, 0.5, -0.8, -0.8, -0.8, -0.8, -0.8, -0.8, -0.8, -0.8)
        rows = tuple(
            self.observation(index, replay_r=value, actual_r=value - 0.2, execution=ExecutionQuality.DEVIATED)
            for index, value in enumerate(returns)
        )
        result = assess_strategy_drift(self.baseline(), rows, policy=self.policy())
        self.assertEqual(result.status, StrategyDriftStatus.STRUCTURAL_DRIFT)
        self.assertTrue(result.strategy_signals)
        self.assertTrue(result.execution_signals)

    def test_incomplete_execution_evidence_does_not_masquerade_as_edge_decay(self) -> None:
        rows = tuple(
            self.observation(
                index,
                replay_r=value,
                actual_r=None,
                actual_holding=None,
                execution=ExecutionQuality.INCOMPLETE,
            )
            for index, value in enumerate((0.8, 0.8, -0.5, 0.8, -0.5, 0.8, -0.5, 0.8, -0.5, 0.8))
        )
        result = assess_strategy_drift(self.baseline(), rows, policy=self.policy())
        self.assertEqual(result.status, StrategyDriftStatus.STABLE)
        self.assertIsNone(result.actual_metrics)
        self.assertFalse(result.execution_signals)

    def test_rule_violation_is_governance_failure_even_if_trade_won(self) -> None:
        rows = list(self.healthy_rows())
        rows[0] = replace(rows[0], rule_violations=1, replay_net_r=5.0, actual_net_r=5.0)
        result = assess_strategy_drift(self.baseline(), rows, policy=self.policy())
        self.assertEqual(result.status, StrategyDriftStatus.GOVERNANCE_FAILURE)
        self.assertIn("forward_rule_violation", result.data_signals)

    def test_data_integrity_failure_blocks_statistical_interpretation(self) -> None:
        rows = list(self.healthy_rows())
        rows[0] = replace(rows[0], data_integrity_ok=False)
        result = assess_strategy_drift(self.baseline(), rows, policy=self.policy())
        self.assertEqual(result.status, StrategyDriftStatus.DATA_OR_REPLAY_DRIFT)
        self.assertIn("forward_data_integrity_failure", result.data_signals)

    def test_diverged_frozen_PIT_replay_is_separate_from_strategy_edge_decay(self) -> None:
        rows = list(self.healthy_rows())
        rows[0] = replace(rows[0], replay_quality=ReplayQuality.DIVERGED)
        result = assess_strategy_drift(self.baseline(), rows, policy=self.policy())
        self.assertEqual(result.status, StrategyDriftStatus.DATA_OR_REPLAY_DRIFT)
        self.assertIn("frozen_strategy_PIT_replay_diverged", result.data_signals)

    def test_mixed_champion_identity_fails_closed(self) -> None:
        rows = list(self.healthy_rows())
        rows[-1] = replace(rows[-1], champion_fingerprint=fp("other-champion"))
        with self.assertRaisesRegex(ValueError, "Champion identity drift"):
            assess_strategy_drift(self.baseline(), rows, policy=self.policy())

    def test_duplicate_trade_or_timestamp_fails_closed(self) -> None:
        rows = list(self.healthy_rows())
        rows[-1] = replace(rows[-1], trade_fingerprint=rows[0].trade_fingerprint)
        with self.assertRaisesRegex(ValueError, "duplicate forward trade identity"):
            assess_strategy_drift(self.baseline(), rows, policy=self.policy())
        rows = list(self.healthy_rows())
        rows[-1] = replace(rows[-1], observed_at=rows[0].observed_at)
        with self.assertRaisesRegex(ValueError, "unique timestamps"):
            assess_strategy_drift(self.baseline(), rows, policy=self.policy())

    def test_observation_must_be_strictly_after_frozen_reference_period(self) -> None:
        row = replace(self.healthy_rows()[0], observed_at=BASE_END)
        with self.assertRaisesRegex(ValueError, "strictly after"):
            assess_strategy_drift(self.baseline(), (row,), policy=self.policy())

    def test_incomplete_replay_cannot_carry_replay_metrics(self) -> None:
        with self.assertRaisesRegex(ValueError, "incomplete PIT replay"):
            replace(self.healthy_rows()[0], replay_quality=ReplayQuality.INCOMPLETE)

    def test_policy_is_explicit_and_rejects_incoherent_thresholds(self) -> None:
        with self.assertRaisesRegex(ValueError, "structural_signal_count"):
            self.policy(structural=1, warning=2)
        first = self.policy()
        second = self.policy()
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_assessment_has_no_execution_suspension_or_governance_authority(self) -> None:
        result = assess_strategy_drift(self.baseline(), self.healthy_rows(), policy=self.policy())
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.position_mutation_authority)
        self.assertFalse(result.champion_suspension_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)
        self.assertFalse(result.provider_selection_authority)
        self.assertFalse(result.provider_weight_authority)


if __name__ == "__main__":
    unittest.main()
