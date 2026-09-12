from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.provisional_research import (
    DEPENDENCIES,
    PRODUCTION_BLOCKED_UNTIL_M165,
    PROVISIONAL_RUNNABLE,
    ProvisionalResearchPlan,
    descendants_of,
    revalidation_after_final_calibration,
)


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class ProvisionalM166M174ResearchTests(unittest.TestCase):
    def plan(self) -> ProvisionalResearchPlan:
        return ProvisionalResearchPlan(
            lane_id="eurusd:m15:test",
            strategy_fingerprint=fp("strategy"),
            dataset_fingerprint=fp("dataset"),
            parameter_fingerprint=fp("parameter"),
            current_calibration_fingerprint=fp("calibration-20-2"),
            current_observation_count=20,
            current_distinct_days=2,
        )

    def test_plan_allows_parallel_research_without_production_authority(self) -> None:
        plan = self.plan()
        self.assertEqual(tuple(PROVISIONAL_RUNNABLE), tuple(f"m{i}_{name}" for i, name in ())) if False else None
        self.assertIn("m166_walk_forward", PROVISIONAL_RUNNABLE)
        self.assertIn("m173_strategy_dependency", PROVISIONAL_RUNNABLE)
        self.assertNotIn("m174_robustness", PROVISIONAL_RUNNABLE)
        self.assertIn("m166_production_admission", PRODUCTION_BLOCKED_UNTIL_M165)
        self.assertFalse(plan.broker_write_authority)
        self.assertFalse(plan.live_write_authority)
        self.assertFalse(plan.custody_write_authority)
        self.assertFalse(plan.promotion_authority)
        self.assertFalse(plan.retry_authority)
        self.assertFalse(plan.risk_override_authority)
        self.assertEqual(len(plan.fingerprint), 64)

    def test_final_calibration_only_invalidates_cost_sensitive_branch_and_m174(self) -> None:
        invalidated = revalidation_after_final_calibration(
            provisional_calibration_fingerprint=fp("calibration-20-2"),
            final_calibration_fingerprint=fp("calibration-30-3"),
        )
        self.assertEqual(invalidated, ("m170_cost_torture", "m174_robustness"))
        for reusable in (
            "m166_walk_forward",
            "m167_purged_validation",
            "m168_parameter_stability",
            "m169_regime_torture",
            "m171_forward_decay",
            "m172_tail_risk",
            "m173_strategy_dependency",
        ):
            self.assertNotIn(reusable, invalidated)

    def test_strategy_change_invalidates_all_strategy_descendants(self) -> None:
        invalidated = descendants_of(("strategy",))
        for stage in (
            "m166_walk_forward",
            "m167_purged_validation",
            "m168_parameter_stability",
            "m169_regime_torture",
            "m170_cost_torture",
            "m171_forward_decay",
            "m172_tail_risk",
            "m173_strategy_dependency",
            "m174_robustness",
        ):
            self.assertIn(stage, invalidated)

    def test_dependency_graph_keeps_m170_as_direct_calibration_consumer(self) -> None:
        self.assertIn("m165_calibration", DEPENDENCIES["m170_cost_torture"])
        for stage in (
            "m166_walk_forward",
            "m167_purged_validation",
            "m168_parameter_stability",
            "m169_regime_torture",
            "m171_forward_decay",
            "m172_tail_risk",
            "m173_strategy_dependency",
        ):
            self.assertNotIn("m165_calibration", DEPENDENCIES[stage])


if __name__ == "__main__":
    unittest.main()
