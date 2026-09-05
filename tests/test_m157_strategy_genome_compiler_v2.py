from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import unittest

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
    StrategyConstraint,
    StrategyGenome,
    StrategyOrigin,
    UserStrategyIntent,
    compile_user_strategy_intent,
)


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _intent(*, researchable_trigger: bool = False) -> UserStrategyIntent:
    trigger_mode = ConstraintMode.RESEARCHABLE if researchable_trigger else ConstraintMode.LOCKED
    trigger_value = "unknown" if researchable_trigger else "rsi_reclaim"
    return UserStrategyIntent(
        "USR-EUR-157",
        "EURUSD typed research strategy",
        "Use a reviewed RSI trigger with a bounded exit and Guardian risk.",
        datetime(2026, 9, 4, tzinfo=timezone.utc),
        ("EURUSD",),
        ("M15",),
        (
            StrategyConstraint("entry.trigger", trigger_value, trigger_mode),
            StrategyConstraint("exit.trigger", "atr_or_time", ConstraintMode.LOCKED),
            StrategyConstraint("risk.policy", "guardian", ConstraintMode.LOCKED),
        ),
    )


def _resolved_specs() -> tuple[GenomeClauseSpec, ...]:
    return (
        GenomeClauseSpec(
            "entry",
            ClauseKind.TRIGGER,
            "entry.trigger",
            ClauseResolution.RESOLVED,
            "rsi_reclaim",
            ("rsi_14@v1",),
            (("threshold", "50"),),
        ),
        GenomeClauseSpec(
            "exit",
            ClauseKind.EXIT,
            "exit.trigger",
            ClauseResolution.RESOLVED,
            "atr_or_time",
            ("atr_14@v1",),
            (("max_bars", "16"),),
        ),
        GenomeClauseSpec(
            "risk",
            ClauseKind.RISK,
            "risk.policy",
            ClauseResolution.RESOLVED,
            "guardian",
        ),
    )


