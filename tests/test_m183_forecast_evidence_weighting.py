from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.disagreement_atlas import DisagreementAtlasCell, DisagreementAtlasStatus
from dusty.forecast_ablation import AblationEffect, ForecastAblationComparison, ForecastAblationVariant
from dusty.forecast_calibration_memory import CalibrationMemoryStatus, ForecastCalibrationMemory
from dusty.forecast_evidence_weighting import EvidenceWeightStatus, weigh_forecast_evidence
from dusty.forecast_failure_memory import FailurePatternStatus, ForecastFailureKind, ForecastFailurePattern
from dusty.forecast_interaction_map import InteractionStatus, StrategyForecastInteractionCell
from dusty.forecast_research import DisagreementState
from dusty.forecast_specialization import ForecastContextBucket, ProviderSpecialization, SpecializationStatus
from dusty.forecast_value import ForecastInformationValue, InformationValueStatus


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M183AdaptiveForecastEvidenceWeightingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bucket = ForecastContextBucket("EURUSD", "M15", "london", "trend", 4)
        self.variant = ForecastAblationVariant(("chronos2",))

    def specialization(self, provider: str = "chronos2", status: SpecializationStatus = SpecializationStatus.SPECIALIST) -> ProviderSpecialization:
        return ProviderSpecialization(provider, self.bucket, status, 40, 0.2, 1.0, 0.8, 0.9, 0.8)

    def calibration(
        self,
        provider: str = "chronos2",
        *,
        status: CalibrationMemoryStatus = CalibrationMemoryStatus.MEASURED,
        skill: float = 0.81,
        direction: float = 0.90,
        coverage_error: float = 0.02,
        bucket: ForecastContextBucket | None = None,
    ) -> ForecastCalibrationMemory:
        return ForecastCalibrationMemory(
            provider,
            self.bucket if bucket is None else bucket,
            status,
            40,
            0.0 if status is CalibrationMemoryStatus.MEASURED else None,
            0.2 if status is CalibrationMemoryStatus.MEASURED else None,
            1.0 if status is CalibrationMemoryStatus.MEASURED else None,
            skill if status is CalibrationMemoryStatus.MEASURED else None,
            direction if status is CalibrationMemoryStatus.MEASURED else None,
            0.8 if status is CalibrationMemoryStatus.MEASURED else None,
            coverage_error if status is CalibrationMemoryStatus.MEASURED else None,
            tuple(fp(f"cal-{provider}-{index}") for index in range(40)),
        )

    def disagreement(
        self,
        *,
        status: DisagreementAtlasStatus = DisagreementAtlasStatus.MEASURED,
        accuracy: float | None = 0.95,
        state: DisagreementState = DisagreementState.UNANIMOUS_UP,
    ) -> DisagreementAtlasCell:
        return DisagreementAtlasCell(
            self.bucket,
            state,
            status,
            30,
            "up" if accuracy is not None else None,
            accuracy if status is DisagreementAtlasStatus.MEASURED else None,
            0.01 if status is DisagreementAtlasStatus.MEASURED else None,
            (fp("disagreement-observation"),),
        )

    def ablation(
        self,
        *,
        effect: AblationEffect = AblationEffect.BENEFICIAL,
        delta: float = 0.02,
        variant: ForecastAblationVariant | None = None,
    ) -> ForecastAblationComparison:
        return ForecastAblationComparison(
            fp("strategy"),
            fp("evaluation"),
            fp("execution-cost"),
            self.variant if variant is None else variant,
            fp("control"),
            fp("forecast-result"),
            effect,
            delta,
            0.0,
            0,
            False,
        )

    def information_value(
        self,
        comparison: ForecastAblationComparison,
        *,
        status: InformationValueStatus = InformationValueStatus.POSITIVE,
    ) -> ForecastInformationValue:
        return ForecastInformationValue(
            comparison.fingerprint,
            fp("information-cost"),
            status,
            comparison.net_return_delta,
            1.0,
            comparison.net_return_delta,
            1.0,
            0.0,
        )

    def interaction(
        self,
        *,
        status: InteractionStatus = InteractionStatus.BENEFICIAL,
        variant: ForecastAblationVariant | None = None,
    ) -> StrategyForecastInteractionCell:
        measured = status is not InteractionStatus.INSUFFICIENT
        return StrategyForecastInteractionCell(
            "breakout",
            self.bucket,
            self.variant if variant is None else variant,
            status,
            4,
            0.02 if measured else None,
            0.0 if measured else None,
            0.0 if measured else None,
            1.0 if measured else None,
            0.0 if measured else None,
            (fp("interaction-observation"),),
        )

    def failure_pattern(self, kind: ForecastFailureKind, *, providers: tuple[str, ...] = ("chronos2",)) -> ForecastFailurePattern:
        return ForecastFailurePattern(
            providers,
            self.bucket,
            kind,
            FailurePatternStatus.RECURRENT,
            3,
            tuple(fp(f"failure-case-{kind.value}-{index}") for index in range(3)),
            tuple(fp(f"failure-event-{kind.value}-{index}") for index in range(3)),
        )

    def strong_weight(self, **overrides):
        comparison = overrides.pop("ablation", self.ablation())
        values = {
            "disagreement": self.disagreement(),
            "ablation": comparison,
            "information_value": self.information_value(comparison),
            "failure_patterns": (),
            "interaction": self.interaction(),
        }
        values.update(overrides)
        return weigh_forecast_evidence(
            self.variant,
            self.bucket,
            (self.specialization(),),
            (self.calibration(),),
            **values,
        )

    def test_strong_evidence_is_deterministic_bounded_and_has_no_authority(self) -> None:
        first = self.strong_weight()
        second = self.strong_weight()
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.status, EvidenceWeightStatus.WEIGHTED)
        self.assertGreater(first.weight, 0.0)
        self.assertLessEqual(first.weight, 1.0)
        self.assertFalse(first.broker_write_authority)
        self.assertFalse(first.provider_selection_authority)
        self.assertFalse(first.strategy_mutation_authority)
        self.assertFalse(first.guardian_override_authority)
        self.assertFalse(first.directional_vote_authority)
        self.assertFalse(first.allocation_authority)

    def test_weak_specialization_caps_calibrated_provider_instead_of_adding_votes(self) -> None:
        comparison = self.ablation()
        result = weigh_forecast_evidence(
            self.variant,
            self.bucket,
            (self.specialization(status=SpecializationStatus.WEAK),),
            (self.calibration(),),
            disagreement=self.disagreement(),
            ablation=comparison,
            information_value=self.information_value(comparison),
            interaction=self.interaction(),
        )
        self.assertLessEqual(result.weight, 0.35)
        self.assertIn("specialization_weak:chronos2", result.reason_codes)

    def test_sparse_provider_evidence_is_insufficient_not_high_confidence(self) -> None:
        comparison = self.ablation()
        result = weigh_forecast_evidence(
            self.variant,
            self.bucket,
            (self.specialization(),),
            (self.calibration(status=CalibrationMemoryStatus.INSUFFICIENT),),
            disagreement=self.disagreement(),
            ablation=comparison,
            information_value=self.information_value(comparison),
            interaction=self.interaction(),
        )
        self.assertEqual(result.status, EvidenceWeightStatus.INSUFFICIENT)
        self.assertEqual(result.weight, 0.0)

    def test_harmful_matched_ablation_blocks_weight_even_with_strong_forecast_metrics(self) -> None:
        harmful = self.ablation(effect=AblationEffect.HARMFUL, delta=-0.02)
        result = self.strong_weight(ablation=harmful, information_value=self.information_value(harmful, status=InformationValueStatus.NEGATIVE))
        self.assertEqual(result.status, EvidenceWeightStatus.BLOCKED)
        self.assertEqual(result.weight, 0.0)
        self.assertIn("matched_ablation_harmful", result.reason_codes)

    def test_negative_information_value_can_only_tighten_the_same_economic_evidence(self) -> None:
        neutral = self.ablation(effect=AblationEffect.NEUTRAL, delta=-0.001)
        result = self.strong_weight(ablation=neutral, information_value=self.information_value(neutral, status=InformationValueStatus.NEGATIVE))
        self.assertLessEqual(result.weight, 0.25)
        self.assertIn("information_value_negative_cap", result.reason_codes)

    def test_harmful_strategy_interaction_blocks_provider_popularity(self) -> None:
        result = self.strong_weight(interaction=self.interaction(status=InteractionStatus.HARMFUL))
        self.assertEqual(result.status, EvidenceWeightStatus.BLOCKED)
        self.assertEqual(result.weight, 0.0)
        self.assertIn("strategy_interaction_harmful", result.reason_codes)

    def test_recurrent_worse_than_no_change_failure_caps_weight(self) -> None:
        pattern = self.failure_pattern(ForecastFailureKind.WORSE_THAN_NO_CHANGE)
        result = self.strong_weight(failure_patterns=(pattern,))
        self.assertLessEqual(result.weight, 0.10)
        self.assertTrue(any(reason.startswith("recurrent_failure:worse_than_no_change") for reason in result.reason_codes))

    def test_duplicate_failure_pattern_cannot_inflate_or_distort_evidence(self) -> None:
        pattern = self.failure_pattern(ForecastFailureKind.WRONG_DIRECTION)
        with self.assertRaises(ValueError):
            self.strong_weight(failure_patterns=(pattern, pattern))

    def test_missing_matched_economic_and_interaction_evidence_prevents_high_weight(self) -> None:
        result = weigh_forecast_evidence(
            self.variant,
            self.bucket,
            (self.specialization(),),
            (self.calibration(),),
            disagreement=self.disagreement(),
        )
        self.assertEqual(result.status, EvidenceWeightStatus.CONSTRAINED)
        self.assertLessEqual(result.weight, 0.40)
        self.assertIn("matched_ablation_missing", result.reason_codes)
        self.assertIn("strategy_interaction_missing", result.reason_codes)

    def test_multi_provider_variant_uses_weakest_provider_quality_not_vote_count(self) -> None:
        variant = ForecastAblationVariant(("chronos2", "timesfm-2.5"))
        comparison = self.ablation(variant=variant)
        interaction = self.interaction(variant=variant)
        result = weigh_forecast_evidence(
            variant,
            self.bucket,
            (self.specialization("chronos2"), self.specialization("timesfm-2.5", SpecializationStatus.WEAK)),
            (self.calibration("chronos2"), self.calibration("timesfm-2.5")),
            disagreement=self.disagreement(),
            ablation=comparison,
            information_value=self.information_value(comparison),
            interaction=interaction,
        )
        self.assertLessEqual(result.weight, 0.35)
        self.assertEqual(tuple(provider for provider, _ in result.provider_qualities), variant.providers)

    def test_no_forecast_control_is_never_recast_as_a_low_weight_provider(self) -> None:
        with self.assertRaises(ValueError):
            weigh_forecast_evidence(ForecastAblationVariant(()), self.bucket, (), ())

    def test_bucket_identity_drift_fails_closed(self) -> None:
        other = ForecastContextBucket("GBPUSD", "M15", "london", "trend", 4)
        with self.assertRaises(ValueError):
            weigh_forecast_evidence(
                self.variant,
                self.bucket,
                (self.specialization(),),
                (self.calibration(bucket=other),),
            )

    def test_mixed_disagreement_has_no_invented_direction_and_only_caps_confidence(self) -> None:
        mixed = self.disagreement(accuracy=None, state=DisagreementState.MIXED_WITH_FLAT)
        result = self.strong_weight(disagreement=mixed)
        self.assertLessEqual(result.weight, 0.50)
        self.assertIn("disagreement_has_no_consensus", result.reason_codes)
        self.assertFalse(result.directional_vote_authority)

    def test_derived_evidence_never_raises_provider_quality(self) -> None:
        result = self.strong_weight()
        provider_cap = dict(result.component_caps)["provider_quality"]
        self.assertLessEqual(result.weight, provider_cap)

    def test_duplicate_provider_aggregate_is_rejected(self) -> None:
        comparison = self.ablation()
        with self.assertRaises(ValueError):
            weigh_forecast_evidence(
                self.variant,
                self.bucket,
                (self.specialization(), self.specialization()),
                (self.calibration(),),
                ablation=comparison,
            )


if __name__ == "__main__":
    unittest.main()
