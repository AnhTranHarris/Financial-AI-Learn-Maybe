from __future__ import annotations

import unittest

from dusty.feature_registry import (
    AvailabilityPolicy,
    ComputeCost,
    FeatureDefinition,
    FeatureFamily,
    FeatureRegistry,
    FeatureSource,
    LookaheadPolicy,
    RepaintPolicy,
    standard_feature_registry,
)
from dusty.features import FeatureConfig


class FeatureIntelligenceRegistryTests(unittest.TestCase):
    def _definition(self, name: str, *, dependencies: tuple[str, ...] = ()) -> FeatureDefinition:
        return FeatureDefinition(
            name=name,
            version="v1",
            family=FeatureFamily.CUSTOM,
            source=FeatureSource.DUSTY_DERIVED,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=5,
            dependencies=dependencies,
            markets=("FOREX",),
            compatible_mutations=("threshold",),
            provenance=("test-suite",),
            compute_cost=ComputeCost.LOW,
        )

    def test_standard_registry_exposes_canonical_period_specific_features(self) -> None:
        registry = standard_feature_registry(FeatureConfig(ma_period=20, atr_period=14, rsi_period=14))
        self.assertTrue(registry.frozen)
        self.assertEqual(len(registry.keys()), 11)
        self.assertIn("sma_20@v1", registry.keys())
        self.assertIn("ema_20@v1", registry.keys())
        self.assertIn("atr_14@v1", registry.keys())
        self.assertIn("rsi_14@v1", registry.keys())
        self.assertNotIn("sma@v1", registry.keys())
        self.assertEqual(registry.warmup_required("rsi_14@v1"), 15)
        self.assertTrue(registry.decision_eligible("rsi_14@v1"))
        self.assertTrue(registry.supports_market("rsi_14@v1", "forex"))
        self.assertTrue(registry.requires_native_parity("rsi_14@v1"))

    def test_registry_dependency_closure_is_ordered_and_content_addressed(self) -> None:
        registry = FeatureRegistry()
        registry.add(self._definition("base"))
        registry.add(self._definition("derived", dependencies=("base@v1",)))
        registry.freeze()
        self.assertEqual(tuple(row.key for row in registry.closure("derived@v1")), ("base@v1", "derived@v1"))
        self.assertEqual(len(registry.resolved_fingerprint("derived@v1")), 64)
        self.assertEqual(len(registry.feature_set_fingerprint(("derived@v1", "base@v1"))), 64)

    def test_registry_identity_is_independent_of_registration_order(self) -> None:
        first = FeatureRegistry((self._definition("a"), self._definition("b"))).freeze()
        second = FeatureRegistry((self._definition("b"), self._definition("a"))).freeze()
        self.assertEqual(first.fingerprint, second.fingerprint)

    def test_dependency_semantics_change_resolved_identity(self) -> None:
        base_a = self._definition("base")
        base_b = FeatureDefinition(
            name="base",
            version="v1",
            family=FeatureFamily.VOLATILITY,
            source=FeatureSource.DUSTY_DERIVED,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=7,
            markets=("FOREX",),
            provenance=("changed-semantics",),
        )
        derived = self._definition("derived", dependencies=("base@v1",))
        first = FeatureRegistry((base_a, derived)).freeze()
        second = FeatureRegistry((base_b, derived)).freeze()
        self.assertNotEqual(first.resolved_fingerprint("derived@v1"), second.resolved_fingerprint("derived@v1"))

    def test_unknown_dependency_and_cycle_fail_closed(self) -> None:
        missing = FeatureRegistry((self._definition("a", dependencies=("missing@v1",)),))
        with self.assertRaisesRegex(ValueError, "unknown feature"):
            missing.freeze()

        cycle = FeatureRegistry()
        cycle.add(self._definition("a", dependencies=("b@v1",)))
        cycle.add(self._definition("b", dependencies=("a@v1",)))
        with self.assertRaisesRegex(ValueError, "dependency cycle"):
            cycle.freeze()

    def test_future_repainting_or_unknown_features_are_not_decision_eligible(self) -> None:
        future = FeatureDefinition(
            name="future_label",
            version="v1",
            family=FeatureFamily.RETURN,
            source=FeatureSource.DUSTY_DERIVED,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.FUTURE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=1,
            markets=("FOREX",),
            provenance=("test-suite",),
        )
        derived = self._definition("derived", dependencies=("future_label@v1",))
        registry = FeatureRegistry((future, derived)).freeze()
        self.assertFalse(registry.decision_eligible("derived@v1"))
        self.assertEqual(registry.eligibility_reasons("derived@v1"), ("future_label@v1:lookahead=future",))

        repainting = FeatureDefinition(
            name="repainting",
            version="v1",
            family=FeatureFamily.CUSTOM,
            source=FeatureSource.MT5_CUSTOM_INDICATOR,
            availability=AvailabilityPolicy.UNKNOWN,
            lookahead=LookaheadPolicy.UNKNOWN,
            repaint=RepaintPolicy.UNKNOWN,
            warmup_observations=0,
            markets=("FOREX",),
            provenance=("opaque-ex5",),
        )
        blocked = FeatureRegistry((repainting,)).freeze()
        self.assertFalse(blocked.decision_eligible("repainting@v1"))
        self.assertEqual(
            blocked.eligibility_reasons("repainting@v1"),
            (
                "repainting@v1:lookahead=unknown",
                "repainting@v1:repaint=unknown",
                "repainting@v1:availability=unknown",
            ),
        )

    def test_registry_freeze_prevents_silent_definition_drift(self) -> None:
        registry = FeatureRegistry((self._definition("a"),)).freeze()
        with self.assertRaisesRegex(RuntimeError, "frozen"):
            registry.add(self._definition("b"))

    def test_manifest_reference_binds_resolved_feature_identity(self) -> None:
        registry = standard_feature_registry()
        ref = registry.to_manifest_ref("atr_14@v1")
        self.assertEqual(ref.name, "atr_14")
        self.assertEqual(ref.version, "v1")
        self.assertEqual(ref.fingerprint, registry.resolved_fingerprint("atr_14@v1"))


if __name__ == "__main__":
    unittest.main()
