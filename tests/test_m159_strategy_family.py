from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unittest

from dusty.controlled_evolution import ExperimentOutcomeType
from dusty.feature_registry import standard_feature_registry
from dusty.strategy_family import (
    BehaviorSignature,
    ExhaustionSignal,
    FamilyExperimentEvidence,
    NoveltyClass,
    StrategyLineageIndex,
    assess_exhaustion,
    assess_novelty,
    behavior_correlation,
    semantic_distance,
    structural_family_fingerprint,
)
from dusty.strategy_genome_v2 import (
    ClauseKind,
    ClauseResolution,
    GenomeClauseSpec,
    compile_strategy_genome_v2,
)
from dusty.strategy_lab import ConstraintMode, StrategyConstraint, StrategyGenome, StrategyOrigin


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _compiled(*, threshold: str = "50", entry_rule: str = "rsi_reclaim", timeframe: str = "M15"):
    genome = StrategyGenome(
        genome_id=f"DD-M159-{threshold}-{entry_rule}-{timeframe}",
        origin=StrategyOrigin.DUSTY,
        title="M159 test strategy",
        source_fingerprint=_sha(f"source-{threshold}-{entry_rule}-{timeframe}"),
        parent_fingerprints=(),
        symbols=("EURUSD",),
        timeframes=(timeframe,),
        components=("entry", "exit", "risk"),
        rules=(("entry.trigger", entry_rule), ("exit.trigger", "atr_or_time"), ("risk.policy", "guardian")),
        unresolved=(),
        constraints=(
            StrategyConstraint("entry.trigger", entry_rule, ConstraintMode.RESEARCHABLE),
            StrategyConstraint("exit.trigger", "atr_or_time", ConstraintMode.RESEARCHABLE),
            StrategyConstraint("risk.policy", "guardian", ConstraintMode.LOCKED),
        ),
        generation=1,
    )
    specs = (
        GenomeClauseSpec(
            "entry",
            ClauseKind.TRIGGER,
            "entry.trigger",
            ClauseResolution.RESOLVED,
            entry_rule,
            ("rsi_14@v1",),
            (("threshold", threshold),),
        ),
        GenomeClauseSpec(
            "exit",
            ClauseKind.EXIT,
            "exit.trigger",
            ClauseResolution.RESOLVED,
            "atr_or_time",
            ("atr_14@v1",),
        ),
        GenomeClauseSpec("risk", ClauseKind.RISK, "risk.policy", ClauseResolution.RESOLVED, "guardian"),
    )
    return compile_strategy_genome_v2(genome, specs, standard_feature_registry())


def _evidence(
    family: str,
    idx: int,
    *,
    outcome: ExperimentOutcomeType = ExperimentOutcomeType.RESEARCH_FAILED,
    axis: str | None = None,
    novelty: float = 0.02,
    improvement: float = 0.01,
    mechanism: str = "entry",
) -> FamilyExperimentEvidence:
    return FamilyExperimentEvidence(
        family_fingerprint=family,
        execution_fingerprint=_sha(f"execution-{idx}"),
        outcome=outcome,
        mutation_axis=axis or ("entry", "exit", "session")[idx % 3],
        novelty_score=novelty,
        improvement_score=improvement,
        evidence_fingerprint=_sha(f"evidence-{idx}-{outcome.value}"),
        failure_mechanism=mechanism if outcome is ExperimentOutcomeType.RESEARCH_FAILED else "",
    )


