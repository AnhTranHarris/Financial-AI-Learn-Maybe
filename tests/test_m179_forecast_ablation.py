from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.forecast_ablation import (
    AblationEffect,
    AblationPolicy,
    ForecastAblationResult,
    ForecastAblationVariant,
    compare_forecast_ablations,
)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def result(providers: tuple[str, ...], ret: float, dd: float, trades: int = 20, passed: bool = True, *, evaluation: str = "eval") -> ForecastAblationResult:
    return ForecastAblationResult(fp("strategy"), fp(evaluation), fp("costs"), ForecastAblationVariant(providers), ret, dd, trades, passed)


class M179ForecastAblationLaboratoryTests(unittest.TestCase):
    def test_matched_control_exposes_benefit_and_harm_without_hiding_drawdown(self) -> None:
        rows = (
            result((), 0.02, 0.08),
            result(("chronos2",), 0.03, 0.10),
            result(("kronos-small",), 0.01, 0.06),
        )
        comparisons = compare_forecast_ablations(rows)
        by_provider = {row.variant.providers: row for row in comparisons}
        self.assertEqual(by_provider[("chronos2",)].effect, AblationEffect.BENEFICIAL)
        self.assertAlmostEqual(by_provider[("chronos2",)].net_return_delta, 0.01)
        self.assertAlmostEqual(by_provider[("chronos2",)].max_drawdown_delta, 0.02)
        self.assertEqual(by_provider[("kronos-small",)].effect, AblationEffect.HARMFUL)
        self.assertFalse(by_provider[("chronos2",)].broker_write_authority)
        self.assertFalse(by_provider[("chronos2",)].strategy_mutation_authority)

    def test_no_forecast_control_is_mandatory(self) -> None:
        with self.assertRaises(ValueError):
            compare_forecast_ablations((result(("chronos2",), 0.03, 0.1), result(("kronos-small",), 0.02, 0.1)))

    def test_different_evaluation_or_cost_identity_cannot_be_compared(self) -> None:
        with self.assertRaises(ValueError):
            compare_forecast_ablations((result((), 0.02, 0.08), result(("chronos2",), 0.03, 0.1, evaluation="other")))
        other_cost = ForecastAblationResult(fp("strategy"), fp("eval"), fp("other-cost"), ForecastAblationVariant(("chronos2",)), 0.03, 0.1, 20, True)
        with self.assertRaises(ValueError):
            compare_forecast_ablations((result((), 0.02, 0.08), other_cost))

    def test_duplicate_variant_fails_closed(self) -> None:
        variant = result(("chronos2",), 0.03, 0.1)
        with self.assertRaises(ValueError):
            compare_forecast_ablations((result((), 0.02, 0.08), variant, variant))

    def test_explicit_neutral_band_prevents_noise_from_becoming_benefit(self) -> None:
        comparisons = compare_forecast_ablations((result((), 0.02, 0.08), result(("timesfm-2.5",), 0.0205, 0.08)), policy=AblationPolicy(neutral_return_delta=0.001))
        self.assertEqual(comparisons[0].effect, AblationEffect.NEUTRAL)

    def test_provider_sets_are_canonical_and_known(self) -> None:
        self.assertEqual(ForecastAblationVariant(("timesfm-2.5", "chronos2")).providers, ("chronos2", "timesfm-2.5"))
        with self.assertRaises(ValueError):
            ForecastAblationVariant(("unknown",))


if __name__ == "__main__":
    unittest.main()
