from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.champion_registry import FrozenChampionRecord
from dusty.demo_execution_cost_learning import DemoCostLearningStatus
from dusty.single_desk_demo_certification import (
    CERTIFIED_PREREQUISITE_COMMITS,
    DemoDeskRuntimeEvidence,
    DemoEvidenceOrigin,
    DemoOperationalExerciseEvidence,
    DemoOperationalScenario,
    MilestoneBuildEvidence,
    REQUIRED_OPERATIONAL_SCENARIOS,
    SingleDeskDemoPolicy,
    SingleDeskDemoStatus,
    certify_single_demo_desk,
)
from dusty.strategy_drift import StrategyDriftStatus


UTC = timezone.utc
CREATED = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
RUN_START = CREATED + timedelta(minutes=1)
RUN_END = RUN_START + timedelta(days=2)
SOURCE_COMMIT = "a" * 40


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M194SingleDeskDemoCertificationTests(unittest.TestCase):
    def champion(self) -> FrozenChampionRecord:
        return FrozenChampionRecord(
            "EURUSD:M15:breakout",
            "generation-1",
            "breakout",
            fp("strategy"),
            fp("graph"),
            (fp("tool-1"), fp("tool-2")),
            "9" * 40,
            fp("selection"),
            fp("m174"),
            None,
            None,
            CREATED,
        )

    def prerequisites(self) -> tuple[MilestoneBuildEvidence, ...]:
        return tuple(
            MilestoneBuildEvidence(
                milestone,
                source_commit,
                fp(f"artifact-{milestone}"),
                fp(f"ci-{milestone}"),
                True,
            )
            for milestone, source_commit in CERTIFIED_PREREQUISITE_COMMITS
        )

    def policy(self) -> SingleDeskDemoPolicy:
        return SingleDeskDemoPolicy(
            minimum_runtime_seconds=86_400,
            minimum_heartbeats=100,
            minimum_completed_cycles=20,
            minimum_reconciled_executions=10,
            minimum_execution_cost_samples=30,
            minimum_recovery_checkpoints=3,
        )

    def runtime(self, champion: FrozenChampionRecord, **changes) -> DemoDeskRuntimeEvidence:
        row = DemoDeskRuntimeEvidence(
            origin=DemoEvidenceOrigin.REAL_DEMO_RUNTIME,
            desk_run_id="desk-run-001",
            lane_id=champion.lane_id,
            champion_fingerprint=champion.fingerprint,
            session_fingerprints=(fp("session-1"), fp("session-2")),
            final_session_fingerprint=fp("session-2"),
            broker_profile_fingerprint=fp("broker"),
            terminal_fingerprint=fp("terminal"),
            account_fingerprint=fp("account"),
            source_commit=SOURCE_COMMIT,
            started_at=RUN_START,
            ended_at=RUN_END,
            heartbeat_count=500,
            completed_cycles=60,
            reconciled_execution_count=40,
            unresolved_execution_count=0,
            execution_cost_sample_count=40,
            recovery_checkpoint_count=5,
            duplicate_action_count=0,
            unauthorized_broker_write_count=0,
            registry_integrity_ok=True,
            champion_active_at_end=True,
            final_session_demo_verified=True,
            final_session_latched=False,
            final_trade_permissions_ok=True,
            ledger_integrity_ok=True,
            artifact_integrity_ok=True,
            live_write_authorized=False,
            execution_cost_status=DemoCostLearningStatus.CALIBRATED,
            deterministic_core_operational=True,
            latest_drift_status=StrategyDriftStatus.STABLE,
            automatic_suspension_exercise_passed=True,
            champion_registry_fingerprint=fp("champion-registry"),
            session_verification_fingerprint=fp("final-session-verification"),
            ledger_fingerprint=fp("ledger"),
            artifact_vault_fingerprint=fp("vault"),
            execution_learning_fingerprint=fp("m189"),
            recovery_fingerprint=fp("m190"),
            provider_fleet_fingerprint=fp("m191"),
            drift_fingerprint=fp("m192"),
            suspension_fingerprint=fp("m193"),
            runtime_attestation_fingerprint=fp("runtime-attestation"),
        )
        return replace(row, **changes) if changes else row

    def exercises(self, runtime: DemoDeskRuntimeEvidence) -> tuple[DemoOperationalExerciseEvidence, ...]:
        rows = []
        cursor = runtime.started_at + timedelta(minutes=10)
        for index, scenario in enumerate(REQUIRED_OPERATIONAL_SCENARIOS):
            start = cursor + timedelta(minutes=index * 10)
            rows.append(
                DemoOperationalExerciseEvidence(
                    DemoEvidenceOrigin.CONTROLLED_DEMO_EXERCISE,
                    scenario,
                    runtime.desk_run_id,
                    SOURCE_COMMIT,
                    start,
                    start + timedelta(minutes=2),
                    True,
                    0,
                    0,
                    (fp(f"exercise-{scenario.value}"), fp(f"recovery-{scenario.value}")),
                )
            )
        return tuple(rows)

    def certify(
        self,
        *,
        runtime: DemoDeskRuntimeEvidence | None = None,
        prerequisites: tuple[MilestoneBuildEvidence, ...] | None = None,
        exercises: tuple[DemoOperationalExerciseEvidence, ...] | None = None,
    ):
        champion = self.champion()
        runtime_row = runtime or self.runtime(champion)
        exercise_rows = self.exercises(runtime_row) if exercises is None else exercises
        return certify_single_demo_desk(
            champion,
            self.prerequisites() if prerequisites is None else prerequisites,
            runtime_row,
            exercise_rows,
            policy=self.policy(),
            current_source_commit=SOURCE_COMMIT,
        )

    def test_complete_real_demo_evidence_can_certify_without_live_authority(self) -> None:
        result = self.certify()
        self.assertEqual(result.status, SingleDeskDemoStatus.CERTIFIED)
        self.assertFalse(result.pending_reasons)
        self.assertFalse(result.rejection_reasons)
        self.assertFalse(result.live_write_authorized)
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)
        self.assertFalse(result.promotion_authority)

    def test_ci_fixture_runtime_can_test_gate_but_cannot_certify_real_desk(self) -> None:
        champion = self.champion()
        runtime = self.runtime(champion, origin=DemoEvidenceOrigin.CI_FIXTURE)
        result = self.certify(runtime=runtime)
        self.assertEqual(result.status, SingleDeskDemoStatus.PENDING)
        self.assertIn("real_demo_runtime_evidence_required", result.pending_reasons)

    def test_missing_prerequisite_or_operational_exercise_remains_pending(self) -> None:
        prerequisites = tuple(row for row in self.prerequisites() if row.milestone != "M190")
        result = self.certify(prerequisites=prerequisites)
        self.assertEqual(result.status, SingleDeskDemoStatus.PENDING)
        self.assertIn("missing_prerequisite:M190", result.pending_reasons)

        champion = self.champion()
        runtime = self.runtime(champion)
        exercises = tuple(row for row in self.exercises(runtime) if row.scenario is not DemoOperationalScenario.AMBIGUOUS_SEND)
        result = self.certify(runtime=runtime, exercises=exercises)
        self.assertEqual(result.status, SingleDeskDemoStatus.PENDING)
        self.assertIn("missing_operational_exercise:ambiguous_send", result.pending_reasons)

    def test_failed_duplicate_or_wrong_lineage_prerequisite_rejects(self) -> None:
        rows = list(self.prerequisites())
        rows[0] = replace(rows[0], passed=False)
        result = self.certify(prerequisites=tuple(rows))
        self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
        self.assertIn("failed_prerequisite:M185", result.rejection_reasons)

        rows = list(self.prerequisites())
        rows.append(rows[0])
        result = self.certify(prerequisites=tuple(rows))
        self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
        self.assertIn("duplicate_prerequisite:M185", result.rejection_reasons)

        rows = list(self.prerequisites())
        rows[3] = replace(rows[3], source_commit="b" * 40)
        result = self.certify(prerequisites=tuple(rows))
        self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
        self.assertIn("prerequisite_source_commit_mismatch:M188", result.rejection_reasons)

    def test_depth_shortfalls_are_pending_not_false_failures(self) -> None:
        champion = self.champion()
        short = self.runtime(
            champion,
            ended_at=RUN_START + timedelta(hours=2),
            heartbeat_count=10,
            completed_cycles=2,
            reconciled_execution_count=1,
            execution_cost_sample_count=5,
            recovery_checkpoint_count=1,
        )
        result = self.certify(runtime=short, exercises=())
        self.assertEqual(result.status, SingleDeskDemoStatus.PENDING)
        self.assertIn("runtime_duration_insufficient", result.pending_reasons)
        self.assertIn("heartbeat_depth_insufficient", result.pending_reasons)
        self.assertIn("completed_cycle_depth_insufficient", result.pending_reasons)
        self.assertIn("reconciled_execution_depth_insufficient", result.pending_reasons)
        self.assertIn("execution_cost_sample_depth_insufficient", result.pending_reasons)
        self.assertIn("recovery_checkpoint_depth_insufficient", result.pending_reasons)

    def test_unresolved_duplicate_or_unauthorized_execution_is_rejected(self) -> None:
        champion = self.champion()
        cases = (
            ("unresolved_execution_count", 1, "unresolved_execution_state_present"),
            ("duplicate_action_count", 1, "duplicate_action_detected"),
            ("unauthorized_broker_write_count", 1, "unauthorized_broker_write_detected"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                runtime = self.runtime(champion, **{field: value})
                result = self.certify(runtime=runtime)
                self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
                self.assertIn(reason, result.rejection_reasons)

    def test_registry_champion_and_final_demo_session_must_end_valid(self) -> None:
        champion = self.champion()
        cases = (
            ("registry_integrity_ok", False, "M185_champion_registry_integrity_failed"),
            ("champion_active_at_end", False, "M185_champion_not_active_at_end"),
            ("final_session_demo_verified", False, "M187_final_session_not_verified_demo"),
            ("final_session_latched", True, "M187_final_session_is_latched"),
            ("final_trade_permissions_ok", False, "M187_final_trade_permissions_invalid"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                result = self.certify(runtime=self.runtime(champion, **{field: value}))
                self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
                self.assertIn(reason, result.rejection_reasons)

    def test_final_session_must_be_part_of_recorded_session_lineage(self) -> None:
        champion = self.champion()
        with self.assertRaisesRegex(ValueError, "final session must be present"):
            self.runtime(champion, final_session_fingerprint=fp("unseen-session"))

    def test_integrity_or_live_write_failure_is_rejected(self) -> None:
        champion = self.champion()
        cases = (
            ("ledger_integrity_ok", False, "execution_ledger_integrity_failed"),
            ("artifact_integrity_ok", False, "artifact_vault_integrity_failed"),
            ("live_write_authorized", True, "live_write_must_remain_false"),
            ("deterministic_core_operational", False, "M191_deterministic_core_not_operational"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                result = self.certify(runtime=self.runtime(champion, **{field: value}))
                self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
                self.assertIn(reason, result.rejection_reasons)

    def test_m189_m192_m193_runtime_constitution_is_fail_closed(self) -> None:
        champion = self.champion()
        result = self.certify(runtime=self.runtime(champion, execution_cost_status=DemoCostLearningStatus.INSUFFICIENT))
        self.assertEqual(result.status, SingleDeskDemoStatus.PENDING)
        self.assertIn("M189_demo_execution_costs_not_calibrated", result.pending_reasons)

        result = self.certify(runtime=self.runtime(champion, latest_drift_status=StrategyDriftStatus.WATCH))
        self.assertEqual(result.status, SingleDeskDemoStatus.PENDING)
        self.assertIn("M192_drift_not_clear:watch", result.pending_reasons)

        result = self.certify(runtime=self.runtime(champion, latest_drift_status=StrategyDriftStatus.STRUCTURAL_DRIFT))
        self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
        self.assertIn("M192_drift_breach:structural_drift", result.rejection_reasons)

        result = self.certify(runtime=self.runtime(champion, automatic_suspension_exercise_passed=False))
        self.assertEqual(result.status, SingleDeskDemoStatus.PENDING)
        self.assertIn("M193_automatic_suspension_path_not_exercised", result.pending_reasons)

    def test_runtime_identity_or_commit_drift_is_rejected(self) -> None:
        champion = self.champion()
        cases = (
            ("source_commit", "b" * 40, "runtime_source_commit_mismatch"),
            ("champion_fingerprint", fp("other-champion"), "runtime_champion_identity_mismatch"),
            ("lane_id", "GBPUSD:M15:breakout", "runtime_lane_identity_mismatch"),
        )
        for field, value, reason in cases:
            with self.subTest(field=field):
                result = self.certify(runtime=self.runtime(champion, **{field: value}))
                self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
                self.assertIn(reason, result.rejection_reasons)

    def test_operational_exercise_must_be_controlled_bound_safe_and_inside_run(self) -> None:
        champion = self.champion()
        runtime = self.runtime(champion)
        rows = list(self.exercises(runtime))
        target = rows[0]

        rows[0] = replace(target, passed=False)
        result = self.certify(runtime=runtime, exercises=tuple(rows))
        self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
        self.assertIn(f"operational_exercise_failed:{target.scenario.value}", result.rejection_reasons)

        rows = list(self.exercises(runtime))
        rows[0] = replace(target, unauthorized_broker_write_count=1)
        result = self.certify(runtime=runtime, exercises=tuple(rows))
        self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
        self.assertIn(f"exercise_unauthorized_broker_write:{target.scenario.value}", result.rejection_reasons)

        rows = list(self.exercises(runtime))
        rows[0] = replace(target, origin=DemoEvidenceOrigin.CI_FIXTURE)
        result = self.certify(runtime=runtime, exercises=tuple(rows))
        self.assertEqual(result.status, SingleDeskDemoStatus.PENDING)
        self.assertIn(f"controlled_demo_exercise_required:{target.scenario.value}", result.pending_reasons)

        rows = list(self.exercises(runtime))
        rows[0] = replace(target, ended_at=runtime.ended_at + timedelta(minutes=1))
        result = self.certify(runtime=runtime, exercises=tuple(rows))
        self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
        self.assertIn(f"exercise_outside_runtime_window:{target.scenario.value}", result.rejection_reasons)

    def test_duplicate_operational_scenario_rejects(self) -> None:
        champion = self.champion()
        runtime = self.runtime(champion)
        rows = list(self.exercises(runtime))
        rows.append(rows[0])
        result = self.certify(runtime=runtime, exercises=tuple(rows))
        self.assertEqual(result.status, SingleDeskDemoStatus.REJECTED)
        self.assertIn(f"duplicate_operational_exercise:{rows[0].scenario.value}", result.rejection_reasons)

    def test_certification_fingerprint_is_deterministic_under_input_ordering(self) -> None:
        champion = self.champion()
        runtime = self.runtime(champion)
        first = certify_single_demo_desk(
            champion,
            self.prerequisites(),
            runtime,
            self.exercises(runtime),
            policy=self.policy(),
            current_source_commit=SOURCE_COMMIT,
        )
        second = certify_single_demo_desk(
            champion,
            tuple(reversed(self.prerequisites())),
            runtime,
            tuple(reversed(self.exercises(runtime))),
            policy=self.policy(),
            current_source_commit=SOURCE_COMMIT,
        )
        self.assertEqual(first.certification_fingerprint, second.certification_fingerprint)


if __name__ == "__main__":
    unittest.main()
