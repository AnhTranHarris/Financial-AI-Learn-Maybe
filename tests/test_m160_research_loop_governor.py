from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from dusty.controlled_evolution import (
    EvolutionAction,
    EvolutionDecision,
    ExperimentOutcome,
    ExperimentOutcomeType,
    InfrastructureFailureKind,
)
from dusty.experiment_manifest import (
    BrokerAssumptions,
    ComputeRequest,
    EvaluationPlan,
    EvaluationStage,
    ExperimentManifest,
    ExperimentWindow,
    FeatureRef,
    ManifestOrigin,
)
from dusty.experiment_queue import ExperimentResource
from dusty.research_loop_governor import (
    GovernorAction,
    GovernorDecision,
    LoopState,
    ReopenChangeKind,
    ReopenEvidence,
    ResearchCandidate,
    SQLiteResearchLoopStore,
    admission_queue_spec,
    archive_graveyard,
    assess_reopen,
    govern_outcome,
    rank_candidates,
    review_exhaustion_warning,
)
from dusty.strategy_family import ExhaustionAssessment, ExhaustionSignal


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _manifest(label: str = "root", **overrides: object) -> ExperimentManifest:
    values: dict[str, object] = {
        "experiment_id": f"DD-M160-{label}",
        "hypothesis_id": f"HYP-{label}",
        "hypothesis": "Bounded research-governor test.",
        "origin": ManifestOrigin.DUSTY,
        "proposal_fingerprint": _fp(f"proposal-{label}"),
        "strategy_fingerprint": _fp(f"strategy-{label}"),
        "variant_fingerprint": _fp(f"variant-{label}"),
        "context_fingerprint": _fp(f"context-{label}"),
        "strategy_ancestry_fingerprints": (),
        "source_provenance_fingerprints": (_fp("source"),),
        "parent_manifest_fingerprints": (),
        "software_commit": "b" * 40,
        "dataset_fingerprint": _fp("dataset"),
        "features": (FeatureRef("rsi_14", "v1", _fp("rsi-14-v1")),),
        "broker": BrokerAssumptions(
            profile_fingerprint=_fp("broker"),
            cost_model_fingerprint=_fp("cost"),
            account_currency="USD",
            initial_balance=10_000.0,
            leverage=100,
            execution_model="research_only",
        ),
        "seed": 160,
        "windows": (
            ExperimentWindow(
                "development",
                datetime(2024, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
        ),
        "symbols": ("EURUSD",),
        "timeframes": ("M15",),
        "research_school": "edge_discovery",
        "fidelity": "python_screen",
        "evaluation": EvaluationPlan(
            stage=EvaluationStage.A1,
            policy_fingerprint=_fp("a1-policy"),
            required_metrics=("expectancy", "trade_count"),
            minimum_trades=30,
            walk_forward_required=False,
            cost_stress_required=False,
        ),
        "risk_policy_fingerprint": _fp("risk-policy"),
        "risk_assumptions": (("risk_mode", "research_only"),),
        "compute": ComputeRequest(
            resource=ExperimentResource.CPU_RESEARCH,
            max_wall_seconds=120,
            max_ram_mb=1024,
            max_workers=1,
            gpu_allowed=False,
        ),
        "expected_outputs": ("metrics.json",),
        "created_at": datetime(2026, 9, 5, 1, 55, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ExperimentManifest(**values)  # type: ignore[arg-type]


def _candidate(label: str, *, value: float, cost: float, age: int, sequence: int) -> ResearchCandidate:
    return ResearchCandidate(
        manifest_fingerprint=_fp(f"manifest-{label}"),
        execution_fingerprint=_fp(f"execution-{label}"),
        family_fingerprint=_fp(f"family-{label}"),
        resource=ExperimentResource.CPU_RESEARCH,
        state=LoopState.PROPOSED,
        expected_information_gain=value,
        failure_resolution_probability=value,
        novelty=value,
        strategic_value=value,
        normalized_compute_cost=cost,
        age_steps=age,
        admission_sequence=sequence,
    )


def _exhaustion(signal: ExhaustionSignal) -> ExhaustionAssessment:
    return ExhaustionAssessment(
        signal=signal,
        research_attempts=12,
        mutation_axes=3,
        recent_mean_novelty=0.02,
        recent_mean_improvement=0.01,
        recent_failure_fraction=1.0,
        dominant_failure_fraction=1.0,
        dominant_failure_mechanism="entry",
        policy_version="test",
        reasons=("test",),
    )


class M160ResearchLoopGovernorTests(unittest.TestCase):
    def test_scheduler_is_bounded_cost_aware_and_aging_prevents_permanent_starvation(self) -> None:
        fresh_high = _candidate("fresh-high", value=0.80, cost=0.10, age=0, sequence=2)
        stale_medium = _candidate("stale-medium", value=0.70, cost=0.30, age=20, sequence=1)
        ranked = rank_candidates((fresh_high, stale_medium))
        self.assertEqual(ranked[0].candidate, stale_medium)
        self.assertLessEqual(ranked[0].score, 1.0)
        self.assertGreater(ranked[0].age_bonus, 0.0)

    def test_scheduler_rejects_duplicate_execution_even_with_different_manifest(self) -> None:
        first = _candidate("a", value=0.5, cost=0.5, age=0, sequence=1)
        second = replace(
            _candidate("b", value=0.6, cost=0.4, age=0, sequence=2),
            execution_fingerprint=first.execution_fingerprint,
        )
        with self.assertRaisesRegex(ValueError, "duplicate execution"):
            rank_candidates((first, second))

    def test_admission_uses_stable_queue_priority_not_dynamic_scheduler_score(self) -> None:
        manifest = _manifest()
        spec = admission_queue_spec(manifest, symbol="eurusd", timeframe="m15", max_attempts=2)
        self.assertEqual(spec.priority, 0)
        self.assertEqual(spec.max_attempts, 2)
        self.assertEqual(spec.context_fingerprint, manifest.fingerprint)

    def test_store_registers_idempotently_and_survives_reopen(self) -> None:
        now = datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m160.sqlite"
            store = SQLiteResearchLoopStore(path)
            first = store.register(
                manifest_fingerprint=_fp("manifest"),
                execution_fingerprint=_fp("execution"),
                subject_fingerprint=_fp("subject"),
                family_fingerprint=_fp("family"),
                stage=EvaluationStage.A1,
                now=now,
            )
            same = store.register(
                manifest_fingerprint=_fp("manifest"),
                execution_fingerprint=_fp("execution"),
                subject_fingerprint=_fp("subject"),
                family_fingerprint=_fp("family"),
                stage=EvaluationStage.A1,
                now=now,
            )
            self.assertEqual(first, same)
            self.assertTrue(store.integrity_ok())
            store.close()

            reopened = SQLiteResearchLoopStore(path)
            recovered = reopened.snapshot(first.loop_fingerprint)
            self.assertEqual(recovered, first)
            self.assertTrue(reopened.integrity_ok())
            reopened.close()

    def test_infrastructure_failure_retries_exact_execution_without_state_or_identity_drift(self) -> None:
        now = datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)
        store = SQLiteResearchLoopStore()
        record = store.register(
            manifest_fingerprint=_fp("manifest"),
            execution_fingerprint=_fp("execution"),
            subject_fingerprint=_fp("subject"),
            family_fingerprint=_fp("family"),
            stage=EvaluationStage.A1,
            now=now,
        )
        record = store.admit(record.loop_fingerprint, now=now + timedelta(seconds=1))
        outcome = ExperimentOutcome(
            record.active_subject_fingerprint,
            ExperimentOutcomeType.INFRASTRUCTURE_FAILED,
            "provider process failed",
            (_fp("provider-log"),),
            InfrastructureFailureKind.PROVIDER,
        )
        evolution = EvolutionDecision(
            EvolutionAction.RETRY_EXACT,
            record.active_subject_fingerprint,
            outcome.fingerprint,
            "retry exact execution",
            exact_retry_execution_fingerprint=record.active_execution_fingerprint,
        )
        decision = govern_outcome(record, outcome, evolution, _exhaustion(ExhaustionSignal.NONE))
        self.assertEqual(decision.action, GovernorAction.RETRY_EXACT)
        self.assertEqual(decision.from_state, decision.to_state)
        after = store.apply(record.loop_fingerprint, decision, now=now + timedelta(seconds=2), evidence_fingerprints=outcome.evidence_fingerprints)
        self.assertEqual(after.active_execution_fingerprint, record.active_execution_fingerprint)
        self.assertEqual(after.state, LoopState.TESTING)
        store.close()

    def test_challenger_handoff_preserves_root_identity_and_retests_new_active_identity(self) -> None:
        now = datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)
        store = SQLiteResearchLoopStore()
        record = store.register(
            manifest_fingerprint=_fp("manifest-root"),
            execution_fingerprint=_fp("execution-root"),
            subject_fingerprint=_fp("subject-root"),
            family_fingerprint=_fp("family"),
            stage=EvaluationStage.A1,
            now=now,
        )
        record = store.admit(record.loop_fingerprint, now=now + timedelta(seconds=1))
        failure = ExperimentOutcome(record.active_subject_fingerprint, ExperimentOutcomeType.RESEARCH_FAILED, "entry timing failed")
        challenger_execution = _fp("execution-child")
        fake_challenger = SimpleNamespace(compiled_genome=SimpleNamespace(execution_fingerprint=challenger_execution))
        evolution = EvolutionDecision(
            EvolutionAction.CREATE_CHALLENGER,
            record.active_subject_fingerprint,
            failure.fingerprint,
            "bounded child",
            challengers=(fake_challenger,),  # type: ignore[arg-type]
        )
        decision = govern_outcome(record, failure, evolution, _exhaustion(ExhaustionSignal.NONE))
        failed = store.apply(record.loop_fingerprint, decision, now=now + timedelta(seconds=2))
        self.assertEqual(failed.state, LoopState.FAILED_RESEARCHABLE)
        child = store.register_challenger(
            record.loop_fingerprint,
            manifest_fingerprint=_fp("manifest-child"),
            execution_fingerprint=challenger_execution,
            subject_fingerprint=_fp("subject-child"),
            now=now + timedelta(seconds=3),
        )
        self.assertEqual(child.root_execution_fingerprint, _fp("execution-root"))
        self.assertEqual(child.active_execution_fingerprint, challenger_execution)
        self.assertEqual(child.state, LoopState.CHALLENGER_CREATED)
        retesting = store.admit(record.loop_fingerprint, now=now + timedelta(seconds=4))
        self.assertEqual(retesting.state, LoopState.RETESTING)
        self.assertEqual(retesting.active_subject_fingerprint, _fp("subject-child"))
        self.assertEqual(retesting.iteration, 2)
        store.close()

    def test_outcome_subject_drift_is_rejected(self) -> None:
        now = datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)
        store = SQLiteResearchLoopStore()
        record = store.register(
            manifest_fingerprint=_fp("manifest"), execution_fingerprint=_fp("execution"), subject_fingerprint=_fp("subject"),
            family_fingerprint=_fp("family"), stage=EvaluationStage.A1, now=now,
        )
        record = store.admit(record.loop_fingerprint, now=now + timedelta(seconds=1))
        outcome = ExperimentOutcome(_fp("other-subject"), ExperimentOutcomeType.PASSED, "pass")
        evolution = EvolutionDecision(EvolutionAction.ADVANCE, _fp("other-subject"), outcome.fingerprint, "advance")
        with self.assertRaisesRegex(ValueError, "active M158 strategy subject"):
            govern_outcome(record, outcome, evolution, _exhaustion(ExhaustionSignal.NONE))
        store.close()

    def test_exhaustion_requires_warning_then_subsequent_strong_confirmation(self) -> None:
        now = datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)
        store = SQLiteResearchLoopStore()
        record = store.register(
            manifest_fingerprint=_fp("manifest"), execution_fingerprint=_fp("execution"), subject_fingerprint=_fp("subject"),
            family_fingerprint=_fp("family"), stage=EvaluationStage.A1, now=now,
        )
        record = store.admit(record.loop_fingerprint, now=now + timedelta(seconds=1))
        failure = ExperimentOutcome(record.active_subject_fingerprint, ExperimentOutcomeType.RESEARCH_FAILED, "family failure")
        evolution = EvolutionDecision(EvolutionAction.STOP_RESEARCH, record.active_subject_fingerprint, failure.fingerprint, "no child")
        warning = govern_outcome(record, failure, evolution, _exhaustion(ExhaustionSignal.STRONG))
        self.assertEqual(warning.to_state, LoopState.EXHAUSTION_WARNING)
        record = store.apply(record.loop_fingerprint, warning, now=now + timedelta(seconds=2))
        confirmed = review_exhaustion_warning(record, _exhaustion(ExhaustionSignal.STRONG))
        self.assertEqual(confirmed.to_state, LoopState.EXHAUSTED)
        record = store.apply(record.loop_fingerprint, confirmed, now=now + timedelta(seconds=3))
        graveyard = archive_graveyard(record)
        record = store.apply(record.loop_fingerprint, graveyard, now=now + timedelta(seconds=4))
        self.assertEqual(record.state, LoopState.GRAVEYARD)
        store.close()

    def test_graveyard_reopen_requires_material_context_change(self) -> None:
        now = datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)
        record = SimpleNamespace(state=LoopState.GRAVEYARD, active_execution_fingerprint=_fp("execution"), exhaustion_signal=ExhaustionSignal.STRONG)
        with self.assertRaisesRegex(ValueError, "materially changed"):
            ReopenEvidence(_fp("evidence"), _fp("context"), _fp("context"), ReopenChangeKind.DATASET, "same context")
        evidence = ReopenEvidence(_fp("evidence"), _fp("context-old"), _fp("context-new"), ReopenChangeKind.DATASET, "new PIT dataset")
        decision = assess_reopen(record, evidence)  # type: ignore[arg-type]
        self.assertEqual(decision.to_state, LoopState.REOPEN_ELIGIBLE)
        self.assertEqual(decision.action, GovernorAction.REOPEN)
        self.assertIsInstance(now, datetime)

    def test_stale_transition_and_operational_authority_fail_closed(self) -> None:
        now = datetime(2026, 9, 5, 2, 0, tzinfo=timezone.utc)
        store = SQLiteResearchLoopStore()
        record = store.register(
            manifest_fingerprint=_fp("manifest"), execution_fingerprint=_fp("execution"), subject_fingerprint=_fp("subject"),
            family_fingerprint=_fp("family"), stage=EvaluationStage.A1, now=now,
        )
        self.assertFalse(store.broker_write_authorized)
        self.assertFalse(store.promotion_authorized)
        admitted = store.admit(record.loop_fingerprint, now=now + timedelta(seconds=1))
        stale = GovernorDecision(GovernorAction.HOLD, LoopState.PROPOSED, LoopState.PROPOSED, "stale", admitted.active_execution_fingerprint)
        with self.assertRaisesRegex(RuntimeError, "stale"):
            store.apply(record.loop_fingerprint, stale, now=now + timedelta(seconds=2))
        self.assertTrue(store.integrity_ok())
        self.assertGreaterEqual(len(store.history(record.loop_fingerprint)), 2)
        store.close()


if __name__ == "__main__":
    unittest.main()