class StrategyGenomeCompilerV2Tests(unittest.TestCase):
    def test_compiler_binds_exact_m156_features_and_preserves_no_authority(self) -> None:
        registry = standard_feature_registry()
        genome = compile_user_strategy_intent(_intent())
        compiled = compile_strategy_genome_v2(genome, _resolved_specs(), registry)

        self.assertTrue(compiled.fully_specified)
        self.assertTrue(compiled.manifest_ready)
        self.assertEqual(tuple((row.name, row.version) for row in compiled.feature_refs), (("atr_14", "v1"), ("rsi_14", "v1")))
        self.assertEqual(len(compiled.execution_fingerprint), 64)
        self.assertEqual(len(compiled.fingerprint), 64)
        self.assertFalse(compiled.broker_write_authority)
        self.assertFalse(compiled.risk_override_authority)
        self.assertFalse(compiled.promotion_authority)

    def test_clause_order_does_not_change_compiled_identity(self) -> None:
        registry = standard_feature_registry()
        genome = compile_user_strategy_intent(_intent())
        specs = _resolved_specs()
        first = compile_strategy_genome_v2(genome, specs, registry)
        second = compile_strategy_genome_v2(genome, tuple(reversed(specs)), registry)
        self.assertEqual(first.execution_fingerprint, second.execution_fingerprint)
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_locked_source_value_cannot_be_silently_rewritten(self) -> None:
        registry = standard_feature_registry()
        genome = compile_user_strategy_intent(_intent())
        specs = list(_resolved_specs())
        specs[0] = replace(specs[0], value="different_trigger")
        with self.assertRaisesRegex(ValueError, "cannot rewrite resolved source"):
            compile_strategy_genome_v2(genome, specs, registry)

    def test_researchable_unknown_stays_explicit_until_child_genome_resolves_it(self) -> None:
        registry = standard_feature_registry()
        genome = compile_user_strategy_intent(_intent(researchable_trigger=True))
        specs = (
            GenomeClauseSpec(
                "entry", ClauseKind.TRIGGER, "entry.trigger", ClauseResolution.UNRESOLVED, "unknown"
            ),
            GenomeClauseSpec(
                "exit", ClauseKind.EXIT, "exit.trigger", ClauseResolution.RESOLVED, "atr_or_time", ("atr_14@v1",)
            ),
            GenomeClauseSpec(
                "risk", ClauseKind.RISK, "risk.policy", ClauseResolution.RESOLVED, "guardian"
            ),
        )
        compiled = compile_strategy_genome_v2(genome, specs, registry)
        self.assertFalse(compiled.fully_specified)
        self.assertFalse(compiled.manifest_ready)
        self.assertEqual(
            next(row for row in compiled.clauses if row.clause_id == "entry").constraint_mode,
            ConstraintMode.RESEARCHABLE,
        )

        premature = list(specs)
        premature[0] = replace(premature[0], resolution=ClauseResolution.RESOLVED, value="breakout", feature_keys=("rsi_14@v1",))
        with self.assertRaisesRegex(ValueError, "resolution mismatch"):
            compile_strategy_genome_v2(genome, premature, registry)

    def test_resolved_decision_clause_rejects_future_or_unknown_feature_dependency(self) -> None:
        registry = FeatureRegistry()
        for definition in standard_feature_registry()._definitions.values():  # test-only copy into a new registry
            registry.add(definition)
        registry.add(
            FeatureDefinition(
                name="future_label",
                version="v1",
                family=FeatureFamily.RETURN,
                source=FeatureSource.DUSTY_DERIVED,
                availability=AvailabilityPolicy.COMPLETED_BAR,
                lookahead=LookaheadPolicy.FUTURE,
                repaint=RepaintPolicy.STABLE,
                warmup_observations=1,
                dependencies=("close@v1",),
                markets=("FOREX",),
                provenance=("test-only",),
            )
        )
        registry.freeze()
        genome = compile_user_strategy_intent(_intent())
        specs = list(_resolved_specs())
        specs[0] = replace(specs[0], feature_keys=("future_label@v1",))
        with self.assertRaisesRegex(ValueError, "uses ineligible feature"):
            compile_strategy_genome_v2(genome, specs, registry)

    def test_compiler_repairs_legacy_external_unresolved_alias_at_boundary(self) -> None:
        registry = standard_feature_registry()
        genome = StrategyGenome(
            genome_id="legacy-child",
            origin=StrategyOrigin.DUSTY,
            title="Legacy child",
            source_fingerprint=_digest("legacy-source"),
            parent_fingerprints=(_digest("legacy-parent"),),
            symbols=("EURUSD",),
            timeframes=("M15",),
            components=("entry", "exit", "risk"),
            rules=(
                ("unresolved.entry_logic", "rsi_reclaim"),
                ("exit.trigger", "atr_or_time"),
                ("risk.policy", "guardian"),
            ),
            unresolved=("entry_logic",),
            constraints=(
                StrategyConstraint("unresolved.entry_logic", "research_required", ConstraintMode.RESEARCHABLE),
                StrategyConstraint("exit.trigger", "atr_or_time", ConstraintMode.LOCKED),
                StrategyConstraint("risk.policy", "guardian", ConstraintMode.LOCKED),
            ),
            generation=1,
        )
        specs = (
            GenomeClauseSpec(
                "entry", ClauseKind.TRIGGER, "entry_logic", ClauseResolution.RESOLVED, "rsi_reclaim", ("rsi_14@v1",)
            ),
            GenomeClauseSpec(
                "exit", ClauseKind.EXIT, "exit.trigger", ClauseResolution.RESOLVED, "atr_or_time", ("atr_14@v1",)
            ),
            GenomeClauseSpec("risk", ClauseKind.RISK, "risk.policy", ClauseResolution.RESOLVED, "guardian"),
        )
        compiled = compile_strategy_genome_v2(genome, specs, registry)
        entry = next(row for row in compiled.clauses if row.clause_id == "entry")
        self.assertEqual(entry.source_key, "unresolved.entry_logic")
        self.assertEqual(entry.resolution, ClauseResolution.RESOLVED)
        self.assertTrue(compiled.fully_specified)

    def test_forbidden_policy_cannot_be_repurposed_as_positive_clause(self) -> None:
        registry = standard_feature_registry()
        genome = compile_user_strategy_intent(_intent())
        specs = _resolved_specs() + (
            GenomeClauseSpec(
                "bad-risk", ClauseKind.RISK, "risk.martingale", ClauseResolution.RESOLVED, "prohibited"
            ),
        )
        with self.assertRaisesRegex(PermissionError, "forbidden source"):
            compile_strategy_genome_v2(genome, specs, registry)

    def test_required_trigger_exit_and_risk_clause_families_fail_closed(self) -> None:
        registry = standard_feature_registry()
        genome = compile_user_strategy_intent(_intent())
        with self.assertRaisesRegex(ValueError, "requires clause kinds"):
            compile_strategy_genome_v2(genome, _resolved_specs()[:2], registry)

    def test_strategy_without_explicit_universe_or_timeframe_cannot_compile(self) -> None:
        registry = standard_feature_registry()
        genome = compile_user_strategy_intent(_intent())
        incomplete = replace(genome, symbols=())
        with self.assertRaisesRegex(ValueError, "explicit symbol/timeframe"):
            compile_strategy_genome_v2(incomplete, _resolved_specs(), registry)

    def test_semantically_identical_strategies_share_execution_identity_but_not_record_identity(self) -> None:
        registry = standard_feature_registry()
        first_genome = compile_user_strategy_intent(_intent())
        second_genome = StrategyGenome(
            genome_id="DD-EUR-ALT",
            origin=StrategyOrigin.DUSTY,
            title="Same semantics, different provenance",
            source_fingerprint=_digest("different-provenance"),
            parent_fingerprints=(_digest("different-parent"),),
            symbols=first_genome.symbols,
            timeframes=first_genome.timeframes,
            components=first_genome.components,
            rules=first_genome.rules,
            unresolved=first_genome.unresolved,
            constraints=first_genome.constraints,
            generation=3,
        )
        first = compile_strategy_genome_v2(first_genome, _resolved_specs(), registry)
        second = compile_strategy_genome_v2(second_genome, _resolved_specs(), registry)
        self.assertEqual(first.execution_fingerprint, second.execution_fingerprint)
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_unfrozen_registry_is_not_a_stable_compiler_dependency(self) -> None:
        registry = FeatureRegistry()
        genome = compile_user_strategy_intent(_intent())
        with self.assertRaisesRegex(ValueError, "validated and frozen"):
            compile_strategy_genome_v2(genome, _resolved_specs(), registry)


if __name__ == "__main__":
    unittest.main()
