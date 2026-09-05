from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.forecast_ablation import AblationEffect, ForecastAblationComparison, ForecastAblationVariant
from dusty.forecast_interaction_map import (
    InteractionPolicy,
    InteractionStatus,
    StrategyForecastInteractionObservation,
    build_interaction_cell,
)
from dusty.forecast_specialization import ForecastContextBucket


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def comparison(index: int, variant: ForecastAblationVariant, delta: float, dd_delta: float) -> ForecastAblationComparison:
    effect = AblationEffect.BENEFICIAL if delta > 0 else (AblationEffect.HARMFUL if delta < 0 else AblationEffect.NEUTRAL)
    return ForecastAblationComparison(fp(f"strategy-{index}"), fp(f"eval-{index}"), fp("cost"), variant, fp(f"control-{index}"), fp(f"variant-{index}"), effect, delta, dd_delta, 0, False)


class M182StrategyForecastInteractionMapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bucket = ForecastContextBucket("EURUSD", "M15", "london", "trend", 4)
        self.variant = ForecastAblationVariant(("chronos2",))

    def test_strategy_family_can_show_consistent_provider_benefit(self) -> None:
        rows = tuple(StrategyForecastInteractionObservation("breakout", self.bucket, comparison(i, self.variant, 0.01 + i * 0.001, 0.005)) for i in range(3))
        cell = build_interaction_cell("breakout", self.bucket, self.variant, rows)
        self.assertEqual(cell.status, InteractionStatus.BENEFICIAL)
        self.assertEqual(cell.observation_count, 3)
        self.assertGreater(cell.mean_net_return_delta, 0)
        self.assertEqual(cell.beneficial_fraction, 1.0)
        self.assertFalse(cell.provider_selection_authority)
        self.assertFalse(cell.strategy_mutation_authority)
        self.assertFalse(cell.broker_write_authority)

    def test_same_provider_can_be_harmful_to_different_strategy_family(self) -> None:
        rows = tuple(StrategyForecastInteractionObservation("mean-reversion", self.bucket, comparison(i, self.variant, -0.01, 0.01)) for i in range(3))
        cell = build_interaction_cell("mean-reversion", self.bucket, self.variant, rows)
        self.assertEqual(cell.status, InteractionStatus.HARMFUL)
        self.assertEqual(cell.harmful_fraction, 1.0)

    def test_sparse_interaction_is_insufficient(self) -> None:
        row = StrategyForecastInteractionObservation("breakout", self.bucket, comparison(0, self.variant, 0.02, 0.0))
        cell = build_interaction_cell("breakout", self.bucket, self.variant, (row,), policy=InteractionPolicy(minimum_observations=3))
        self.assertEqual(cell.status, InteractionStatus.INSUFFICIENT)
        self.assertIsNone(cell.mean_net_return_delta)

    def test_no_forecast_control_cannot_be_interaction_subject(self) -> None:
        with self.assertRaises(ValueError):
            build_interaction_cell("breakout", self.bucket, ForecastAblationVariant(()), ())

    def test_context_and_family_do_not_cross_contaminate(self) -> None:
        other = ForecastContextBucket("GBPUSD", "M15", "london", "trend", 4)
        row = StrategyForecastInteractionObservation("breakout", other, comparison(0, self.variant, 0.02, 0.0))
        cell = build_interaction_cell("breakout", self.bucket, self.variant, (row,), policy=InteractionPolicy(minimum_observations=1))
        self.assertEqual(cell.status, InteractionStatus.INSUFFICIENT)
        self.assertEqual(cell.observation_count, 0)

    def test_duplicate_strategy_evaluation_identity_fails_closed(self) -> None:
        comp = comparison(0, self.variant, 0.01, 0.0)
        row1 = StrategyForecastInteractionObservation("breakout", self.bucket, comp)
        row2 = StrategyForecastInteractionObservation("breakout", self.bucket, comp)
        with self.assertRaises(ValueError):
            build_interaction_cell("breakout", self.bucket, self.variant, (row1, row2), policy=InteractionPolicy(minimum_observations=1))


if __name__ == "__main__":
    unittest.main()
