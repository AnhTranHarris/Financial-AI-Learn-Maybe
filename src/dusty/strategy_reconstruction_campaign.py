from __future__ import annotations

"""M196.6 bounded strategy-reconstruction campaign planning and execution.

This layer begins after governed ``StrategyProposal`` intake. It removes exact
proposal-family duplicates before any model call, assigns one bounded timeframe
profile per family, and drains work in six-proposal windows. Ollama remains
strictly sequential inside each window; "six at a time" is a workload/checkpoint
boundary, never six concurrent inference requests.

Multi-timeframe profiles are research metadata. The current executable
``StrategySpecV2`` still owns one decision timeframe, so context timeframes do
not silently become executable conditions until a later point-in-time feature
binding proves them. D1 and M1 may therefore participate as human-style context
without bypassing the existing M5+ decision-timeframe constitution.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Iterable

from .source_intake import EvidenceClass, StrategyProposal, deduplicate_proposals
from .strategy_estate import load_strategy_estate
from .strategy_estate_builder import EstatePopulationResult, EstatePopulationRow, StrategyEstateBuilder


RECONSTRUCTION_BATCH_SIZE = 6
MAX_PROFILE_SYMBOLS = 3
CONTEXT_TIMEFRAME_LADDER = ("D1", "H4", "H1", "M30", "M15", "M5", "M1")
DECISION_TIMEFRAME_LADDER = ("H4", "H1", "M30", "M15", "M5")


class TimeframeMode(StrEnum):
    SINGLE = "single"
    MULTI = "multi"


class AssignmentBasis(StrEnum):
    SOURCE_DECLARED = "source_declared"
    RESEARCH_EXPLORATION = "research_exploration"


class PlanStatus(StrEnum):
    READY = "ready"
    ALREADY_REPRESENTED = "already_represented"
    DEFERRED = "deferred"


@dataclass(frozen=True, slots=True)
class TimeframeProfile:
    primary: str
    context: tuple[str, ...]
    mode: TimeframeMode
    basis: AssignmentBasis

    def __post_init__(self) -> None:
        primary = self.primary.strip().upper()
        context = tuple(value.strip().upper() for value in self.context if value.strip())
        if primary not in DECISION_TIMEFRAME_LADDER:
            raise ValueError("M196.6 primary timeframe must remain in the M5+ executable ladder")
        if len(context) > 2 or len(set(context)) != len(context) or primary in context:
            raise ValueError("M196.6 context timeframe set is invalid")
        if any(value not in CONTEXT_TIMEFRAME_LADDER for value in context):
            raise ValueError("M196.6 context timeframe is outside the canonical ladder")
        if self.mode is TimeframeMode.SINGLE and context:
            raise ValueError("single-timeframe profile cannot carry context")
        if self.mode is TimeframeMode.MULTI and not context:
            raise ValueError("multi-timeframe profile requires context")
        object.__setattr__(self, "primary", primary)
        object.__setattr__(self, "context", context)

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m1966-timeframe-profile-v1", self.primary, self.context, self.mode.value, self.basis.value))


@dataclass(frozen=True, slots=True)
class ReconstructionPlan:
    proposal: StrategyProposal
    status: PlanStatus
    profile: TimeframeProfile | None
    target_symbols: tuple[str, ...]
    deferred_symbols: tuple[str, ...] = ()
    reason: str = ""

    def __post_init__(self) -> None:
        symbols = tuple(value.strip().upper() for value in self.target_symbols if value.strip())
        deferred = tuple(value.strip().upper() for value in self.deferred_symbols if value.strip())
        if len(symbols) > MAX_PROFILE_SYMBOLS or len(set(symbols)) != len(symbols):
            raise ValueError("M196.6 target symbols must be unique and bounded")
        if len(set(deferred)) != len(deferred):
            raise ValueError("M196.6 deferred symbols must be unique")
        if self.status is PlanStatus.READY:
            if self.profile is None or not symbols or self.reason:
                raise ValueError("ready reconstruction plan requires profile and symbols only")
        elif self.profile is not None or symbols or not self.reason.strip():
            raise ValueError("non-ready reconstruction plan requires reason only")
        object.__setattr__(self, "target_symbols", symbols)
        object.__setattr__(self, "deferred_symbols", deferred)

    @property
    def fingerprint(self) -> str:
        payload = {
            "protocol": "dusty-m1966-reconstruction-plan-v1",
            "proposal": self.proposal.fingerprint,
            "status": self.status.value,
            "profile": None if self.profile is None else self.profile.fingerprint,
            "target_symbols": self.target_symbols,
            "deferred_symbols": self.deferred_symbols,
            "reason": self.reason,
        }
        return _digest(payload)


@dataclass(frozen=True, slots=True)
class ReconstructionCampaign:
    proposals_seen: int
    proposals_after_dedupe: int
    duplicates_removed: int
    plans: tuple[ReconstructionPlan, ...]
    batches: tuple[tuple[ReconstructionPlan, ...], ...]

    def __post_init__(self) -> None:
        if self.proposals_seen < 0 or self.proposals_after_dedupe < 0 or self.proposals_after_dedupe > self.proposals_seen:
            raise ValueError("invalid M196.6 campaign counts")
        if self.duplicates_removed != self.proposals_seen - self.proposals_after_dedupe:
            raise ValueError("M196.6 duplicate count mismatch")
        if len(self.plans) != self.proposals_after_dedupe:
            raise ValueError("M196.6 plan count must equal deduplicated proposal count")
        if any(not 1 <= len(batch) <= RECONSTRUCTION_BATCH_SIZE for batch in self.batches):
            raise ValueError("M196.6 batch exceeds reconstruction governor")
        flattened = tuple(plan for batch in self.batches for plan in batch)
        ready = tuple(plan for plan in self.plans if plan.status is PlanStatus.READY)
        if flattened != ready:
            raise ValueError("M196.6 batches must contain every ready plan exactly once")


@dataclass(frozen=True, slots=True)
class CampaignExecutionResult:
    campaign: ReconstructionCampaign
    rows: tuple[EstatePopulationRow, ...]
    batches_completed: int
    model_calls_scheduled: int
    added_to_estate: int

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _declared_profile(proposal: StrategyProposal) -> TimeframeProfile | None:
    declared = tuple(dict.fromkeys(value.strip().upper() for value in proposal.timeframes if value.strip()))
    if not declared:
        return None
    primary = next((value for value in declared if value in DECISION_TIMEFRAME_LADDER), None)
    if primary is None:
        return None
    context = tuple(value for value in CONTEXT_TIMEFRAME_LADDER if value in declared and value != primary)[:2]
    if context:
        return TimeframeProfile(primary, context, TimeframeMode.MULTI, AssignmentBasis.SOURCE_DECLARED)
    return TimeframeProfile(primary, (), TimeframeMode.SINGLE, AssignmentBasis.SOURCE_DECLARED)


def _exploration_profile(proposal: StrategyProposal) -> TimeframeProfile:
    raw = bytes.fromhex(proposal.family_fingerprint)
    primary = DECISION_TIMEFRAME_LADDER[raw[0] % len(DECISION_TIMEFRAME_LADDER)]
    if raw[1] % 2 == 0:
        return TimeframeProfile(primary, (), TimeframeMode.SINGLE, AssignmentBasis.RESEARCH_EXPLORATION)

    index = CONTEXT_TIMEFRAME_LADDER.index(primary)
    neighbors: list[str] = []
    if index > 0:
        neighbors.append(CONTEXT_TIMEFRAME_LADDER[index - 1])
    if index + 1 < len(CONTEXT_TIMEFRAME_LADDER):
        neighbors.append(CONTEXT_TIMEFRAME_LADDER[index + 1])
    if len(neighbors) < 2 and index + 2 < len(CONTEXT_TIMEFRAME_LADDER):
        neighbors.append(CONTEXT_TIMEFRAME_LADDER[index + 2])
    return TimeframeProfile(primary, tuple(neighbors[:2]), TimeframeMode.MULTI, AssignmentBasis.RESEARCH_EXPLORATION)


def _symbols_for(proposal: StrategyProposal, allowed_symbols: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    universe = tuple(dict.fromkeys(value.strip().upper() for value in allowed_symbols if value.strip()))
    if not universe:
        raise ValueError("M196.6 campaign requires an allowed-symbol universe")
    if proposal.symbols:
        allowed = set(universe)
        candidates = tuple(dict.fromkeys(value.upper() for value in proposal.symbols if value.upper() in allowed))
    else:
        candidates = (universe[int(proposal.family_fingerprint[:8], 16) % len(universe)],)
    return candidates[:MAX_PROFILE_SYMBOLS], candidates[MAX_PROFILE_SYMBOLS:]


def plan_reconstruction_campaign(
    proposals: Iterable[StrategyProposal],
    *,
    allowed_symbols: tuple[str, ...],
    estate_path: str | Path | None = None,
) -> ReconstructionCampaign:
    incoming = tuple(proposals)
    deduped = deduplicate_proposals(incoming)
    existing = {row.proposal_fingerprint for row in load_strategy_estate(estate_path)} if estate_path is not None else set()
    plans: list[ReconstructionPlan] = []
    for proposal in deduped:
        if proposal.fingerprint in existing:
            plans.append(ReconstructionPlan(proposal, PlanStatus.ALREADY_REPRESENTED, None, (), reason="proposal_already_in_estate"))
            continue
        if proposal.evidence_class is not EvidenceClass.STRATEGY_HYPOTHESIS:
            plans.append(ReconstructionPlan(proposal, PlanStatus.DEFERRED, None, (), reason="proposal_is_not_strategy_hypothesis"))
            continue
        symbols, deferred = _symbols_for(proposal, allowed_symbols)
        if not symbols:
            plans.append(ReconstructionPlan(proposal, PlanStatus.DEFERRED, None, (), reason="proposal_has_no_allowed_symbol"))
            continue
        if proposal.timeframes:
            profile = _declared_profile(proposal)
            if profile is None:
                plans.append(ReconstructionPlan(
                    proposal,
                    PlanStatus.DEFERRED,
                    None,
                    (),
                    reason="source_has_no_current_m5_plus_canonical_decision_timeframe",
                ))
                continue
        else:
            profile = _exploration_profile(proposal)
        plans.append(ReconstructionPlan(proposal, PlanStatus.READY, profile, symbols, deferred_symbols=deferred))

    ready = tuple(plan for plan in plans if plan.status is PlanStatus.READY)
    batches = tuple(ready[index:index + RECONSTRUCTION_BATCH_SIZE] for index in range(0, len(ready), RECONSTRUCTION_BATCH_SIZE))
    return ReconstructionCampaign(
        proposals_seen=len(incoming),
        proposals_after_dedupe=len(deduped),
        duplicates_removed=len(incoming) - len(deduped),
        plans=tuple(plans),
        batches=batches,
    )


def execute_reconstruction_campaign(
    campaign: ReconstructionCampaign,
    *,
    builder: StrategyEstateBuilder,
    model_tag: str,
    model_digest: str,
    allowed_features: tuple[str, ...],
    allowed_sessions: tuple[str, ...],
    estate_path: str | Path,
) -> CampaignExecutionResult:
    """Drain six-item windows sequentially and persist after every candidate."""

    rows: list[EstatePopulationRow] = []
    added = 0
    scheduled = 0
    completed = 0
    for batch in campaign.batches:
        for plan in batch:
            assert plan.profile is not None
            scheduled += 1
            result: EstatePopulationResult = builder.populate(
                (plan.proposal,),
                model_tag=model_tag,
                model_digest=model_digest,
                allowed_symbols=plan.target_symbols,
                allowed_timeframes=(plan.profile.primary,),
                allowed_features=allowed_features,
                allowed_sessions=allowed_sessions,
                estate_path=estate_path,
            )
            rows.extend(result.rows)
            added += result.added_count
        completed += 1
    return CampaignExecutionResult(campaign, tuple(rows), completed, scheduled, added)
