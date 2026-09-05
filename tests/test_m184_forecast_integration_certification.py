from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unittest

from dusty.disagreement_atlas import DisagreementAtlasCell, DisagreementAtlasStatus
from dusty.forecast_ablation import AblationEffect, ForecastAblationComparison, ForecastAblationVariant
from dusty.forecast_calibration_memory import CalibrationMemoryStatus, ForecastCalibrationMemory
from dusty.forecast_evidence_weighting import EvidenceWeightStatus, weigh_forecast_evidence
from dusty.forecast_integration_certification import (
    ForecastIntegrationPolicy,
    ForecastIntegrationStatus,
    certify_forecast_integration,
)
from dusty.forecast_interaction_map import InteractionStatus, StrategyForecastInteractionCell
from dusty.forecast_research import DisagreementState
from dusty.forecast_specialization import ForecastContextBucket, ProviderSpecialization, SpecializationStatus
from dusty.forecast_value import ForecastInformationValue, InformationValueStatus
from dusty.robustness_gate import RobustnessCertification, RobustnessGateStatus


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M184ForecastIntegrationCertificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = fp("strategy")
        self.evaluation = fp("evaluation")
        self.execution_cost = fp("execution-cost")
        self.bucket = ForecastContextBucket("EURUSD", "M15", "london", "trend", 4)
        self.variant = ForecastAblationVariant(("chronos2",))
        self.robustness = RobustnessCertification(
            RobustnessGateStatus.SERIOUS_CHALLENGER,
            (
                ("broker_calibration", "calibrated"),
                ("walk_forward", "pass_fraction=0.9"),
                ("parameter_neighborhood", "stable"),
                ("regime_torture", "passed"),
                ("cost_torture", "passed=True"),
                ("historical_forward_decay", "retention=0.75"),
                ("tail_risk", "dd=0.12;cvar=0.08"),
                ("strategy_dependency", "diversified"),
            ),
            (),
            tuple(fp(f"robustness-{index}") for index in range(8)),
        )

    def specialization(self) -> ProviderSpecialization:
        return ProviderSpecialization(
            "chronos2",
            self.bucket,
            SpecializationStatus.SPECIALIST,
            40,
            0.2,
            1.0,
            0.8,
            0.9,
            0.8,
        )

    def calibration(self) -> ForecastCalibrationMemory:
        return ForecastCalibrationMemory(
            "chronos2",
            self.bucket,
            CalibrationMemoryStatus.MEASURED,
            40,
            0.0,
            0.2,
            1.0,
            0.81,
            0.90,
            0.80,
            0.02,
            tuple(fp(f"calibration-{index}") for index in range(40)),
        )

    def disagreement(self, accuracy: float = 0.95) -> DisagreementAtlasCell:
        return DisagreementAtlasCell(
            self.bucket,
            DisagreementState.UNANIMOUS_UP,
            DisagreementAtlasStatus.MEASURED,
            30,
            "up",
            accuracy,
            0.01,
            tuple(fp(f"disagreement-{index}") for index in range(30)),
        )

    def ablation(
        self,
        *,
        effect: AblationEffect = AblationEffect.BENEFICIAL,
        delta: float = 0.02,
    ) -> ForecastAblationComparison:
        return ForecastAblationComparison(
            self.strategy,
            self.evaluation,
            self.execution_cost,
            self.variant,
            fp("control-result"),
            fp("forecast-result"),
            effect,
            delta,
            -0.01,
            2,
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
            5.0,
            comparison.net_return_delta / 5.0,
            3.0,
            0.0,
        )

    def interaction(
        self,
        *,
        status: InteractionStatus = InteractionStatus.BENEFICIAL,
        mean_delta: float = 0.02,
    ) -> StrategyForecastInteractionCell:
        measured = status is not InteractionStatus.INSUFFICIENT
        observation_count = 3
        observations = tuple(fp(f"interaction-{index}") for index in range(observation_count))
        return StrategyForecastInteractionCell(
            "breakout",
            self.bucket,
            self.variant,
            status,
            observation_count,
            mean_delta if measured else None,
            -0.01 if measured else None,
            0.0 if measured else None,
            1.0 if measured else None,
            0.0 if measured else None,
            observations,
        )

    def package(
        self,
        *,
        effect: AblationEffect = AblationEffect.BENEFICIAL,
        delta: float = 0.02,
        value_status: InformationValueStatus = InformationValueStatus.POSITIVE,
        interaction_status: InteractionStatus = InteractionStatus.BENEFICIAL,
        interaction_delta: float = 0.02,
        disagreement_accuracy: float = 0.95,
    ):
        comparison = self.ablation(effect=effect, delta=delta)
        information = self.information_value(comparison, status=value_status)
        interaction = self.interaction(status=interaction_status, mean_delta=interaction_delta)
        weight = weigh_forecast_evidence(
            self.variant,
            self.bucket,
            (self.specialization(),),
            (self.calibration(),),
            disagreement=self.disagreement(disagreement_accuracy),
            ablation=comparison,
            information_value=information,
            interaction=interaction,
        )
        return comparison, information, interaction, weight

    def certify(self, *, robustness=None, policy_weight: float = 0.50, package=None):
        comparison, information, interaction, weight = self.package() if package is None else package
        return certify_forecast_integration(
            strategy_fingerprint=self.strategy,
            evaluation_fingerprint=self.evaluation,
            robustness=self.robustness if robustness is None else robustness,
            evidence_weight=weight,
            ablation=comparison,
            information_value=information,
            interaction=interaction,
            policy=ForecastIntegrationPolicy(policy_weight),
        )

    def test_exact_strong_package_is_research_integration_eligible_only(self) -> None:
        first = self.certify()
        second = self.certify()
        self.assertEqual(first.status, ForecastIntegrationStatus.RESEARCH_INTEGRATION_ELIGIBLE)
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.blockers, ())
        self.assertFalse(first.broker_write_authority)
        self.assertFalse(first.provider_selection_authority)
        self.assertFalse(first.strategy_mutation_authority)
        self.assertFalse(first.champion_promotion_authority)
        self.assertFalse(first.guardian_override_authority)
        self.assertFalse(first.risk_override_authority)
        self.assertFalse(first.directional_vote_authority)
        self.assertFalse(first.allocation_authority)
        self.assertFalse(first.operational_forecast_certification)

    def test_constrained_weight_can_be_eligible_only_when_explicit_policy_allows_it(self) -> None:
        package = self.package(disagreement_accuracy=0.80)
        self.assertEqual(package[3].status, EvidenceWeightStatus.CONSTRAINED)
        allowed = self.certify(package=package, policy_weight=0.75)
        denied = self.certify(package=package, policy_weight=0.85)
        self.assertEqual(allowed.status, ForecastIntegrationStatus.RESEARCH_INTEGRATION_ELIGIBLE)
        self.assertEqual(denied.status, ForecastIntegrationStatus.REJECTED)
        self.assertIn("adaptive_evidence_below_policy_threshold", denied.blockers)

    def test_pending_robustness_is_insufficient_not_eligible(self) -> None:
        pending = replace(
            self.robustness,
            status=RobustnessGateStatus.PENDING,
            blockers=("broker_calibration",),
        )
        result = self.certify(robustness=pending)
        self.assertEqual(result.status, ForecastIntegrationStatus.INSUFFICIENT)
        self.assertIn("strategy_robustness_pending", result.blockers)

    def test_rejected_robustness_is_rejected(self) -> None:
        rejected = replace(
            self.robustness,
            status=RobustnessGateStatus.REJECTED,
            blockers=("cost_torture",),
        )
        result = self.certify(robustness=rejected)
        self.assertEqual(result.status, ForecastIntegrationStatus.REJECTED)
        self.assertIn("strategy_robustness_rejected", result.blockers)

    def test_neutral_ablation_and_information_value_cannot_certify(self) -> None:
        package = self.package(
            effect=AblationEffect.NEUTRAL,
            delta=0.0,
            value_status=InformationValueStatus.NEUTRAL,
        )
        result = self.certify(package=package, policy_weight=0.20)
        self.assertEqual(result.status, ForecastIntegrationStatus.REJECTED)
        self.assertIn("matched_ablation_not_beneficial", result.blockers)
        self.assertIn("information_value_not_positive", result.blockers)

    def test_harmful_interaction_cannot_certify(self) -> None:
        package = self.package(
            interaction_status=InteractionStatus.HARMFUL,
            interaction_delta=-0.02,
        )
        result = self.certify(package=package, policy_weight=0.0)
        self.assertEqual(result.status, ForecastIntegrationStatus.REJECTED)
        self.assertIn("strategy_interaction_not_beneficial", result.blockers)
        self.assertIn("adaptive_evidence_blocked", result.blockers)

    def test_insufficient_interaction_remains_insufficient_when_no_measured_failure_exists(self) -> None:
        package = self.package(
            interaction_status=InteractionStatus.INSUFFICIENT,
            interaction_delta=0.0,
        )
        result = self.certify(package=package, policy_weight=0.40)
        self.assertEqual(result.status, ForecastIntegrationStatus.INSUFFICIENT)
        self.assertIn("strategy_interaction_insufficient", result.blockers)

    def test_strategy_or_evaluation_identity_drift_fails_closed(self) -> None:
        comparison, information, interaction, weight = self.package()
        with self.assertRaises(ValueError):
            certify_forecast_integration(
                strategy_fingerprint=fp("different-strategy"),
                evaluation_fingerprint=self.evaluation,
                robustness=self.robustness,
                evidence_weight=weight,
                ablation=comparison,
                information_value=information,
                interaction=interaction,
                policy=ForecastIntegrationPolicy(0.50),
            )
        with self.assertRaises(ValueError):
            certify_forecast_integration(
                strategy_fingerprint=self.strategy,
                evaluation_fingerprint=fp("different-evaluation"),
                robustness=self.robustness,
                evidence_weight=weight,
                ablation=comparison,
                information_value=information,
                interaction=interaction,
                policy=ForecastIntegrationPolicy(0.50),
            )

    def test_m184_requires_exact_evidence_package_weighed_by_m183(self) -> None:
        comparison, information, interaction, weight = self.package()
        broken_weight = replace(
            weight,
            source_fingerprints=tuple(
                value for value in weight.source_fingerprints if value != interaction.fingerprint
            ),
        )
        with self.assertRaises(ValueError):
            certify_forecast_integration(
                strategy_fingerprint=self.strategy,
                evaluation_fingerprint=self.evaluation,
                robustness=self.robustness,
                evidence_weight=broken_weight,
                ablation=comparison,
                information_value=information,
                interaction=interaction,
                policy=ForecastIntegrationPolicy(0.50),
            )

    def test_malformed_weight_component_arithmetic_fails_closed(self) -> None:
        comparison, information, interaction, weight = self.package()
        malformed = replace(weight, weight=0.99)
        with self.assertRaises(ValueError):
            certify_forecast_integration(
                strategy_fingerprint=self.strategy,
                evaluation_fingerprint=self.evaluation,
                robustness=self.robustness,
                evidence_weight=malformed,
                ablation=comparison,
                information_value=information,
                interaction=interaction,
                policy=ForecastIntegrationPolicy(0.50),
            )

    def test_malformed_interaction_count_fails_closed(self) -> None:
        comparison, information, interaction, weight = self.package()
        malformed_interaction = replace(interaction, observation_count=4)
        broken_weight = replace(
            weight,
            source_fingerprints=tuple(
                sorted(
                    {
                        value
                        for value in weight.source_fingerprints
                        if value != interaction.fingerprint
                    }
                    | {malformed_interaction.fingerprint}
                )
            ),
        )
        with self.assertRaises(ValueError):
            certify_forecast_integration(
                strategy_fingerprint=self.strategy,
                evaluation_fingerprint=self.evaluation,
                robustness=self.robustness,
                evidence_weight=broken_weight,
                ablation=comparison,
                information_value=information,
                interaction=malformed_interaction,
                policy=ForecastIntegrationPolicy(0.50),
            )

    def test_policy_threshold_is_explicit_and_bounded(self) -> None:
        with self.assertRaises(ValueError):
            ForecastIntegrationPolicy(-0.01)
        with self.assertRaises(ValueError):
            ForecastIntegrationPolicy(1.01)
        valid = ForecastIntegrationPolicy(1.0)
        self.assertEqual(valid.minimum_evidence_weight, 1.0)


if __name__ == "__main__":
    unittest.main()
