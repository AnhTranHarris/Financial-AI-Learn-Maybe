from __future__ import annotations

import unittest

from dusty.research import RuleOp
from dusty.research_challengers import (
    ChallengerPlan,
    MutationKind,
    ResearchMutation,
    apply_mutation,
    generate_challengers,
)
from dusty.reviewed_strategies import reviewed_research_packages


class M106ChallengerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parent = reviewed_research_packages()[0]
        self.mutations = (
            ResearchMutation(MutationKind.FORECAST_NEUTRAL_RETURN, 0.0002),
            ResearchMutation(MutationKind.EXIT_HORIZON_MINUTES, 180),
            ResearchMutation(MutationKind.COOLDOWN_STEPS, 2),
            ResearchMutation(MutationKind.RSI_PERIOD, 21),
            ResearchMutation(MutationKind.ENTRY_THRESHOLD, 57.0, "rsi", RuleOp.GE),
        )

    def test_one_factor_plan_is_deterministic_order_independent_and_never_promotable(self) -> None:
        forward = generate_challengers(self.parent, ChallengerPlan(self.mutations, max_candidates=5))
        reverse = generate_challengers(self.parent, ChallengerPlan(tuple(reversed(self.mutations)), max_candidates=5))
        self.assertEqual([row.candidate_fingerprint for row in forward],
                         [row.candidate_fingerprint for row in reverse])
        self.assertEqual(len(forward), 5)
        self.assertEqual(len({row.package.fingerprint for row in forward}), 5)
        self.assertTrue(all(row.parent_fingerprint == self.parent.fingerprint for row in forward))
        self.assertTrue(all(not row.promotion_eligible for row in forward))

    def test_each_supported_mutation_changes_only_its_semantic_area(self) -> None:
        by_kind = {
            draft.mutation.kind: draft.package
            for draft in generate_challengers(self.parent, ChallengerPlan(self.mutations, max_candidates=5))
        }
        forecast = by_kind[MutationKind.FORECAST_NEUTRAL_RETURN]
        self.assertEqual(forecast.spec, self.parent.spec)
        self.assertEqual(forecast.features, self.parent.features)
        self.assertEqual(forecast.cognition.forecast_neutral_return, 0.0002)

        horizon = by_kind[MutationKind.EXIT_HORIZON_MINUTES]
        self.assertEqual(horizon.spec.exit_plan.max_elapsed_minutes, 180)
        self.assertEqual(horizon.spec.exit_plan.max_hold_steps, 12)
        self.assertEqual(horizon.spec.intended_horizon_minutes, 180)
        self.assertEqual(horizon.features, self.parent.features)
        self.assertEqual(horizon.cognition, self.parent.cognition)

        cooldown = by_kind[MutationKind.COOLDOWN_STEPS]
        self.assertEqual(cooldown.spec.cooldown_steps, 2)
        self.assertEqual(cooldown.spec.entry_groups, self.parent.spec.entry_groups)

        rsi_period = by_kind[MutationKind.RSI_PERIOD]
        self.assertEqual(rsi_period.features.rsi_period, 21)
        self.assertEqual(rsi_period.spec, self.parent.spec)
        self.assertEqual(rsi_period.cognition, self.parent.cognition)

        threshold = by_kind[MutationKind.ENTRY_THRESHOLD]
        changed = [
            clause.value
            for group in threshold.spec.entry_groups
            for clause in group.clauses
            if clause.feature == "rsi" and clause.op is RuleOp.GE
        ]
        self.assertEqual(changed, [57.0])
        self.assertEqual(threshold.features, self.parent.features)
        self.assertEqual(threshold.cognition, self.parent.cognition)

    def test_budget_fails_closed_instead_of_silently_selecting_a_subset(self) -> None:
        with self.assertRaisesRegex(ValueError, "candidate_budget_exceeded"):
            generate_challengers(self.parent, ChallengerPlan(self.mutations, max_candidates=4))

    def test_duplicate_mutations_and_ambiguous_or_missing_clause_targets_are_rejected(self) -> None:
        mutation = ResearchMutation(MutationKind.RSI_PERIOD, 21)
        with self.assertRaisesRegex(ValueError, "duplicate_mutations"):
            ChallengerPlan((mutation, mutation))
        with self.assertRaisesRegex(ValueError, "exactly_one_matching_clause"):
            apply_mutation(
                self.parent,
                ResearchMutation(MutationKind.ENTRY_THRESHOLD, 1.0, "missing", RuleOp.GE),
            )

    def test_exit_horizon_must_align_with_decision_timeframe(self) -> None:
        with self.assertRaisesRegex(ValueError, "align"):
            apply_mutation(
                self.parent,
                ResearchMutation(MutationKind.EXIT_HORIZON_MINUTES, 181),
            )

    def test_noop_mutation_is_deduplicated_against_parent(self) -> None:
        plan = ChallengerPlan((ResearchMutation(MutationKind.RSI_PERIOD, self.parent.features.rsi_period),))
        self.assertEqual(generate_challengers(self.parent, plan), ())


if __name__ == "__main__":
    unittest.main()
