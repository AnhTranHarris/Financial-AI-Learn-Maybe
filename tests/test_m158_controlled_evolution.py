from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.controlled_evolution import (
    EvolutionAction,
    ExperimentOutcome,
    ExperimentOutcomeType,
    FeatureReplacement,
    InfrastructureFailureKind,
    MutationInstruction,
    create_challenger,
    decide_evolution,
)
from dusty.feature_registry import (
    AvailabilityPolicy,
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureSource,
    LookaheadPolicy,
    RepaintPolicy,
    standard_feature_registry,
)
from dusty.strategy_genome_v2 import (
    ClauseKind,
    ClauseResolution,
    GenomeClauseSpec,
    compile_strategy_genome_v2,
)
from dusty.strategy_lab import (
    ConstraintMode,
    FailureDiagnosis,
    FailureMechanism,
    PERMANENT_FORBIDDEN,
    StrategyConstraint,
    StrategyGenome,
    StrategyOrigin,
)


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _parent() -> StrategyGenome:
    return StrategyGenome(
        genome_id="DD-M158-PARENT",
        origin=StrategyOrigin.DUSTY,
        title="M158 parent",
        source_fingerprint=_sha("m158-parent-source"),
        parent_fingerprints=(_sha("m157-ancestor"),),
        symbols=("EURUSD",),
        timeframes=("M15",),
        components=("entry", "exit", "risk"),
        rules=(
            ("entry.trigger", "rsi_reclaim"),
            ("exit.trigger", "atr_or_time"),
            ("risk.policy", "guardian"),
        ),
        unresolved=(),
        constraints=(
            StrategyConstraint("entry.trigger", "rsi_reclaim", ConstraintMode.RESEARCHABLE),
            StrategyConstraint("exit.trigger", "atr_or_time", ConstraintMode.RESEARCHABLE),
            StrategyConstraint("risk.policy", "guardian", ConstraintMode.LOCKED),
            *PERMANENT_FORBIDDEN,
        ),
        generation=1,
    )


def _specs(*, entry_feature: str = "rsi_14@v1") -> tuple[GenomeClauseSpec, ...]:
    return (
        GenomeClauseSpec(
            "entry",
            ClauseKind.TRIGGER,
            "entry.trigger",
            ClauseResolution.RESOLVED,
            "rsi_reclaim",
            (entry_feature,),
            (("threshold", "50"),),
        ),
        GenomeClauseSpec(
            "exit",
            ClauseKind.EXIT,
            "exit.trigger",
            ClauseResolution.RESOLVED,
            "atr_or_time",
            ("atr_14@v1",),
        ),
        GenomeClauseSpec(
            "risk",
            ClauseKind.RISK,
            "risk.policy",
            ClauseResolution.RESOLVED,
            "guardian",
        ),
    )


def _compiled(parent: StrategyGenome | None = None):
    parent = _parent() if parent is None else parent
    registry = standard_feature_registry()
    return parent, registry, compile_strategy_genome_v2(parent, _specs(), registry)


def _outcome(parent: StrategyGenome, kind: ExperimentOutcomeType, reason: str = "test") -> ExperimentOutcome:
    return ExperimentOutcome(
        parent.fingerprint,
        kind,
        reason,
        (_sha("evidence-a"),),
        InfrastructureFailureKind.PROVIDER if kind is ExperimentOutcomeType.INFRASTRUCTURE_FAILED else None,
    )


def _replacement_registry(*, future_target: bool = False) -> FeatureRegistry:
    base = dict(
        family=FeatureFamily.MOMENTUM,
        source=FeatureSource.DUSTY_DERIVED,
        availability=AvailabilityPolicy.COMPLETED_BAR,
        repaint=RepaintPolicy.STABLE,
        markets=("FOREX",),
        compatible_mutations=("rolling_window",),
        provenance=("m158-test",),
    )
    return FeatureRegistry(
        (
            FeatureDefinition(
                name="rsi_14",
                version="v1",
                lookahead=LookaheadPolicy.NONE,
                warmup_observations=15,
                **base,
            ),
            FeatureDefinition(
                name="rsi_21",
                version="v1",
                lookahead=LookaheadPolicy.FUTURE if future_target else LookaheadPolicy.NONE,
                warmup_observations=22,
                **base,
            ),
        )
    ).freeze()


