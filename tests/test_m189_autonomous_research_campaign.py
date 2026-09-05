from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from dusty.autonomous_research_campaign import (
    AutonomousCampaignManifest,
    CampaignStatus,
    SQLiteAutonomousCampaignStore,
    advance_campaign,
    cancel_campaign,
    start_campaign,
)
from dusty.experiment_manifest import EvaluationStage
from dusty.research_brain import ResearchSchool
from dusty.research_loop_governor import LoopState, ResearchLoopRecord
from dusty.strategy_family import ExhaustionSignal


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 21, 0, tzinfo=UTC)
SOURCE_COMMIT = "77e666ab3aa1ef7974887c5cc4e4965d6da04545"


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M189AutonomousResearchCampaignTests(unittest.TestCase):
    def manifest(self, **changes: object) -> AutonomousCampaignManifest:
        values = dict(
            campaign_id="campaign-1",
            constitution_fingerprint=fp("constitution"),
            context_fingerprint=fp("context"),
            source_commit=SOURCE_COMMIT,
            maximum_steps=20,
            maximum_experiments=10,
            maximum_resource_seconds=1_000.0,
            maximum_stagnant_steps=3,
        )
        values.update(changes)
        return AutonomousCampaignManifest(**values)

    def loop(
        self,
        *,
        state: LoopState = LoopState.PROPOSED,
        iteration: int = 0,
        outcome: str | None = None,
        updated_at: datetime = NOW,
        loop_id: str | None = None,
    ) -> ResearchLoopRecord:
        return ResearchLoopRecord(
            loop_id or fp("loop"),
            fp("root-manifest"),
            fp("active-manifest"),
            fp("root-execution"),
            fp("active-execution"),
            fp("active-subject"),
            fp("family"),
            EvaluationStage.A1_EDGE,
            state,
            iteration,
            (),
            fp(outcome) if outcome is not None else None,
            ExhaustionSignal.NONE,
            NOW - timedelta(hours=1),
            updated_at,
        )

    def test_manifest_freezes_a1_a2_a3_order_and_source_identity(self) -> None:
        manifest = self.manifest()
        self.assertEqual(
            manifest.schools,
            (ResearchSchool.A1_EDGE, ResearchSchool.A2_PROFITABILITY, ResearchSchool.A3_VELOCITY),
        )
        self.assertFalse(manifest.broker_write_authority)
        with self.assertRaisesRegex(ValueError, "A1 -> A2 -> A3"):
            self.manifest(schools=(ResearchSchool.A2_PROFITABILITY, ResearchSchool.A1_EDGE, ResearchSchool.A3_VELOCITY))
        with self.assertRaisesRegex(ValueError, "source commit"):
            self.manifest(source_commit="not-a-commit")

    def test_start_is_deterministic_research_only_and_terminal_loop_stays_terminal(self) -> None:
        manifest = self.manifest()
        first = start_campaign(manifest, self.loop(), now=NOW)
        second = start_campaign(manifest, self.loop(), now=NOW)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.status, CampaignStatus.ACTIVE)
        self.assertFalse(first.broker_write_authority)
        self.assertFalse(first.promotion_authority)
        self.assertFalse(first.risk_override_authority)
        self.assertFalse(first.guardian_override_authority)
        exhausted = start_campaign(manifest, self.loop(state=LoopState.GRAVEYARD), now=NOW)
        self.assertEqual(exhausted.status, CampaignStatus.EXHAUSTED)

    def test_resume_manifest_loop_iteration_and_time_drift_fail_closed(self) -> None:
        manifest = self.manifest()
        previous = start_campaign(manifest, self.loop(), now=NOW)
        with self.assertRaisesRegex(ValueError, "manifest identity drift"):
            advance_campaign(
                self.manifest(context_fingerprint=fp("new-context")),
                previous,
                self.loop(updated_at=NOW + timedelta(seconds=1)),
                action_fingerprint=fp("action"),
                now=NOW + timedelta(seconds=1),
            )
        with self.assertRaisesRegex(ValueError, "research-loop identity drift"):
            advance_campaign(
                manifest,
                previous,
                self.loop(loop_id=fp("other-loop"), updated_at=NOW + timedelta(seconds=1)),
                action_fingerprint=fp("action"),
                now=NOW + timedelta(seconds=1),
            )
        prior = replace(previous, loop_iteration=2)
        with self.assertRaisesRegex(ValueError, "iteration regressed"):
            advance_campaign(
                manifest,
                prior,
                self.loop(iteration=1, updated_at=NOW + timedelta(seconds=1)),
                action_fingerprint=fp("action"),
                now=NOW + timedelta(seconds=1),
            )
        with self.assertRaisesRegex(ValueError, "predates durable"):
            advance_campaign(
                manifest,
                previous,
                self.loop(updated_at=NOW - timedelta(seconds=1)),
                action_fingerprint=fp("action"),
                now=NOW + timedelta(seconds=1),
            )

    def test_campaign_cannot_recount_experiment_or_result_evidence(self) -> None:
        manifest = self.manifest()
        previous = start_campaign(manifest, self.loop(), now=NOW)
        step = advance_campaign(
            manifest,
            previous,
            self.loop(iteration=1, updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("action-1"),
            completed_experiment_fingerprints=(fp("experiment-1"),),
            result_fingerprints=(fp("result-1"),),
            now=NOW + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValueError, "recount completed experiment"):
            advance_campaign(
                manifest,
                step,
                self.loop(iteration=2, updated_at=NOW + timedelta(seconds=2)),
                action_fingerprint=fp("action-2"),
                completed_experiment_fingerprints=(fp("experiment-1"),),
                now=NOW + timedelta(seconds=2),
            )
        with self.assertRaisesRegex(ValueError, "recount result evidence"):
            advance_campaign(
                manifest,
                step,
                self.loop(iteration=2, updated_at=NOW + timedelta(seconds=2)),
                action_fingerprint=fp("action-2"),
                result_fingerprints=(fp("result-1"),),
                now=NOW + timedelta(seconds=2),
            )

    def test_school_pass_requires_exact_new_m160_outcome(self) -> None:
        manifest = self.manifest()
        previous = start_campaign(manifest, self.loop(), now=NOW)
        with self.assertRaisesRegex(ValueError, "PASSED_STAGE"):
            advance_campaign(
                manifest,
                previous,
                self.loop(state=LoopState.TESTING, outcome="a1-outcome", updated_at=NOW + timedelta(seconds=1)),
                action_fingerprint=fp("pass-a1"),
                result_fingerprints=(fp("a1-outcome"),),
                school_passed=True,
                now=NOW + timedelta(seconds=1),
            )
        with self.assertRaisesRegex(ValueError, "exact new M160 outcome"):
            advance_campaign(
                manifest,
                previous,
                self.loop(state=LoopState.PASSED_STAGE, outcome="a1-outcome", updated_at=NOW + timedelta(seconds=1)),
                action_fingerprint=fp("pass-a1"),
                result_fingerprints=(fp("unrelated-result"),),
                school_passed=True,
                now=NOW + timedelta(seconds=1),
            )

    def test_a1_a2_a3_progression_requires_three_distinct_m160_outcomes(self) -> None:
        manifest = self.manifest()
        checkpoint = start_campaign(manifest, self.loop(), now=NOW)
        for index, label in enumerate(("a1-outcome", "a2-outcome", "a3-outcome"), start=1):
            checkpoint = advance_campaign(
                manifest,
                checkpoint,
                self.loop(
                    state=LoopState.PASSED_STAGE,
                    iteration=index,
                    outcome=label,
                    updated_at=NOW + timedelta(seconds=index),
                ),
                action_fingerprint=fp(f"pass-{index}"),
                result_fingerprints=(fp(label),),
                school_passed=True,
                now=NOW + timedelta(seconds=index),
            )
        self.assertEqual(checkpoint.school_index, 2)
        self.assertEqual(checkpoint.status, CampaignStatus.COMPLETE)
        self.assertEqual(checkpoint.reason, "a1_a2_a3_campaign_complete")
        with self.assertRaisesRegex(ValueError, "only active campaigns"):
            advance_campaign(
                manifest,
                checkpoint,
                self.loop(state=LoopState.PASSED_STAGE, iteration=4, outcome="a4", updated_at=NOW + timedelta(seconds=4)),
                action_fingerprint=fp("extra"),
                result_fingerprints=(fp("a4"),),
                school_passed=True,
                now=NOW + timedelta(seconds=4),
            )

    def test_same_m160_outcome_cannot_be_reused_to_advance_next_school(self) -> None:
        manifest = self.manifest()
        previous = start_campaign(manifest, self.loop(), now=NOW)
        first = advance_campaign(
            manifest,
            previous,
            self.loop(state=LoopState.PASSED_STAGE, iteration=1, outcome="shared", updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("pass-a1"),
            result_fingerprints=(fp("shared"),),
            school_passed=True,
            now=NOW + timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ValueError, "recount result evidence"):
            advance_campaign(
                manifest,
                first,
                self.loop(state=LoopState.PASSED_STAGE, iteration=2, outcome="shared", updated_at=NOW + timedelta(seconds=2)),
                action_fingerprint=fp("pass-a2"),
                result_fingerprints=(fp("shared"),),
                school_passed=True,
                now=NOW + timedelta(seconds=2),
            )

    def test_stagnation_counts_no_progress_even_when_action_ids_churn(self) -> None:
        manifest = self.manifest(maximum_stagnant_steps=2)
        checkpoint = start_campaign(manifest, self.loop(), now=NOW)
        checkpoint = advance_campaign(
            manifest,
            checkpoint,
            self.loop(updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("action-a"),
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(checkpoint.stagnant_steps, 1)
        self.assertEqual(checkpoint.status, CampaignStatus.ACTIVE)
        checkpoint = advance_campaign(
            manifest,
            checkpoint,
            self.loop(updated_at=NOW + timedelta(seconds=2)),
            action_fingerprint=fp("action-b"),
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(checkpoint.stagnant_steps, 2)
        self.assertEqual(checkpoint.status, CampaignStatus.PAUSED)
        self.assertEqual(checkpoint.reason, "campaign_stagnation_detected")

    def test_real_progress_resets_stagnation(self) -> None:
        manifest = self.manifest(maximum_stagnant_steps=3)
        checkpoint = start_campaign(manifest, self.loop(), now=NOW)
        checkpoint = advance_campaign(
            manifest,
            checkpoint,
            self.loop(updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("idle"),
            now=NOW + timedelta(seconds=1),
        )
        checkpoint = advance_campaign(
            manifest,
            checkpoint,
            self.loop(iteration=1, updated_at=NOW + timedelta(seconds=2)),
            action_fingerprint=fp("experiment"),
            completed_experiment_fingerprints=(fp("experiment"),),
            result_fingerprints=(fp("result"),),
            now=NOW + timedelta(seconds=2),
        )
        self.assertEqual(checkpoint.stagnant_steps, 0)
        self.assertEqual(checkpoint.status, CampaignStatus.ACTIVE)

    def test_exact_budget_boundary_pauses_and_overshoot_is_refused(self) -> None:
        step_manifest = self.manifest(maximum_steps=1)
        start = start_campaign(step_manifest, self.loop(), now=NOW)
        ended = advance_campaign(
            step_manifest,
            start,
            self.loop(updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("one-step"),
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(ended.status, CampaignStatus.PAUSED)
        self.assertEqual(ended.reason, "campaign_step_budget_exhausted")

        experiment_manifest = self.manifest(maximum_experiments=1)
        start = start_campaign(experiment_manifest, self.loop(), now=NOW)
        ended = advance_campaign(
            experiment_manifest,
            start,
            self.loop(iteration=1, updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("one-exp"),
            completed_experiment_fingerprints=(fp("exp-1"),),
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(ended.status, CampaignStatus.PAUSED)
        self.assertEqual(ended.reason, "campaign_experiment_budget_exhausted")
        with self.assertRaisesRegex(ValueError, "experiment budget"):
            advance_campaign(
                self.manifest(maximum_experiments=1),
                start,
                self.loop(iteration=1, updated_at=NOW + timedelta(seconds=1)),
                action_fingerprint=fp("two-exp"),
                completed_experiment_fingerprints=(fp("exp-1"), fp("exp-2")),
                now=NOW + timedelta(seconds=1),
            )

        resource_manifest = self.manifest(maximum_resource_seconds=10.0)
        start = start_campaign(resource_manifest, self.loop(), now=NOW)
        ended = advance_campaign(
            resource_manifest,
            start,
            self.loop(updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("resource"),
            resource_seconds_delta=10.0,
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(ended.status, CampaignStatus.PAUSED)
        self.assertEqual(ended.reason, "campaign_resource_budget_exhausted")
        with self.assertRaisesRegex(ValueError, "resource budget"):
            advance_campaign(
                resource_manifest,
                start,
                self.loop(updated_at=NOW + timedelta(seconds=1)),
                action_fingerprint=fp("resource-over"),
                resource_seconds_delta=10.01,
                now=NOW + timedelta(seconds=1),
            )

    def test_m160_exhaustion_stops_campaign_without_mutating_governor(self) -> None:
        manifest = self.manifest()
        previous = start_campaign(manifest, self.loop(), now=NOW)
        result = advance_campaign(
            manifest,
            previous,
            self.loop(state=LoopState.EXHAUSTED, updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("observe-exhaustion"),
            now=NOW + timedelta(seconds=1),
        )
        self.assertEqual(result.status, CampaignStatus.EXHAUSTED)
        self.assertEqual(result.reason, "m160_research_loop_exhausted")

    def test_cancel_is_explicit_same_step_terminal_checkpoint(self) -> None:
        manifest = self.manifest()
        previous = start_campaign(manifest, self.loop(), now=NOW)
        cancelled = cancel_campaign(manifest, previous, reason="operator requested stop", now=NOW + timedelta(seconds=1))
        self.assertEqual(cancelled.step_index, previous.step_index)
        self.assertEqual(cancelled.status, CampaignStatus.CANCELLED)
        with self.assertRaisesRegex(ValueError, "terminal campaign"):
            cancel_campaign(manifest, cancelled, reason="again", now=NOW + timedelta(seconds=2))

    def test_store_is_append_only_idempotent_and_manifest_bound(self) -> None:
        manifest = self.manifest()
        first = start_campaign(manifest, self.loop(), now=NOW)
        second = advance_campaign(
            manifest,
            first,
            self.loop(iteration=1, updated_at=NOW + timedelta(seconds=1)),
            action_fingerprint=fp("work"),
            completed_experiment_fingerprints=(fp("exp"),),
            result_fingerprints=(fp("result"),),
            now=NOW + timedelta(seconds=1),
        )
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteAutonomousCampaignStore(Path(folder) / "campaign.sqlite")
            try:
                store.append(first)
                store.append(first)
                store.append(second)
                self.assertEqual(store.latest(manifest), second)
                self.assertTrue(store.integrity_ok())
                with self.assertRaisesRegex(ValueError, "manifest identity drift"):
                    store.latest(self.manifest(context_fingerprint=fp("different-context")))
            finally:
                store.close()

    def test_store_refuses_time_step_and_terminal_history_regression(self) -> None:
        manifest = self.manifest()
        first = start_campaign(manifest, self.loop(), now=NOW)
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteAutonomousCampaignStore(Path(folder) / "campaign.sqlite")
            try:
                store.append(first)
                with self.assertRaisesRegex(ValueError, "time regression"):
                    store.append(replace(first, step_index=1, created_at=NOW - timedelta(seconds=1)))
                with self.assertRaisesRegex(ValueError, "same-step append"):
                    store.append(replace(first, reason="different same step", created_at=NOW + timedelta(seconds=1)))
                cancelled = cancel_campaign(manifest, first, reason="stop", now=NOW + timedelta(seconds=2))
                store.append(cancelled)
                with self.assertRaisesRegex(ValueError, "terminal checkpoint"):
                    store.append(replace(cancelled, step_index=1, created_at=NOW + timedelta(seconds=3), status=CampaignStatus.ACTIVE, reason="illegal resurrection"))
            finally:
                store.close()

    def test_store_detects_payload_and_fingerprint_tampering(self) -> None:
        manifest = self.manifest()
        first = start_campaign(manifest, self.loop(), now=NOW)
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteAutonomousCampaignStore(Path(folder) / "campaign.sqlite")
            try:
                store.append(first)
                store._db.execute(
                    "UPDATE autonomous_campaign_checkpoints SET payload=? WHERE campaign_id=?",
                    ("{}", manifest.campaign_id),
                )
                store._db.commit()
                self.assertFalse(store.integrity_ok())
                with self.assertRaisesRegex(RuntimeError, "payload integrity"):
                    store.latest(manifest)
            finally:
                store.close()

        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteAutonomousCampaignStore(Path(folder) / "campaign.sqlite")
            try:
                store.append(first)
                store._db.execute(
                    "UPDATE autonomous_campaign_checkpoints SET checkpoint_fingerprint=? WHERE campaign_id=?",
                    (fp("tampered-checkpoint-fingerprint"), manifest.campaign_id),
                )
                store._db.commit()
                self.assertFalse(store.integrity_ok())
                with self.assertRaisesRegex(RuntimeError, "fingerprint integrity"):
                    store.latest(manifest)
            finally:
                store.close()

    def test_cancelled_checkpoint_can_be_persisted_at_same_step_but_not_resumed(self) -> None:
        manifest = self.manifest()
        first = start_campaign(manifest, self.loop(), now=NOW)
        cancelled = cancel_campaign(manifest, first, reason="manual stop", now=NOW + timedelta(seconds=1))
        with tempfile.TemporaryDirectory() as folder:
            store = SQLiteAutonomousCampaignStore(Path(folder) / "campaign.sqlite")
            try:
                store.append(first)
                store.append(cancelled)
                self.assertEqual(store.latest(manifest).status, CampaignStatus.CANCELLED)
                with self.assertRaisesRegex(ValueError, "only active campaigns"):
                    advance_campaign(
                        manifest,
                        cancelled,
                        self.loop(updated_at=NOW + timedelta(seconds=2)),
                        action_fingerprint=fp("resume"),
                        now=NOW + timedelta(seconds=2),
                    )
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
