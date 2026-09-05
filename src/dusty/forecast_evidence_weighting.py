from __future__ import annotations

"""M183 dependency-aware adaptive forecast evidence weighting.

The weighting surface is deliberately research-only.  It does not turn
forecast providers, disagreement, ablations, or failure memories into votes.
Derived evidence is applied as a set of conservative caps so the same
underlying experiment cannot manufacture confidence by appearing in several
milestones.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .disagreement_atlas import DisagreementAtlasCell, DisagreementAtlasStatus
from .forecast_ablation import AblationEffect, ForecastAblationComparison, ForecastAblationVariant
from .forecast_calibration_memory import CalibrationMemoryStatus, ForecastCalibrationMemory
from .forecast_failure_memory import FailurePatternStatus, ForecastFailureKind, ForecastFailurePattern
from .forecast_interaction_map import InteractionStatus, StrategyForecastInteractionCell
from .forecast_specialization import ForecastContextBucket, ProviderSpecialization, SpecializationStatus
from .forecast_value import ForecastInformationValue, InformationValueStatus


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


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


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class EvidenceWeightStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    BLOCKED = "blocked"
    CONSTRAINED = "constrained"
    WEIGHTED = "weighted"


@dataclass(frozen=True, slots=True)
class AdaptiveEvidencePolicy:
    """Auditable caps for M183.

    Values are intentionally policy, not learned parameters.  M184 can certify
    alternative policies, but M183 never tunes these values on the same cases it
    scores.
    """

    weak_specialization_cap: float = 0.35
    coverage_error_zero_at: float = 0.20
    missing_disagreement_cap: float = 0.70
    mixed_disagreement_cap: float = 0.50
    missing_economic_cap: float = 0.40
    neutral_ablation_cap: float = 0.35
    negative_information_cap: float = 0.25
    neutral_information_cap: float = 0.50
    missing_interaction_cap: float = 0.50
    neutral_interaction_cap: float = 0.35
    recurrent_wrong_direction_cap: float = 0.25
    recurrent_interval_miss_cap: float = 0.50
    recurrent_worse_than_no_change_cap: float = 0.10
    recurrent_unavailable_cap: float = 0.50
    recurrent_harmful_ablation_cap: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "weak_specialization_cap",
            "missing_disagreement_cap",
            "mixed_disagreement_cap",
            "missing_economic_cap",
            "neutral_ablation_cap",
            "negative_information_cap",
            "neutral_information_cap",
            "missing_interaction_cap",
            "neutral_interaction_cap",
            "recurrent_wrong_direction_cap",
            "recurrent_interval_miss_cap",
            "recurrent_worse_than_no_change_cap",
            "recurrent_unavailable_cap",
            "recurrent_harmful_ablation_cap",
        ):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        coverage = _finite(self.coverage_error_zero_at, "coverage_error_zero_at")
        if not 0.0 < coverage <= 1.0:
            raise ValueError("coverage_error_zero_at must be in (0,1]")
        object.__setattr__(self, "coverage_error_zero_at", coverage)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m183-adaptive-evidence-policy-v1",
            self.weak_specialization_cap,
            self.coverage_error_zero_at,
            self.missing_disagreement_cap,
            self.mixed_disagreement_cap,
            self.missing_economic_cap,
            self.neutral_ablation_cap,
            self.negative_information_cap,
            self.neutral_information_cap,
            self.missing_interaction_cap,
            self.neutral_interaction_cap,
            self.recurrent_wrong_direction_cap,
            self.recurrent_interval_miss_cap,
            self.recurrent_worse_than_no_change_cap,
            self.recurrent_unavailable_cap,
            self.recurrent_harmful_ablation_cap,
        ))


@dataclass(frozen=True, slots=True)
class AdaptiveForecastEvidenceWeight:
    variant: ForecastAblationVariant
    bucket: ForecastContextBucket
    status: EvidenceWeightStatus
    weight: float
    provider_qualities: tuple[tuple[str, float], ...]
    component_caps: tuple[tuple[str, float], ...]
    reason_codes: tuple[str, ...]
    source_fingerprints: tuple[str, ...]
    policy_fingerprint: str

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m183-adaptive-forecast-evidence-weight-v1",
            self.variant.fingerprint,
            self.bucket.fingerprint,
            self.status.value,
            self.weight,
            self.provider_qualities,
            self.component_caps,
            self.reason_codes,
            self.source_fingerprints,
            self.policy_fingerprint,
        ))

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
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def directional_vote_authority(self) -> bool:
        return False

    @property
    def allocation_authority(self) -> bool:
        return False


def _provider_map(rows: Iterable[object], *, label: str) -> dict[str, object]:
    mapped: dict[str, object] = {}
    for row in rows:
        provider = str(getattr(row, "provider_id")).strip().lower()
        if provider in mapped:
            raise ValueError(f"duplicate {label} provider identity")
        mapped[provider] = row
    return mapped


def _provider_quality(
    specialization: ProviderSpecialization,
    calibration: ForecastCalibrationMemory,
    *,
    policy: AdaptiveEvidencePolicy,
) -> tuple[float, tuple[str, ...]]:
    reasons: list[str] = []
    provider = specialization.provider_id
    if specialization.status is SpecializationStatus.INSUFFICIENT:
        return 0.0, (f"specialization_insufficient:{provider}",)
    if calibration.status is not CalibrationMemoryStatus.MEASURED:
        return 0.0, (f"calibration_insufficient:{provider}",)
    if calibration.skill is None or calibration.direction_accuracy is None or calibration.interval_coverage_error is None:
        return 0.0, (f"calibration_incomplete:{provider}",)

    skill = _finite(calibration.skill, "calibration skill")
    if skill > 1.0:
        raise ValueError("calibration skill cannot exceed one")
    direction = _unit(calibration.direction_accuracy, "calibration direction_accuracy")
    coverage_error = _unit(calibration.interval_coverage_error, "calibration interval_coverage_error")
    coverage_quality = 1.0 - min(1.0, coverage_error / policy.coverage_error_zero_at)
    positive_skill = _clamp01(skill)
    product = positive_skill * direction * coverage_quality
    quality = 0.0 if product <= 0.0 else product ** (1.0 / 3.0)

    if specialization.status is SpecializationStatus.WEAK:
        quality = min(quality, policy.weak_specialization_cap)
        reasons.append(f"specialization_weak:{provider}")
    if skill <= 0.0:
        reasons.append(f"nonpositive_skill:{provider}")
    if coverage_quality == 0.0:
        reasons.append(f"interval_calibration_failed:{provider}")
    return quality, tuple(reasons)


def _disagreement_cap(
    bucket: ForecastContextBucket,
    cell: DisagreementAtlasCell | None,
    *,
    policy: AdaptiveEvidencePolicy,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    if cell is None:
        return policy.missing_disagreement_cap, ("disagreement_evidence_missing",), ()
    if cell.bucket != bucket:
        raise ValueError("disagreement bucket identity drift")
    if cell.status is DisagreementAtlasStatus.INSUFFICIENT:
        return policy.missing_disagreement_cap, ("disagreement_evidence_insufficient",), (cell.fingerprint,)
    if cell.consensus_accuracy is None:
        return policy.mixed_disagreement_cap, ("disagreement_has_no_consensus",), (cell.fingerprint,)
    accuracy = _unit(cell.consensus_accuracy, "disagreement consensus_accuracy")
    return accuracy, (() if accuracy == 1.0 else ("historical_disagreement_accuracy_cap",)), (cell.fingerprint,)


def _economic_cap(
    variant: ForecastAblationVariant,
    comparison: ForecastAblationComparison | None,
    information_value: ForecastInformationValue | None,
    *,
    policy: AdaptiveEvidencePolicy,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    if comparison is None:
        if information_value is not None:
            raise ValueError("information value requires its ablation comparison")
        return policy.missing_economic_cap, ("matched_ablation_missing",), ()
    if comparison.variant != variant:
        raise ValueError("ablation variant identity drift")

    if comparison.effect is AblationEffect.HARMFUL:
        cap = 0.0
        reasons = ["matched_ablation_harmful"]
    elif comparison.effect is AblationEffect.NEUTRAL:
        cap = policy.neutral_ablation_cap
        reasons = ["matched_ablation_neutral"]
    else:
        cap = 1.0
        reasons = []

    fingerprints = [comparison.fingerprint]
    if information_value is not None:
        if information_value.comparison_fingerprint != comparison.fingerprint:
            raise ValueError("information-value/ablation identity drift")
        if not math.isclose(information_value.net_return_delta, comparison.net_return_delta, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("information-value return delta drift")
        fingerprints.append(information_value.fingerprint)
        if information_value.status is InformationValueStatus.NEGATIVE:
            cap = min(cap, policy.negative_information_cap)
            reasons.append("information_value_negative_cap")
        elif information_value.status is InformationValueStatus.NEUTRAL:
            cap = min(cap, policy.neutral_information_cap)
            reasons.append("information_value_neutral_cap")
    return cap, tuple(reasons), tuple(fingerprints)


def _interaction_cap(
    variant: ForecastAblationVariant,
    bucket: ForecastContextBucket,
    interaction: StrategyForecastInteractionCell | None,
    *,
    policy: AdaptiveEvidencePolicy,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    if interaction is None:
        return policy.missing_interaction_cap, ("strategy_interaction_missing",), ()
    if interaction.variant != variant or interaction.bucket != bucket:
        raise ValueError("strategy interaction identity drift")
    if interaction.status is InteractionStatus.INSUFFICIENT:
        return policy.missing_interaction_cap, ("strategy_interaction_insufficient",), (interaction.fingerprint,)
    if interaction.status is InteractionStatus.HARMFUL:
        return 0.0, ("strategy_interaction_harmful",), (interaction.fingerprint,)
    if interaction.status is InteractionStatus.NEUTRAL:
        return policy.neutral_interaction_cap, ("strategy_interaction_neutral",), (interaction.fingerprint,)
    return 1.0, (), (interaction.fingerprint,)


def _failure_cap(
    variant: ForecastAblationVariant,
    bucket: ForecastContextBucket,
    patterns: Iterable[ForecastFailurePattern],
    *,
    policy: AdaptiveEvidencePolicy,
) -> tuple[float, tuple[str, ...], tuple[str, ...]]:
    rows = tuple(patterns)
    fingerprints = tuple(row.fingerprint for row in rows)
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("duplicate failure pattern evidence")
    provider_set = set(variant.providers)
    cap = 1.0
    reasons: list[str] = []
    recurrent_caps = {
        ForecastFailureKind.WRONG_DIRECTION: policy.recurrent_wrong_direction_cap,
        ForecastFailureKind.INTERVAL_MISS: policy.recurrent_interval_miss_cap,
        ForecastFailureKind.WORSE_THAN_NO_CHANGE: policy.recurrent_worse_than_no_change_cap,
        ForecastFailureKind.UNAVAILABLE: policy.recurrent_unavailable_cap,
        ForecastFailureKind.HARMFUL_ABLATION: policy.recurrent_harmful_ablation_cap,
    }
    for row in rows:
        if row.bucket != bucket:
            raise ValueError("failure pattern bucket identity drift")
        if not set(row.providers).issubset(provider_set):
            raise ValueError("failure pattern provider identity drift")
        if row.status is FailurePatternStatus.RECURRENT:
            cap = min(cap, recurrent_caps[row.kind])
            reasons.append(f"recurrent_failure:{row.kind.value}:{'+'.join(row.providers)}")
    return cap, tuple(sorted(reasons)), tuple(sorted(fingerprints))


def weigh_forecast_evidence(
    variant: ForecastAblationVariant,
    bucket: ForecastContextBucket,
    specializations: Iterable[ProviderSpecialization],
    calibrations: Iterable[ForecastCalibrationMemory],
    *,
    disagreement: DisagreementAtlasCell | None = None,
    ablation: ForecastAblationComparison | None = None,
    information_value: ForecastInformationValue | None = None,
    failure_patterns: Iterable[ForecastFailurePattern] = (),
    interaction: StrategyForecastInteractionCell | None = None,
    policy: AdaptiveEvidencePolicy = AdaptiveEvidencePolicy(),
) -> AdaptiveForecastEvidenceWeight:
    """Return a bounded research weight without granting decision authority."""

    if variant.is_control:
        raise ValueError("NO_FORECAST is a control, not weighted forecast evidence")

    target_providers = tuple(variant.providers)
    specialization_map = _provider_map(specializations, label="specialization")
    calibration_map = _provider_map(calibrations, label="calibration")
    if set(specialization_map) != set(target_providers):
        raise ValueError("specialization provider set must exactly match forecast variant")
    if set(calibration_map) != set(target_providers):
        raise ValueError("calibration provider set must exactly match forecast variant")

    provider_qualities: list[tuple[str, float]] = []
    reasons: list[str] = []
    source_fingerprints: list[str] = []
    for provider in target_providers:
        specialization = specialization_map[provider]
        calibration = calibration_map[provider]
        if specialization.bucket != bucket or calibration.bucket != bucket:
            raise ValueError("provider evidence bucket identity drift")
        quality, provider_reasons = _provider_quality(specialization, calibration, policy=policy)
        provider_qualities.append((provider, quality))
        reasons.extend(provider_reasons)
        source_fingerprints.extend((specialization.fingerprint, calibration.fingerprint))

    provider_cap = min(value for _, value in provider_qualities)
    disagreement_cap, disagreement_reasons, disagreement_fps = _disagreement_cap(bucket, disagreement, policy=policy)
    economic_cap, economic_reasons, economic_fps = _economic_cap(variant, ablation, information_value, policy=policy)
    interaction_cap, interaction_reasons, interaction_fps = _interaction_cap(variant, bucket, interaction, policy=policy)
    failure_cap, failure_reasons, failure_fps = _failure_cap(variant, bucket, failure_patterns, policy=policy)

    component_caps = (
        ("provider_quality", provider_cap),
        ("disagreement", disagreement_cap),
        ("economic", economic_cap),
        ("strategy_interaction", interaction_cap),
        ("failure_memory", failure_cap),
    )
    weight = min(value for _, value in component_caps)
    reasons.extend(disagreement_reasons)
    reasons.extend(economic_reasons)
    reasons.extend(interaction_reasons)
    reasons.extend(failure_reasons)
    source_fingerprints.extend(disagreement_fps)
    source_fingerprints.extend(economic_fps)
    source_fingerprints.extend(interaction_fps)
    source_fingerprints.extend(failure_fps)

    if provider_cap == 0.0 and any("insufficient" in reason or "incomplete" in reason for reason in reasons):
        status = EvidenceWeightStatus.INSUFFICIENT
    elif weight == 0.0:
        status = EvidenceWeightStatus.BLOCKED
    elif weight < provider_cap:
        status = EvidenceWeightStatus.CONSTRAINED
    else:
        status = EvidenceWeightStatus.WEIGHTED

    return AdaptiveForecastEvidenceWeight(
        variant,
        bucket,
        status,
        weight,
        tuple(provider_qualities),
        component_caps,
        tuple(sorted(set(reasons))),
        tuple(sorted(set(source_fingerprints))),
        policy.fingerprint,
    )
