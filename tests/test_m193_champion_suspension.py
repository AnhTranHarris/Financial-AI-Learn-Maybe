from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.champion_registry import (
    ChampionLifecycleEvent,
    ChampionLifecycleEventType,
    ChampionLifecycleState,
    FrozenChampionRegistry,
    freeze_champion_record,
)
from dusty.champion_suspension import (
    ChampionSuspensionDecision,
    ChampionSuspensionPolicy,
    ForwardDrawdownEvidence,
    apply_automatic_suspension,
    evaluate_automatic_suspension,
)
from dusty.robustness_gate import RobustnessCertification, RobustnessGateStatus
from dusty.strategy_drift import StrategyDriftAssessment, StrategyDriftBaseline, StrategyDriftStatus
from dusty.strategy_v3 import FrozenStrategyDeployment


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 21, 0, tzinfo=UTC)
BASE_END = datetime(2026, 8, 1, tzinfo=UTC)
SOURCE_COMMIT = "1cb068e8948e0b143eb4bb94e0b44a2e038fc485"


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M193ChampionSuspensionTests(unittest.TestCase):
    def robustness(self) -> RobustnessCertification:
        return RobustnessCertification(
            RobustnessGateStatus.SERIOUS_CHALLENGER,
            (
                ("broker_calibration", "calibrated"),
                ("walk_forward", "pass_fraction=0.9"),
                ("parameter_neighborhood", "stable"),
                ("regime_torture", "passed"),
                ("cost_torture", "passed=True"),
                ("historical_forward_decay", "retention=0.75"),
                ("tail_risk", "dd=0.1;cvar=0.08"),
                ("strategy_dependency", "diversified"),
            ),
            (),
            tuple(fp(f"robustness-{index}") for index in range(8)),
        )

    def champion(self):
        robust = self.robustness()
        deployment = FrozenStrategyDeployment(
            fp("strategy"),
            fp("graph"),
            (fp("tool-1"), fp("tool-2")),
            "generation-1",
        )
        record = freeze_champion_record(
            lane_id="EURUSD:M15:breakout",
            strategy_family="breakout",
            deployment=deployment,
            source_commit=SOURCE_COMMIT,
            selection_evidence_fingerprint=fp("selection"),
            robustness=robust,
            forecast_integration=None,
            parent_champion_fingerprint=None,
            created_at=NOW,
        )
        return record, robust

    def register(self, registry: FrozenChampionRegistry):
        champion, robust = self.champion()
        registry.register(
            champion,
            robustness=robust,
            forecast_integration=None,
            actor_fingerprint=fp("registration-actor"),
            evidence_fingerprints=(champion.selection_evidence_fingerprint, champion.robustness_fingerprint),
            reason="M185 test registration",
        )
        return champion

    def baseline(self, champion) -> StrategyDriftBaseline:
        return StrategyDriftBaseline(
            champion.fingerprint,
            champion.deployment_fingerprint,
            champion.strategy_fingerprint,
            champion.robustness_fingerprint,
            fp("reference-data"),
            champion.source_commit,
            BASE_END,
            400,
            0.20,
            0.55,
            0.80,
            0.50,
            1.95,
            3600.0,
            (fp("walk-forward"), fp("oos")),
        )

    def drift(self, champion, baseline, status: StrategyDriftStatus) -> StrategyDriftAssessment:
        strategy: tuple[str, ...] = ()
        execution: tuple[str, ...] = ()
        data: tuple[str, ...] = ()
        if status is StrategyDriftStatus.WATCH:
            strategy = ("holding_time_distribution_shift",)
        elif status is StrategyDriftStatus.STRUCTURAL_DRIFT:
            strategy = ("expectancy_decay", "hit_rate_decay")
        elif status is StrategyDriftStatus.EXECUTION_DRIFT_ONLY:
            execution = ("actual_expectancy_below_PIT_replay",)
        elif status is StrategyDriftStatus.DATA_OR_REPLAY_DRIFT:
            data = ("forward_data_integrity_failure",)
        elif status is StrategyDriftStatus.GOVERNANCE_FAILURE:
            data = ("forward_rule_violation",)
        elif status is StrategyDriftStatus.INSUFFICIENT:
            data = ("insufficient_forward_PIT_replay",)
        return StrategyDriftAssessment(
            status,
            champion.fingerprint,
            baseline.fingerprint,
            None,
            None,
            strategy,
            execution,
            data,
            (fp(f"m192-{status.value}"),),
            fp("m192-policy"),
            f"M192 status {status.value}",
        )

    def drawdown(
        self,
        champion,
        baseline,
        drift: StrategyDriftAssessment,
        *,
        fraction: float = 0.05,
        count: int = 30,
        data_ok: bool = True,
        start: datetime | None = None,
        end: datetime | None = None,
        drift_fingerprint: str | None = None,
    ) -> ForwardDrawdownEvidence:
        period_start = start or (champion.created_at + timedelta(days=1))
        period_end = end or (champion.created_at + timedelta(days=30))
        return ForwardDrawdownEvidence(
            champion.fingerprint,
            baseline.fingerprint,
            drift_fingerprint or drift.fingerprint,
            period_start,
            period_end,
            count,
            fraction,
            data_ok,
            (fp("equity-series"), fp("deal-history")),
        )

    def policy(self, *, execution: bool = False, confirmations: int = 2) -> ChampionSuspensionPolicy:
        return ChampionSuspensionPolicy(0.15, 20, True, True, True, execution, confirmations)

    def evaluate(
        self,
        status: StrategyDriftStatus,
        *,
        drawdown_fraction: float = 0.05,
        drawdown_count: int = 30,
        data_ok: bool = True,
        execution_policy: bool = False,
        confirmations: tuple[str, ...] = (),
    ):
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        drift = self.drift(champion, baseline, status)
        dd = self.drawdown(
            champion,
            baseline,
            drift,
            fraction=drawdown_fraction,
            count=drawdown_count,
            data_ok=data_ok,
        )
        result = evaluate_automatic_suspension(
            champion,
            baseline,
            drift,
            dd,
            execution_confirmation_fingerprints=confirmations,
            policy=self.policy(execution=execution_policy),
            assessed_at=dd.period_end + timedelta(minutes=1),
        )
        return champion, baseline, drift, dd, result

    def test_stable_watch_and_insufficient_do_not_suspend_inside_drawdown_policy(self) -> None:
        for status in (StrategyDriftStatus.STABLE, StrategyDriftStatus.WATCH, StrategyDriftStatus.INSUFFICIENT):
            with self.subTest(status=status):
                *_, result = self.evaluate(status)
                self.assertEqual(result.decision, ChampionSuspensionDecision.KEEP_ACTIVE)
                self.assertFalse(result.reasons)

    def test_structural_data_and_governance_failures_suspend(self) -> None:
        cases = (
            (StrategyDriftStatus.STRUCTURAL_DRIFT, "structural_strategy_drift"),
            (StrategyDriftStatus.DATA_OR_REPLAY_DRIFT, "data_or_PIT_replay_integrity_failure"),
            (StrategyDriftStatus.GOVERNANCE_FAILURE, "forward_governance_failure"),
        )
        for status, reason in cases:
            with self.subTest(status=status):
                *_, result = self.evaluate(status)
                self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
                self.assertIn(reason, result.reasons)

    def test_drawdown_trigger_requires_depth_sample_and_integrity(self) -> None:
        *_, result = self.evaluate(StrategyDriftStatus.STABLE, drawdown_fraction=0.20)
        self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
        self.assertIn("forward_drawdown_exceeded", result.reasons)

        *_, result = self.evaluate(StrategyDriftStatus.STABLE, drawdown_fraction=0.20, drawdown_count=5)
        self.assertEqual(result.decision, ChampionSuspensionDecision.KEEP_ACTIVE)

        *_, result = self.evaluate(StrategyDriftStatus.STABLE, data_ok=False)
        self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
        self.assertIn("forward_drawdown_data_integrity_failure", result.reasons)

    def test_execution_only_drift_requires_explicit_policy_and_independent_confirmations(self) -> None:
        *_, result = self.evaluate(StrategyDriftStatus.EXECUTION_DRIFT_ONLY)
        self.assertEqual(result.decision, ChampionSuspensionDecision.KEEP_ACTIVE)

        *_, result = self.evaluate(
            StrategyDriftStatus.EXECUTION_DRIFT_ONLY,
            execution_policy=True,
            confirmations=(fp("execution-confirmation-1"),),
        )
        self.assertEqual(result.decision, ChampionSuspensionDecision.KEEP_ACTIVE)

        *_, result = self.evaluate(
            StrategyDriftStatus.EXECUTION_DRIFT_ONLY,
            execution_policy=True,
            confirmations=(fp("execution-confirmation-1"), fp("execution-confirmation-2")),
        )
        self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
        self.assertIn("confirmed_execution_mismatch", result.reasons)

    def test_execution_confirmation_evidence_is_unique_and_cannot_contaminate_other_drift(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        stable = self.drift(champion, baseline, StrategyDriftStatus.STABLE)
        dd = self.drawdown(champion, baseline, stable)
        with self.assertRaisesRegex(ValueError, "require M192 execution-drift evidence"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                stable,
                dd,
                execution_confirmation_fingerprints=(fp("unrelated-execution"),),
                policy=self.policy(),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )

        execution = self.drift(champion, baseline, StrategyDriftStatus.EXECUTION_DRIFT_ONLY)
        dd = self.drawdown(champion, baseline, execution)
        duplicate = fp("confirmation")
        with self.assertRaisesRegex(ValueError, "must be unique"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                execution,
                dd,
                execution_confirmation_fingerprints=(duplicate, duplicate),
                policy=self.policy(execution=True),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )

    def test_malformed_m192_status_signal_semantics_fail_closed(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        malformed = StrategyDriftAssessment(
            StrategyDriftStatus.STRUCTURAL_DRIFT,
            champion.fingerprint,
            baseline.fingerprint,
            None,
            None,
            (),
            (),
            (),
            (fp("evidence"),),
            fp("m192-policy"),
            "forged structural status without signals",
        )
        dd = self.drawdown(champion, baseline, malformed)
        with self.assertRaisesRegex(ValueError, "requires strategy signals"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                malformed,
                dd,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )

    def test_identity_and_cross_evidence_binding_fail_closed(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        drift = self.drift(champion, baseline, StrategyDriftStatus.STABLE)
        dd = self.drawdown(champion, baseline, drift)

        wrong_baseline = replace(baseline, champion_fingerprint=fp("other-champion"))
        with self.assertRaisesRegex(ValueError, "Champion/baseline identity drift"):
            evaluate_automatic_suspension(
                champion,
                wrong_baseline,
                drift,
                dd,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )

        mismatched = self.drawdown(champion, baseline, drift, drift_fingerprint=fp("other-drift"))
        with self.assertRaisesRegex(ValueError, "not bound to supplied M192"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                drift,
                mismatched,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=mismatched.period_end + timedelta(minutes=1),
            )

    def test_monitoring_window_must_be_after_baseline_and_champion_activation(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        drift = self.drift(champion, baseline, StrategyDriftStatus.STABLE)

        prebaseline = self.drawdown(
            champion,
            baseline,
            drift,
            start=BASE_END - timedelta(days=1),
            end=BASE_END + timedelta(days=2),
        )
        with self.assertRaisesRegex(ValueError, "strictly after certified baseline"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                drift,
                prebaseline,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=champion.created_at + timedelta(days=1),
            )

        preactivation = self.drawdown(
            champion,
            baseline,
            drift,
            start=BASE_END + timedelta(days=2),
            end=champion.created_at - timedelta(days=1),
        )
        with self.assertRaisesRegex(ValueError, "after Champion activation"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                drift,
                preactivation,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=champion.created_at + timedelta(days=1),
            )

        fresh = self.drawdown(champion, baseline, drift)
        with self.assertRaisesRegex(ValueError, "cannot predate drawdown evidence"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                drift,
                fresh,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=fresh.period_end - timedelta(seconds=1),
            )

    def test_keep_active_assessment_cannot_mutate_registry(self) -> None:
        registry = FrozenChampionRegistry()
        try:
            champion = self.register(registry)
            baseline = self.baseline(champion)
            drift = self.drift(champion, baseline, StrategyDriftStatus.STABLE)
            dd = self.drawdown(champion, baseline, drift)
            assessment = evaluate_automatic_suspension(
                champion,
                baseline,
                drift,
                dd,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )
            with self.assertRaisesRegex(ValueError, "KEEP_ACTIVE"):
                apply_automatic_suspension(registry, champion, assessment, actor_fingerprint=fp("m193-actor"))
            self.assertEqual(registry.state(champion.fingerprint), ChampionLifecycleState.ACTIVE)
        finally:
            registry.close()

    def test_apply_appends_one_irreversible_suspension_event_and_is_idempotent(self) -> None:
        registry = FrozenChampionRegistry()
        try:
            champion = self.register(registry)
            baseline = self.baseline(champion)
            drift = self.drift(champion, baseline, StrategyDriftStatus.STABLE)
            dd = self.drawdown(champion, baseline, drift, fraction=0.20)
            assessment = evaluate_automatic_suspension(
                champion,
                baseline,
                drift,
                dd,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )
            first = apply_automatic_suspension(registry, champion, assessment, actor_fingerprint=fp("m193-actor"))
            second = apply_automatic_suspension(registry, champion, assessment, actor_fingerprint=fp("m193-actor"))
            self.assertEqual(first, second)
            self.assertEqual(first.event_type, ChampionLifecycleEventType.SUSPENDED)
            self.assertEqual(registry.state(champion.fingerprint), ChampionLifecycleState.SUSPENDED)
            self.assertIn(assessment.fingerprint, first.evidence_fingerprints)
            self.assertTrue(registry.integrity_check()[0])
        finally:
            registry.close()

    def test_terminal_inactive_champion_cannot_be_suspended(self) -> None:
        registry = FrozenChampionRegistry()
        try:
            champion = self.register(registry)
            registry.append_lifecycle_event(
                ChampionLifecycleEvent(
                    champion.fingerprint,
                    ChampionLifecycleEventType.RETIRED,
                    fp("governance-actor"),
                    (fp("retirement-evidence"),),
                    "retired before M193",
                    champion.created_at + timedelta(minutes=1),
                )
            )
            baseline = self.baseline(champion)
            drift = self.drift(champion, baseline, StrategyDriftStatus.STABLE)
            dd = self.drawdown(champion, baseline, drift, fraction=0.20)
            assessment = evaluate_automatic_suspension(
                champion,
                baseline,
                drift,
                dd,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )
            with self.assertRaisesRegex(ValueError, "only ACTIVE Champion"):
                apply_automatic_suspension(registry, champion, assessment, actor_fingerprint=fp("m193-actor"))
            self.assertEqual(registry.state(champion.fingerprint), ChampionLifecycleState.RETIRED)
        finally:
            registry.close()

    def test_assessment_has_no_broker_position_risk_guardian_or_promotion_authority(self) -> None:
        *_, result = self.evaluate(StrategyDriftStatus.STRUCTURAL_DRIFT)
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.position_mutation_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)
        self.assertFalse(result.promotion_authority)


if __name__ == "__main__":
    unittest.main()
