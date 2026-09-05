from __future__ import annotations

"""M184 fail-closed forecast integration research certification.

This module certifies whether an already-robust strategy may consume a bounded
forecast evidence weight as a *research feature* for one frozen strategy,
evaluation, context bucket, and provider variant.  It deliberately does not
certify a provider globally and grants no execution, promotion, allocation,
directional-vote, Guardian, or risk authority.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math

from .forecast_ablation import AblationEffect, ForecastAblationComparison
from .forecast_evidence_weighting import AdaptiveForecastEvidenceWeight, EvidenceWeightStatus
from .forecast_interaction_map import InteractionStatus, StrategyForecastInteractionCell
from .forecast_value import ForecastInformationValue, InformationValueStatus
from .robustness_gate import RobustnessCertification, RobustnessGateStatus


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


def _unit(value: float, label: str) -> float:
    rendered = _finite(value, label)
    if not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{label} must be in [0,1]")
    return rendered


class ForecastIntegrationStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    REJECTED = "rejected"
    RESEARCH_INTEGRATION_ELIGIBLE = "research_integration_eligible"


@dataclass(frozen=True, slots=True)
class ForecastIntegrationPolicy:
    minimum_evidence_weight: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "minimum_evidence_weight",
            _unit(self.minimum_evidence_weight, "minimum_evidence_weight"),
        )

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m184-forecast-integration-policy-v1", self.minimum_evidence_weight))


@dataclass(frozen=True, slots=True)
class ForecastIntegrationCertification:
    strategy_fingerprint: str
    evaluation_fingerprint: str
    execution_cost_fingerprint: str
    strategy_family: str
    variant_fingerprint: str
    bucket_fingerprint: str
    status: ForecastIntegrationStatus
    checks: tuple[tuple[str, str], ...]
    blockers: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    policy_fingerprint: str

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m184-forecast-integration-certification-v1",
                self.strategy_fingerprint,
                self.evaluation_fingerprint,
                self.execution_cost_fingerprint,
                self.strategy_family,
                self.variant_fingerprint,
                self.bucket_fingerprint,
                self.status.value,
                self.checks,
                self.blockers,
                self.evidence_fingerprints,
                self.policy_fingerprint,
            )
        )

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def provider_selection_authority(self) -> bool:
        return False

    @property
    def strategy_mutation_authority(self) -> bool:
        return False

    @property
    def champion_promotion_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def directional_vote_authority(self) -> bool:
        return False

    @property
    def allocation_authority(self) -> bool:
        return False

    @property
    def operational_forecast_certification(self) -> bool:
        return False


def _validate_robustness(certification: RobustnessCertification) -> None:
    expected_checks = {
        "broker_calibration",
        "walk_forward",
        "parameter_neighborhood",
        "regime_torture",
        "cost_torture",
        "historical_forward_decay",
        "tail_risk",
        "strategy_dependency",
    }
    names = tuple(name for name, _ in certification.checks)
    if len(names) != len(set(names)) or set(names) != expected_checks:
        raise ValueError("robustness certification check identity drift")
    evidence = tuple(_sha(value, "robustness evidence fingerprint") for value in certification.evidence_fingerprints)
    if not evidence or len(evidence) != len(set(evidence)):
        raise ValueError("robustness certification requires unique evidence fingerprints")
    if certification.status is RobustnessGateStatus.SERIOUS_CHALLENGER and certification.blockers:
        raise ValueError("serious challenger cannot carry robustness blockers")
    if certification.status is not RobustnessGateStatus.SERIOUS_CHALLENGER and not certification.blockers:
        raise ValueError("non-passing robustness certification requires blockers")


def _validate_weight(weight: AdaptiveForecastEvidenceWeight) -> None:
    if weight.variant.is_control:
        raise ValueError("NO_FORECAST control cannot receive integration certification")
    rendered_weight = _unit(weight.weight, "evidence weight")
    providers = tuple(provider for provider, _ in weight.provider_qualities)
    if providers != weight.variant.providers or len(providers) != len(set(providers)):
        raise ValueError("evidence-weight provider identity drift")
    for provider, quality in weight.provider_qualities:
        if not provider.strip():
            raise ValueError("evidence-weight provider identity is empty")
        _unit(quality, f"provider quality:{provider}")

    expected_caps = {
        "provider_quality",
        "disagreement",
        "economic",
        "strategy_interaction",
        "failure_memory",
    }
    cap_names = tuple(name for name, _ in weight.component_caps)
    if len(cap_names) != len(set(cap_names)) or set(cap_names) != expected_caps:
        raise ValueError("evidence-weight component identity drift")
    caps = tuple(_unit(value, f"component cap:{name}") for name, value in weight.component_caps)
    if not math.isclose(rendered_weight, min(caps), rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("evidence weight must equal its most conservative component cap")

    fingerprints = tuple(_sha(value, "M183 source fingerprint") for value in weight.source_fingerprints)
    if not fingerprints or len(fingerprints) != len(set(fingerprints)):
        raise ValueError("M183 source fingerprints must be unique and nonempty")
    _sha(weight.policy_fingerprint, "M183 policy fingerprint")


def _validate_ablation(
    comparison: ForecastAblationComparison,
    *,
    strategy_fingerprint: str,
    evaluation_fingerprint: str,
    weight: AdaptiveForecastEvidenceWeight,
) -> None:
    fields = (
        (comparison.strategy_fingerprint, "ablation strategy fingerprint"),
        (comparison.evaluation_fingerprint, "ablation evaluation fingerprint"),
        (comparison.execution_cost_fingerprint, "ablation execution-cost fingerprint"),
        (comparison.control_fingerprint, "ablation control fingerprint"),
        (comparison.variant_result_fingerprint, "ablation variant-result fingerprint"),
    )
    for value, label in fields:
        _sha(value, label)
    if comparison.strategy_fingerprint.lower() != strategy_fingerprint:
        raise ValueError("ablation strategy identity drift")
    if comparison.evaluation_fingerprint.lower() != evaluation_fingerprint:
        raise ValueError("ablation evaluation identity drift")
    if comparison.variant != weight.variant:
        raise ValueError("ablation/M183 forecast variant identity drift")
    _finite(comparison.net_return_delta, "ablation net_return_delta")
    _finite(comparison.max_drawdown_delta, "ablation max_drawdown_delta")
    if isinstance(comparison.trade_count_delta, bool) or int(comparison.trade_count_delta) != comparison.trade_count_delta:
        raise ValueError("ablation trade_count_delta must be integral")


def _validate_information_value(value: ForecastInformationValue, comparison: ForecastAblationComparison) -> None:
    _sha(value.comparison_fingerprint, "information-value comparison fingerprint")
    _sha(value.cost_fingerprint, "information-value cost fingerprint")
    if value.comparison_fingerprint.lower() != comparison.fingerprint:
        raise ValueError("information-value/ablation identity drift")
    if not math.isclose(
        _finite(value.net_return_delta, "information-value net_return_delta"),
        comparison.net_return_delta,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise ValueError("information-value return delta drift")
    weighted = _finite(value.weighted_compute_seconds, "weighted_compute_seconds")
    wall = _finite(value.wall_seconds, "wall_seconds")
    external = _finite(value.external_cost, "external_cost")
    _finite(value.value_per_compute_second, "value_per_compute_second")
    if weighted <= 0 or wall < 0 or external < 0:
        raise ValueError("information-value cost evidence is invalid")


def _validate_interaction(cell: StrategyForecastInteractionCell, weight: AdaptiveForecastEvidenceWeight) -> str:
    family = str(cell.strategy_family).strip().lower()
    if not family or "\n" in family or "\r" in family:
        raise ValueError("strategy interaction family must be one line")
    if cell.variant != weight.variant or cell.bucket != weight.bucket:
        raise ValueError("strategy interaction/M183 identity drift")
    if isinstance(cell.observation_count, bool) or int(cell.observation_count) != cell.observation_count or int(cell.observation_count) < 0:
        raise ValueError("strategy interaction observation_count must be nonnegative")
    fingerprints = tuple(_sha(value, "interaction observation fingerprint") for value in cell.observation_fingerprints)
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("strategy interaction observation fingerprints must be unique")
    if len(fingerprints) != int(cell.observation_count):
        raise ValueError("strategy interaction observation count/fingerprint drift")
    measured = cell.status is not InteractionStatus.INSUFFICIENT
    numeric_values = (
        cell.mean_net_return_delta,
        cell.mean_max_drawdown_delta,
        cell.worst_max_drawdown_delta,
        cell.beneficial_fraction,
        cell.harmful_fraction,
    )
    if measured and any(value is None for value in numeric_values):
        raise ValueError("measured strategy interaction is incomplete")
    if not measured and any(value is not None for value in numeric_values):
        raise ValueError("insufficient strategy interaction cannot carry measured metrics")
    if measured:
        assert cell.mean_net_return_delta is not None
        assert cell.mean_max_drawdown_delta is not None
        assert cell.worst_max_drawdown_delta is not None
        assert cell.beneficial_fraction is not None
        assert cell.harmful_fraction is not None
        _finite(cell.mean_net_return_delta, "interaction mean_net_return_delta")
        _finite(cell.mean_max_drawdown_delta, "interaction mean_max_drawdown_delta")
        _finite(cell.worst_max_drawdown_delta, "interaction worst_max_drawdown_delta")
        _unit(cell.beneficial_fraction, "interaction beneficial_fraction")
        _unit(cell.harmful_fraction, "interaction harmful_fraction")
        if cell.status is InteractionStatus.BENEFICIAL and cell.mean_net_return_delta <= 0:
            raise ValueError("beneficial interaction requires positive mean return delta")
        if cell.status is InteractionStatus.HARMFUL and cell.mean_net_return_delta >= 0:
            raise ValueError("harmful interaction requires negative mean return delta")
    return family


def certify_forecast_integration(
    *,
    strategy_fingerprint: str,
    evaluation_fingerprint: str,
    robustness: RobustnessCertification,
    evidence_weight: AdaptiveForecastEvidenceWeight,
    ablation: ForecastAblationComparison,
    information_value: ForecastInformationValue,
    interaction: StrategyForecastInteractionCell,
    policy: ForecastIntegrationPolicy,
) -> ForecastIntegrationCertification:
    """Certify research-feature integration for one frozen evidence package."""

    strategy = _sha(strategy_fingerprint, "strategy_fingerprint")
    evaluation = _sha(evaluation_fingerprint, "evaluation_fingerprint")
    _validate_robustness(robustness)
    _validate_weight(evidence_weight)
    _validate_ablation(
        ablation,
        strategy_fingerprint=strategy,
        evaluation_fingerprint=evaluation,
        weight=evidence_weight,
    )
    _validate_information_value(information_value, ablation)
    family = _validate_interaction(interaction, evidence_weight)

    required_links = {ablation.fingerprint, information_value.fingerprint, interaction.fingerprint}
    if not required_links.issubset(set(evidence_weight.source_fingerprints)):
        raise ValueError("M184 evidence package is not the package weighed by M183")

    checks: list[tuple[str, str]] = []
    rejected: list[str] = []
    insufficient: list[str] = []

    checks.append(("strategy_robustness", robustness.status.value))
    if robustness.status is RobustnessGateStatus.PENDING:
        insufficient.append("strategy_robustness_pending")
    elif robustness.status is RobustnessGateStatus.REJECTED:
        rejected.append("strategy_robustness_rejected")

    checks.append(("adaptive_evidence_weight", f"{evidence_weight.status.value};weight={evidence_weight.weight:.12g}"))
    if evidence_weight.status is EvidenceWeightStatus.INSUFFICIENT:
        insufficient.append("adaptive_evidence_insufficient")
    elif evidence_weight.status is EvidenceWeightStatus.BLOCKED:
        rejected.append("adaptive_evidence_blocked")
    if evidence_weight.weight < policy.minimum_evidence_weight:
        rejected.append("adaptive_evidence_below_policy_threshold")

    checks.append(("matched_ablation", ablation.effect.value))
    if ablation.effect is not AblationEffect.BENEFICIAL:
        rejected.append("matched_ablation_not_beneficial")

    checks.append(("information_value", information_value.status.value))
    if information_value.status is not InformationValueStatus.POSITIVE:
        rejected.append("information_value_not_positive")

    checks.append(("strategy_interaction", interaction.status.value))
    if interaction.status is InteractionStatus.INSUFFICIENT:
        insufficient.append("strategy_interaction_insufficient")
    elif interaction.status is not InteractionStatus.BENEFICIAL:
        rejected.append("strategy_interaction_not_beneficial")

    if rejected:
        status = ForecastIntegrationStatus.REJECTED
        blockers = tuple(sorted(set(rejected + insufficient)))
    elif insufficient:
        status = ForecastIntegrationStatus.INSUFFICIENT
        blockers = tuple(sorted(set(insufficient)))
    else:
        status = ForecastIntegrationStatus.RESEARCH_INTEGRATION_ELIGIBLE
        blockers = ()

    evidence = tuple(
        sorted(
            {
                robustness.fingerprint,
                evidence_weight.fingerprint,
                ablation.fingerprint,
                information_value.fingerprint,
                interaction.fingerprint,
            }
        )
    )
    return ForecastIntegrationCertification(
        strategy,
        evaluation,
        _sha(ablation.execution_cost_fingerprint, "execution_cost_fingerprint"),
        family,
        evidence_weight.variant.fingerprint,
        evidence_weight.bucket.fingerprint,
        status,
        tuple(checks),
        blockers,
        evidence,
        policy.fingerprint,
    )