def _replacement_specs() -> tuple[GenomeClauseSpec, ...]:
    return (
        GenomeClauseSpec(
            "entry",
            ClauseKind.TRIGGER,
            "entry.trigger",
            ClauseResolution.RESOLVED,
            "rsi_reclaim",
            ("rsi_14@v1",),
        ),
        GenomeClauseSpec("exit", ClauseKind.EXIT, "exit.trigger", ClauseResolution.RESOLVED, "atr_or_time"),
        GenomeClauseSpec("risk", ClauseKind.RISK, "risk.policy", ClauseResolution.RESOLVED, "guardian"),
    )


class ControlledEvolutionTests(unittest.TestCase):
    def test_pass_advances_without_mutation(self) -> None:
        parent, registry, compiled = _compiled()
        decision = decide_evolution(parent, compiled, registry, _outcome(parent, ExperimentOutcomeType.PASSED, "A1 passed"))
        self.assertEqual(decision.action, EvolutionAction.ADVANCE)
        self.assertFalse(decision.challengers)
        self.assertIsNone(decision.exact_retry_execution_fingerprint)

    def test_infrastructure_failure_retries_exact_execution_and_never_mutates(self) -> None:
        parent, registry, compiled = _compiled()
        outcome = _outcome(parent, ExperimentOutcomeType.INFRASTRUCTURE_FAILED, "Kronos process crashed")
        decision = decide_evolution(
            parent,
            compiled,
            registry,
            outcome,
            candidate_instructions=((MutationInstruction("entry.trigger", "different", "should never run"),),),
        )
        self.assertEqual(decision.action, EvolutionAction.RETRY_EXACT)
        self.assertEqual(decision.exact_retry_execution_fingerprint, compiled.execution_fingerprint)
        self.assertFalse(decision.challengers)

    def test_failure_diagnosis_creates_bounded_one_change_descendants(self) -> None:
        parent, registry, compiled = _compiled()
        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED, "entry weak in holdout")
        diagnosis = FailureDiagnosis(
            parent.fingerprint,
            FailureMechanism.ENTRY,
            "Test a bounded entry-trigger alternative only.",
            outcome.evidence_fingerprints,
            "entry.trigger",
            ("rsi_reclaim_55", "rsi_reclaim_60"),
        )
        decision = decide_evolution(parent, compiled, registry, outcome, diagnosis=diagnosis)
        self.assertEqual(decision.action, EvolutionAction.CREATE_CHALLENGER)
        self.assertEqual(len(decision.challengers), 2)
        self.assertEqual(parent.rule_map()["entry.trigger"], "rsi_reclaim")
        for challenger in decision.challengers:
            self.assertEqual(challenger.source_genome.generation, parent.generation + 1)
            self.assertIn(parent.fingerprint, challenger.source_genome.parent_fingerprints)
            self.assertNotEqual(challenger.source_genome.fingerprint, parent.fingerprint)
            self.assertFalse(challenger.compiled_genome.broker_write_authority)
            self.assertFalse(challenger.compiled_genome.risk_override_authority)
            self.assertFalse(challenger.compiled_genome.promotion_authority)

    def test_locked_or_forbidden_variables_cannot_mutate(self) -> None:
        parent, registry, compiled = _compiled()
        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED)
        with self.assertRaisesRegex(PermissionError, "locked variable"):
            create_challenger(
                parent,
                compiled,
                registry,
                outcome,
                (MutationInstruction("risk.policy", "aggressive", "try more risk"),),
            )
        with self.assertRaisesRegex(PermissionError, "forbidden variable"):
            create_challenger(
                parent,
                compiled,
                registry,
                outcome,
                (MutationInstruction("risk.martingale", "enabled", "bad idea"),),
            )

    def test_more_than_two_mutations_are_rejected_before_child_creation(self) -> None:
        parent, registry, compiled = _compiled()
        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED)
        with self.assertRaisesRegex(ValueError, "one or two mutations"):
            create_challenger(
                parent,
                compiled,
                registry,
                outcome,
                (
                    MutationInstruction("entry.trigger", "a", "a"),
                    MutationInstruction("exit.trigger", "b", "b"),
                    MutationInstruction("risk.policy", "c", "c"),
                ),
            )

    def test_candidate_instruction_generator_is_not_consumed_by_diagnosis_logic(self) -> None:
        parent, registry, compiled = _compiled()
        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED)
        groups = (
            (MutationInstruction("entry.trigger", value, f"test {value}"),)
            for value in ("rsi_52", "rsi_54")
        )
        decision = decide_evolution(parent, compiled, registry, outcome, candidate_instructions=groups)
        self.assertEqual(decision.action, EvolutionAction.CREATE_CHALLENGER)
        self.assertEqual(len(decision.challengers), 2)

    def test_feature_replacement_must_be_registered_and_allowed_by_m156(self) -> None:
        parent = _parent()
        registry = _replacement_registry()
        compiled = compile_strategy_genome_v2(parent, _replacement_specs(), registry)
        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED)
        instruction = MutationInstruction(
            "entry.trigger",
            "rsi_21_reclaim",
            "Test wider RSI window",
            FeatureReplacement("entry", "rsi_14@v1", "rsi_21@v1", "rolling_window"),
        )
        challenger = create_challenger(parent, compiled, registry, outcome, (instruction,))
        self.assertEqual(
            tuple((row.name, row.version) for row in challenger.compiled_genome.feature_refs),
            (("rsi_21", "v1"),),
        )

        denied = MutationInstruction(
            "entry.trigger",
            "rsi_21_reclaim",
            "Try incompatible semantic mutation",
            FeatureReplacement("entry", "rsi_14@v1", "rsi_21@v1", "normalization"),
        )
        with self.assertRaisesRegex(PermissionError, "does not allow mutation family"):
            create_challenger(parent, compiled, registry, outcome, (denied,))

    def test_feature_replacement_cannot_smuggle_future_feature_into_decision(self) -> None:
        parent = _parent()
        registry = _replacement_registry(future_target=True)
        compiled = compile_strategy_genome_v2(parent, _replacement_specs(), registry)
        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED)
        instruction = MutationInstruction(
            "entry.trigger",
            "future_rsi",
            "This must fail at M157 decision eligibility",
            FeatureReplacement("entry", "rsi_14@v1", "rsi_21@v1", "rolling_window"),
        )
        with self.assertRaisesRegex(ValueError, "uses ineligible feature"):
            create_challenger(parent, compiled, registry, outcome, (instruction,))

    def test_research_failure_without_defensible_mutation_stops_at_m158_boundary(self) -> None:
        parent, registry, compiled = _compiled()
        decision = decide_evolution(parent, compiled, registry, _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED))
        self.assertEqual(decision.action, EvolutionAction.STOP_RESEARCH)
        self.assertFalse(decision.challengers)

    def test_outcome_and_diagnosis_must_belong_to_exact_parent(self) -> None:
        parent, registry, compiled = _compiled()
        other = _sha("other")
        bad_outcome = ExperimentOutcome(other, ExperimentOutcomeType.RESEARCH_FAILED, "wrong subject")
        with self.assertRaisesRegex(ValueError, "does not belong to parent"):
            decide_evolution(parent, compiled, registry, bad_outcome)

        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED)
        bad_diagnosis = FailureDiagnosis(
            other,
            FailureMechanism.ENTRY,
            "wrong diagnosis",
            (),
            "entry.trigger",
            ("x",),
        )
        with self.assertRaisesRegex(ValueError, "diagnosis does not belong"):
            decide_evolution(parent, compiled, registry, outcome, diagnosis=bad_diagnosis)

    def test_same_parent_outcome_and_mutation_produce_deterministic_challenger_identity(self) -> None:
        parent, registry, compiled = _compiled()
        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED)
        instruction = (MutationInstruction("entry.trigger", "rsi_55", "bounded test"),)
        first = create_challenger(parent, compiled, registry, outcome, instruction)
        second = create_challenger(parent, compiled, registry, outcome, instruction)
        self.assertEqual(first.mutation_fingerprint, second.mutation_fingerprint)
        self.assertEqual(first.source_genome.fingerprint, second.source_genome.fingerprint)
        self.assertEqual(first.compiled_genome.fingerprint, second.compiled_genome.fingerprint)

    def test_duplicate_candidate_groups_do_not_create_duplicate_challengers(self) -> None:
        parent, registry, compiled = _compiled()
        outcome = _outcome(parent, ExperimentOutcomeType.RESEARCH_FAILED)
        instruction = (MutationInstruction("entry.trigger", "rsi_55", "bounded test"),)
        decision = decide_evolution(
            parent,
            compiled,
            registry,
            outcome,
            candidate_instructions=(instruction, instruction),
        )
        self.assertEqual(decision.action, EvolutionAction.CREATE_CHALLENGER)
        self.assertEqual(len(decision.challengers), 1)


if __name__ == "__main__":
    unittest.main()
