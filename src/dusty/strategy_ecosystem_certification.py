from __future__ import annotations

"""M196.15 end-to-end Strategy Ecosystem certification and M197 handoff.

This module certifies the *research system*, not a profitable strategy.  A
strategy may remain A1-insufficient/rejected while the engineering handoff to
M197 is valid, provided the Estate -> execution -> financial accounting -> A1
campaign -> bounded refinement -> retest chain is identity-safe and authority
isolated.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json

from .controlled_evolution import EvolutionAction
from .estate_a1_refinement import EstateA1RefinementPlan
from .multitimeframe_a1_campaign import A1CampaignAssessment, A1CampaignStatus
from .trading_skills import ReconstructionRuleBasis, StrategyReconstruction


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


class StrategyEcosystemCertificationStatus(StrEnum):
    BLOCKED = "blocked"
    READY_FOR_M197 = "ready_for_m197"


@dataclass(frozen=True, slots=True)
class StrategyEcosystemCertification:
    status: StrategyEcosystemCertificationStatus
    parent_reconstruction_fingerprint: str
    parent_strategy_hash: str
    parent_campaign_fingerprint: str
    final_reconstruction_fingerprint: str
    final_strategy_hash: str
    final_campaign_fingerprint: str
    estate_sha256: str
    strategy_a1_qualified: bool
    engineering_handoff_to_m197: bool
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "parent_reconstruction_fingerprint",
            "parent_strategy_hash",
            "parent_campaign_fingerprint",
            "final_reconstruction_fingerprint",
            "final_strategy_hash",
            "final_campaign_fingerprint",
            "estate_sha256",
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        if self.engineering_handoff_to_m197 != (self.status is StrategyEcosystemCertificationStatus.READY_FOR_M197):
            raise ValueError("M196.15 handoff flag must match certification status")
        if self.status is StrategyEcosystemCertificationStatus.READY_FOR_M197 and self.blockers:
            raise ValueError("ready M196.15 certification cannot retain blockers")
        if self.status is StrategyEcosystemCertificationStatus.BLOCKED and not self.blockers:
            raise ValueError("blocked M196.15 certification requires blockers")

    @property
    def certification_hash(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m19615-strategy-ecosystem-certification-v1",
                "status": self.status.value,
                "parent_reconstruction": self.parent_reconstruction_fingerprint,
                "parent_strategy": self.parent_strategy_hash,
                "parent_campaign": self.parent_campaign_fingerprint,
                "final_reconstruction": self.final_reconstruction_fingerprint,
                "final_strategy": self.final_strategy_hash,
                "final_campaign": self.final_campaign_fingerprint,
                "estate_sha256": self.estate_sha256,
                "strategy_a1_qualified": self.strategy_a1_qualified,
                "engineering_handoff_to_m197": self.engineering_handoff_to_m197,
                "blockers": self.blockers,
                "authority": {
                    "broker_write": False,
                    "live_write": False,
                    "promotion": False,
                    "risk_override": False,
                    "guardian_override": False,
                },
            }
        )

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False


def _source_declared_rules(reconstruction: StrategyReconstruction) -> tuple[tuple[str, str], ...]:
    return tuple(
        (row.name, row.value)
        for row in reconstruction.rules
        if row.basis is ReconstructionRuleBasis.SOURCE_DECLARED
    )


def certify_strategy_ecosystem(
    parent: StrategyReconstruction,
    parent_campaign: A1CampaignAssessment,
    refinement: EstateA1RefinementPlan,
    *,
    estate_sha256_before: str,
    estate_sha256_after: str,
    child: StrategyReconstruction | None = None,
    child_campaign: A1CampaignAssessment | None = None,
    minimum_chronological_windows: int = 5,
) -> StrategyEcosystemCertification:
    """Certify the M196 research chain without granting strategy promotion.

    A promising parent may hand off directly. A non-promising parent must travel
    through one bounded M158 Challenger and a fresh chronological retest.  The
    final A1 status is reported separately from engineering readiness.
    """

    if isinstance(minimum_chronological_windows, bool) or minimum_chronological_windows < 2:
        raise ValueError("M196.15 minimum chronological windows must be at least 2")
    before = _sha(estate_sha256_before, "M196.15 Estate before")
    after = _sha(estate_sha256_after, "M196.15 Estate after")

    blockers: list[str] = []
    if before != after:
        blockers.append("strategy_estate_mutated")
    if parent_campaign.strategy_hash != parent.candidate_spec.strategy_hash:
        blockers.append("parent_campaign_strategy_identity_drift")
    if parent_campaign.window_count < minimum_chronological_windows:
        blockers.append("parent_chronological_window_count_failed")
    if refinement.reconstruction_fingerprint != parent.fingerprint:
        blockers.append("refinement_parent_reconstruction_drift")
    if refinement.campaign_fingerprint != parent_campaign.fingerprint:
        blockers.append("refinement_parent_campaign_drift")

    final_reconstruction = parent
    final_campaign = parent_campaign

    if parent_campaign.status is A1CampaignStatus.PROMISING:
        if refinement.evolution.action is not EvolutionAction.ADVANCE:
            blockers.append("promising_parent_did_not_advance")
        if child is not None or child_campaign is not None:
            blockers.append("promising_parent_should_not_require_challenger")
    else:
        if refinement.evolution.action is not EvolutionAction.CREATE_CHALLENGER:
            blockers.append("failed_parent_did_not_create_challenger")
        if len(refinement.evolution.challengers) != 1:
            blockers.append("bounded_challenger_count_failed")
        if child is None or child_campaign is None:
            blockers.append("failed_parent_missing_child_retest")
        else:
            final_reconstruction = child
            final_campaign = child_campaign
            if child.candidate_spec.strategy_hash == parent.candidate_spec.strategy_hash:
                blockers.append("challenger_execution_identity_not_distinct")
            if _source_declared_rules(child) != _source_declared_rules(parent):
                blockers.append("source_declared_rules_changed")
            if child_campaign.strategy_hash != child.candidate_spec.strategy_hash:
                blockers.append("child_campaign_strategy_identity_drift")
            if child_campaign.window_count < minimum_chronological_windows:
                blockers.append("child_chronological_window_count_failed")

    for campaign, label in ((parent_campaign, "parent"), (final_campaign, "final")):
        if any(
            (
                campaign.broker_write_authority,
                campaign.live_write_authority,
                campaign.promotion_authority,
                campaign.risk_override_authority,
                campaign.guardian_override_authority,
            )
        ):
            blockers.append(f"{label}_campaign_gained_operational_authority")
    if any(
        (
            refinement.broker_write_authority,
            refinement.live_write_authority,
            refinement.promotion_authority,
            refinement.risk_override_authority,
            refinement.guardian_override_authority,
        )
    ):
        blockers.append("refinement_gained_operational_authority")

    blockers_tuple = tuple(dict.fromkeys(blockers))
    ready = not blockers_tuple
    return StrategyEcosystemCertification(
        StrategyEcosystemCertificationStatus.READY_FOR_M197 if ready else StrategyEcosystemCertificationStatus.BLOCKED,
        parent.fingerprint,
        parent.candidate_spec.strategy_hash,
        parent_campaign.fingerprint,
        final_reconstruction.fingerprint,
        final_reconstruction.candidate_spec.strategy_hash,
        final_campaign.fingerprint,
        before,
        final_campaign.status is A1CampaignStatus.PROMISING,
        ready,
        blockers_tuple,
    )
