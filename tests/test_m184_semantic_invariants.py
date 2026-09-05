from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unittest

from dusty.forecast_ablation import ForecastAblationComparison, ForecastAblationVariant, AblationEffect
from dusty.forecast_evidence_weighting import AdaptiveForecastEvidenceWeight, EvidenceWeightStatus
from dusty.forecast_integration_certification import _validate_information_value, _validate_weight
from dusty.forecast_specialization import ForecastContextBucket
from dusty.forecast_value import ForecastInformationValue, InformationValueStatus


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M184SemanticInvariantTests(unittest.TestCase):
    def weight(self) -> AdaptiveForecastEvidenceWeight:
        variant = ForecastAblationVariant(("chronos2",))
        bucket = ForecastContextBucket("EURUSD", "M15", "london", "trend", 4)
        return AdaptiveForecastEvidenceWeight(
            variant,
            bucket,
            EvidenceWeightStatus.CONSTRAINED,
            0.50,
            (("chronos2", 0.80),),
            (
                ("provider_quality", 0.80),
                ("disagreement", 0.90),
                ("economic", 0.75),
                ("strategy_interaction", 0.50),
                ("failure_memory", 1.00),
            ),
            (),
            (fp("source"),),
            fp("policy"),
        )

    def comparison(self, delta: float) -> ForecastAblationComparison:
        return ForecastAblationComparison(
            fp("strategy"),
            fp("evaluation"),
            fp("execution-cost"),
            ForecastAblationVariant(("chronos2",)),
            fp("control"),
            fp("forecast"),
            AblationEffect.BENEFICIAL if delta > 0 else AblationEffect.HARMFUL,
            delta,
            0.0,
            0,
            False,
        )

    def information(self, comparison: ForecastAblationComparison, status: InformationValueStatus) -> ForecastInformationValue:
        return ForecastInformationValue(
            comparison.fingerprint,
            fp("cost"),
            status,
            comparison.net_return_delta,
            1.0,
            comparison.net_return_delta,
            1.0,
            0.0,
        )

    def test_zero_weight_status_cannot_carry_positive_weight(self) -> None:
        malformed = replace(self.weight(), status=EvidenceWeightStatus.BLOCKED)
        with self.assertRaisesRegex(ValueError, "status/weight identity drift"):
            _validate_weight(malformed)

    def test_positive_weight_status_cannot_carry_zero_weight(self) -> None:
        base = self.weight()
        zero_caps = tuple((name, 0.0 if name == "strategy_interaction" else value) for name, value in base.component_caps)
        malformed = replace(base, status=EvidenceWeightStatus.CONSTRAINED, weight=0.0, component_caps=zero_caps)
        with self.assertRaisesRegex(ValueError, "status/weight identity drift"):
            _validate_weight(malformed)

    def test_negative_information_status_cannot_label_positive_return_delta(self) -> None:
        comparison = self.comparison(0.02)
        malformed = self.information(comparison, InformationValueStatus.NEGATIVE)
        with self.assertRaisesRegex(ValueError, "negative information value requires negative"):
            _validate_information_value(malformed, comparison)

    def test_positive_information_status_cannot_label_negative_return_delta(self) -> None:
        comparison = self.comparison(-0.02)
        malformed = self.information(comparison, InformationValueStatus.POSITIVE)
        with self.assertRaisesRegex(ValueError, "positive information value requires positive"):
            _validate_information_value(malformed, comparison)


if __name__ == "__main__":
    unittest.main()