class M159StrategyFamilyTests(unittest.TestCase):
    def test_execution_identical_strategies_are_exact_duplicates_despite_record_provenance(self) -> None:
        base = _compiled()
        copy = replace(
            base,
            source_genome_fingerprint=_sha("different-genome-record"),
            source_provenance_fingerprint=_sha("different-provenance"),
            generation=7,
        )
        self.assertEqual(base.execution_fingerprint, copy.execution_fingerprint)
        self.assertNotEqual(base.fingerprint, copy.fingerprint)
        assessment = assess_novelty(copy, base)
        self.assertEqual(assessment.classification, NoveltyClass.EXACT_DUPLICATE)

    def test_small_numeric_parameter_change_is_near_duplicate_not_new_alpha(self) -> None:
        base = _compiled(threshold="50")
        nearby = _compiled(threshold="51")
        distance = semantic_distance(nearby, base)
        self.assertEqual(distance.structural, 0.0)
        self.assertGreater(distance.parameters, 0.0)
        self.assertLess(distance.parameters, 0.03)
        self.assertEqual(structural_family_fingerprint(base), structural_family_fingerprint(nearby))
        self.assertEqual(assess_novelty(nearby, base).classification, NoveltyClass.NEAR_DUPLICATE)

    def test_material_semantic_and_timeframe_change_can_be_novel(self) -> None:
        base = _compiled()
        other = _compiled(threshold="90", entry_rule="breakout_after_volatility_expansion", timeframe="H4")
        assessment = assess_novelty(other, base)
        self.assertEqual(assessment.classification, NoveltyClass.NOVEL)
        self.assertNotEqual(structural_family_fingerprint(base), structural_family_fingerprint(other))

    def test_behavior_comparison_requires_identical_evaluation_evidence(self) -> None:
        left = BehaviorSignature(_sha("evaluation-a"), (0.0, 1.0, 2.0, 3.0))
        right = BehaviorSignature(_sha("evaluation-b"), (0.0, 2.0, 4.0, 6.0))
        with self.assertRaisesRegex(ValueError, "same evaluation evidence"):
            behavior_correlation(left, right)

    def test_behavioral_similarity_can_mark_structural_change_as_family_variant(self) -> None:
        base = _compiled()
        other = _compiled(entry_rule="breakout_after_volatility_expansion", timeframe="H4")
        evaluation = _sha("shared-evaluation")
        left = BehaviorSignature(evaluation, (-1.0, 0.0, 1.0, 2.0, 3.0))
        right = BehaviorSignature(evaluation, (-2.0, 0.0, 2.0, 4.0, 6.0))
        assessment = assess_novelty(other, base, candidate_behavior=right, incumbent_behavior=left)
        self.assertAlmostEqual(assessment.behavior_correlation or 0.0, 1.0, places=12)
        self.assertEqual(assessment.classification, NoveltyClass.FAMILY_VARIANT)

    def test_unknown_external_parent_is_allowed_but_cycle_is_rejected_transactionally(self) -> None:
        template = _compiled()
        node_a = _sha("node-a")
        node_b = _sha("node-b")
        external = _sha("external-root")
        a = replace(template, source_genome_fingerprint=node_a, parent_fingerprints=(node_b,))
        b = replace(template, source_genome_fingerprint=node_b, parent_fingerprints=(node_a,))
        root_child = replace(template, source_genome_fingerprint=_sha("root-child"), parent_fingerprints=(external,))

        index = StrategyLineageIndex()
        index.register(root_child)
        self.assertEqual(index.parents(root_child.source_genome_fingerprint), (external,))
        self.assertIn(external, index.ancestors(root_child.source_genome_fingerprint))

        index.register(a)
        with self.assertRaisesRegex(ValueError, "cycle"):
            index.register(b)
        self.assertEqual(index.parents(node_b), ())
        self.assertEqual(index.parents(node_a), (node_b,))

    def test_experiment_count_alone_and_infrastructure_failures_cannot_exhaust_family(self) -> None:
        family = structural_family_fingerprint(_compiled())
        infrastructure = tuple(
            _evidence(family, idx, outcome=ExperimentOutcomeType.INFRASTRUCTURE_FAILED)
            for idx in range(100)
        )
        few_research = tuple(_evidence(family, 100 + idx) for idx in range(11))
        assessment = assess_exhaustion((*infrastructure, *few_research))
        self.assertEqual(assessment.signal, ExhaustionSignal.NONE)
        self.assertEqual(assessment.research_attempts, 11)

    def test_low_novelty_low_improvement_repeated_failure_can_be_strong_exhaustion_evidence(self) -> None:
        family = structural_family_fingerprint(_compiled())
        rows = tuple(_evidence(family, idx, novelty=0.02, improvement=0.01, mechanism="entry") for idx in range(12))
        assessment = assess_exhaustion(rows)
        self.assertEqual(assessment.signal, ExhaustionSignal.STRONG)
        self.assertEqual(assessment.mutation_axes, 3)
        self.assertEqual(assessment.dominant_failure_mechanism, "entry")

    def test_recent_meaningful_improvement_blocks_premature_exhaustion(self) -> None:
        family = structural_family_fingerprint(_compiled())
        rows = [
            _evidence(family, idx, novelty=0.02, improvement=0.01, mechanism="entry")
            for idx in range(12)
        ]
        rows[-1] = _evidence(family, 11, novelty=0.50, improvement=0.80, mechanism="entry")
        assessment = assess_exhaustion(rows)
        self.assertEqual(assessment.signal, ExhaustionSignal.NONE)
        self.assertGreater(assessment.recent_mean_improvement, 0.10)

    def test_exhaustion_evidence_must_not_mix_structural_families(self) -> None:
        family_a = structural_family_fingerprint(_compiled())
        family_b = structural_family_fingerprint(_compiled(timeframe="H1"))
        with self.assertRaisesRegex(ValueError, "one structural family"):
            assess_exhaustion((_evidence(family_a, 1), _evidence(family_b, 2)))


if __name__ == "__main__":
    unittest.main()
