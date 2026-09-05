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
        strategy = ()
        execution = ()
        data = ()
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
        *,
        fraction: float = 0.05,
        count: int = 30,
        data_ok: bool = True,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> ForwardDrawdownEvidence:
        period_start = start or (BASE_END + timedelta(days=1))
        period_end = end or (BASE_END + timedelta(days=30))
        return ForwardDrawdownEvidence(
            champion.fingerprint,
            baseline.fingerprint,
            period_start,
            period_end,
            count,
            fraction,
            data_ok,
            (fp("equity-series"), fp("deal-history")),
        )

    def policy(
        self,
        *,
        structural: bool = True,
        data: bool = True,
        governance: bool = True,
        execution: bool = False,
        confirmations: int = 2,
    ) -> ChampionSuspensionPolicy:
        return ChampionSuspensionPolicy(
            0.15,
            20,
            structural,
            data,
            governance,
            execution,
            confirmations,
        )

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
        dd = self.drawdown(champion, baseline, fraction=drawdown_fraction, count=drawdown_count, data_ok=data_ok)
        result = evaluate_automatic_suspension(
            champion,
            baseline,
            self.drift(champion, baseline, status),
            dd,
            execution_confirmation_fingerprints=confirmations,
            policy=self.policy(execution=execution_policy),
            assessed_at=dd.period_end + timedelta(minutes=1),
        )
        return champion, baseline, dd, result

    def test_stable_watch_and_insufficient_do_not_suspend_inside_drawdown_policy(self) -> None:
        for status in (StrategyDriftStatus.STABLE, StrategyDriftStatus.WATCH, StrategyDriftStatus.INSUFFICIENT):
            with self.subTest(status=status):
                _, _, _, result = self.evaluate(status)
                self.assertEqual(result.decision, ChampionSuspensionDecision.KEEP_ACTIVE)
                self.assertFalse(result.reasons)

    def test_structural_drift_suspends_under_precommitted_policy(self) -> None:
        _, _, _, result = self.evaluate(StrategyDriftStatus.STRUCTURAL_DRIFT)
        self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
        self.assertIn("structural_strategy_drift", result.reasons)

    def test_data_or_replay_failure_and_governance_failure_suspend(self) -> None:
        for status, reason in (
            (StrategyDriftStatus.DATA_OR_REPLAY_DRIFT, "data_or_PIT_replay_integrity_failure"),
            (StrategyDriftStatus.GOVERNANCE_FAILURE, "forward_governance_failure"),
        ):
            with self.subTest(status=status):
                _, _, _, result = self.evaluate(status)
                self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
                self.assertIn(reason, result.reasons)

    def test_excessive_forward_drawdown_suspends_even_when_m192_is_stable(self) -> None:
        _, _, _, result = self.evaluate(StrategyDriftStatus.STABLE, drawdown_fraction=0.20)
        self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
        self.assertIn("forward_drawdown_exceeded", result.reasons)

    def test_large_drawdown_with_too_few_observations_does_not_become_false_alarm(self) -> None:
        _, _, _, result = self.evaluate(StrategyDriftStatus.STABLE, drawdown_fraction=0.20, drawdown_count=5)
        self.assertEqual(result.decision, ChampionSuspensionDecision.KEEP_ACTIVE)

    def test_drawdown_data_integrity_failure_suspends_without_interpreting_performance(self) -> None:
        _, _, _, result = self.evaluate(StrategyDriftStatus.STABLE, data_ok=False)
        self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
        self.assertIn("forward_drawdown_data_integrity_failure", result.reasons)

    def test_execution_only_drift_requires_explicit_policy_and_confirmations(self) -> None:
        _, _, _, result = self.evaluate(StrategyDriftStatus.EXECUTION_DRIFT_ONLY)
        self.assertEqual(result.decision, ChampionSuspensionDecision.KEEP_ACTIVE)
        _, _, _, result = self.evaluate(
            StrategyDriftStatus.EXECUTION_DRIFT_ONLY,
            execution_policy=True,
            confirmations=(fp("execution-confirmation-1"),),
        )
        self.assertEqual(result.decision, ChampionSuspensionDecision.KEEP_ACTIVE)
        _, _, _, result = self.evaluate(
            StrategyDriftStatus.EXECUTION_DRIFT_ONLY,
            execution_policy=True,
            confirmations=(fp("execution-confirmation-1"), fp("execution-confirmation-2")),
        )
        self.assertEqual(result.decision, ChampionSuspensionDecision.SUSPEND)
        self.assertIn("confirmed_execution_mismatch", result.reasons)

    def test_execution_confirmation_evidence_cannot_be_attached_to_nonexecution_drift(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        dd = self.drawdown(champion, baseline)
        with self.assertRaisesRegex(ValueError, "require M192 execution-drift evidence"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                self.drift(champion, baseline, StrategyDriftStatus.STABLE),
                dd,
                execution_confirmation_fingerprints=(fp("unrelated-execution"),),
                policy=self.policy(),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )

    def test_duplicate_execution_confirmations_fail_closed(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        dd = self.drawdown(champion, baseline)
        duplicated = fp("confirmation")
        with self.assertRaisesRegex(ValueError, "must be unique"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                self.drift(champion, baseline, StrategyDriftStatus.EXECUTION_DRIFT_ONLY),
                dd,
                execution_confirmation_fingerprints=(duplicated, duplicated),
                policy=self.policy(execution=True),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )

    def test_malformed_m192_status_signal_semantics_fail_closed(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        dd = self.drawdown(champion, baseline)
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

    def test_champion_and_baseline_identity_drift_fail_closed(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        dd = self.drawdown(champion, baseline)
        wrong = replace(baseline, champion_fingerprint=fp("other-champion"))
        with self.assertRaisesRegex(ValueError, "Champion/baseline identity drift"):
            evaluate_automatic_suspension(
                champion,
                wrong,
                self.drift(champion, baseline, StrategyDriftStatus.STABLE),
                dd,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=dd.period_end + timedelta(minutes=1),
            )

    def test_drawdown_must_be_forward_and_assessment_cannot_predate_evidence(self) -> None:
        champion, _ = self.champion()
        baseline = self.baseline(champion)
        stale = self.drawdown(
            champion,
            baseline,
            start=BASE_END - timedelta(days=1),
            end=BASE_END + timedelta(days=2),
        )
        with self.assertRaisesRegex(ValueError, "begin strictly after"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                self.drift(champion, baseline, StrategyDriftStatus.STABLE),
                stale,
                execution_confirmation_fingerprints=(),
                policy=self.policy(),
                assessed_at=stale.period_end + timedelta(minutes=1),
            )
        fresh = self.drawdown(champion, baseline)
        with self.assertRaisesRegex(ValueError, "cannot predate"):
            evaluate_automatic_suspension(
                champion,
                baseline,
                self.drift(champion, baseline, StrategyDriftStatus.STABLE),
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
            dd = self.drawdown(champion, baseline)
            assessment = evaluate_automatic_suspension(
                champion,
                baseline,
                self.drift(champion, baseline, StrategyDriftStatus.STABLE),
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
            dd = self.drawdown(champion, baseline, fraction=0.20)
            assessment = evaluate_automatic_suspension(
                champion,
                baseline,
                self.drift(champion, baseline, StrategyDriftStatus.STABLE),
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
            self.assertIn(assessment.policy_fingerprint, first.evidence_fingerprints)
            self.assertTrue(registry.integrity_check()[0])
        finally:
            registry.close()

    def test_terminal_inactive_champion_cannot_be_suspended(self) -> None:
        registry = FrozenChampionRegistry()
        try:
            champion = self.register(registry)
            retired = ChampionLifecycleEvent(
                champion.fingerprint,
                ChampionLifecycleEventType.RETIRED,
                fp("governance-actor"),
                (fp("retirement-evidence"),),
                "retired before M193",
                NOW + timedelta(minutes=1),
            )
            registry.append_lifecycle_event(retired)
            baseline = self.baseline(champion)
            dd = self.drawdown(champion, baseline, fraction=0.20)
            assessment = evaluate_automatic_suspension(
                champion,
                baseline,
                self.drift(champion, baseline, StrategyDriftStatus.STABLE),
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
        _, _, _, result = self.evaluate(StrategyDriftStatus.STRUCTURAL_DRIFT)
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.position_mutation_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)
        self.assertFalse(result.promotion_authority)


if __name__ == "__main__":
    unittest.main()
