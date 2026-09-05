from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.forecast_ablation import AblationEffect, ForecastAblationComparison, ForecastAblationVariant
from dusty.forecast_value import ForecastInformationCost, InformationValuePolicy, InformationValueStatus, measure_information_value


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def comparison(delta: float, effect: AblationEffect) -> ForecastAblationComparison:
    variant = ForecastAblationVariant(("chronos2",))
    return ForecastAblationComparison(fp("strategy"), fp("eval"), fp("cost"), variant, fp("control"), fp("variant"), effect, delta, 0.01, 0, False)


class M180ValueOfInformationTests(unittest.TestCase):
    def test_positive_matched_uplift_has_positive_research_value(self) -> None:
        row = comparison(0.02, AblationEffect.BENEFICIAL)
        cost = ForecastInformationCost(row.variant.fingerprint, wall_seconds=4.0, cpu_seconds=2.0, gpu_seconds=1.0)
        value = measure_information_value(row, cost, policy=InformationValuePolicy(gpu_second_weight=2.0))
        self.assertEqual(value.status, InformationValueStatus.POSITIVE)
        self.assertAlmostEqual(value.weighted_compute_seconds, 4.0)
        self.assertAlmostEqual(value.value_per_compute_second, 0.005)
        self.assertFalse(value.broker_write_authority)
        self.assertFalse(value.allocation_authority)

    def test_harmful_ablation_cannot_become_positive_information_value(self) -> None:
        row = comparison(-0.01, AblationEffect.HARMFUL)
        cost = ForecastInformationCost(row.variant.fingerprint, wall_seconds=1.0, cpu_seconds=1.0, gpu_seconds=0.0)
        value = measure_information_value(row, cost)
        self.assertEqual(value.status, InformationValueStatus.NEGATIVE)
        self.assertLess(value.value_per_compute_second, 0)

    def test_variant_identity_drift_fails_closed(self) -> None:
        row = comparison(0.02, AblationEffect.BENEFICIAL)
        cost = ForecastInformationCost(ForecastAblationVariant(("timesfm-2.5",)).fingerprint, 1.0, 1.0, 0.0)
        with self.assertRaises(ValueError):
            measure_information_value(row, cost)

    def test_external_cost_is_explicit_and_does_not_silently_change_uplift(self) -> None:
        row = comparison(0.02, AblationEffect.BENEFICIAL)
        free = measure_information_value(row, ForecastInformationCost(row.variant.fingerprint, 1, 1, 0, 0))
        paid = measure_information_value(row, ForecastInformationCost(row.variant.fingerprint, 1, 1, 0, 5))
        self.assertEqual(free.net_return_delta, paid.net_return_delta)
        self.assertEqual(paid.external_cost, 5)
        self.assertNotEqual(free.fingerprint, paid.fingerprint)

    def test_neutral_band_prevents_tiny_noise_from_becoming_value(self) -> None:
        row = comparison(0.0001, AblationEffect.NEUTRAL)
        cost = ForecastInformationCost(row.variant.fingerprint, 1, 1, 0)
        value = measure_information_value(row, cost, policy=InformationValuePolicy(neutral_return_delta=0.001))
        self.assertEqual(value.status, InformationValueStatus.NEUTRAL)


if __name__ == "__main__":
    unittest.main()
