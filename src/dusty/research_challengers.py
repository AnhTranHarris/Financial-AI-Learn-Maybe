"""Bounded V2 research challengers with one semantic change per candidate.

This is a research-plan generator, not an optimizer. It never receives market results,
never ranks candidates, and never promotes anything. One-factor-at-a-time mutations
keep attribution interpretable and avoid an uncontrolled Cartesian search.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
import json
import math

from .research import RuleOp
from .reviewed_strategies import ReviewedResearchPackage
from .strategy_ir import RuleGroup


class MutationKind(StrEnum):
    ENTRY_THRESHOLD = "entry_threshold"
    EXIT_HORIZON_MINUTES = "exit_horizon_minutes"
    COOLDOWN_STEPS = "cooldown_steps"
    RSI_PERIOD = "rsi_period"
    FORECAST_NEUTRAL_RETURN = "forecast_neutral_return"


@dataclass(frozen=True, slots=True)
class ResearchMutation:
    kind: MutationKind
    value: int | float
    feature: str = ""
    op: RuleOp | None = None

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)) or not math.isfinite(self.value):
            raise ValueError("research_mutation_value_must_be_finite_numeric")
        if self.kind is MutationKind.ENTRY_THRESHOLD:
            if not self.feature.strip() or self.op is None:
                raise ValueError("entry_threshold_mutation_requires_feature_and_operator")
        elif self.feature or self.op is not None:
            raise ValueError("non_clause_mutation_cannot_name_feature_or_operator")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "value": self.value,
            "feature": self.feature,
            "op": None if self.op is None else self.op.value,
        }

    @property
    def fingerprint(self) -> str:
        return sha256(
            json.dumps(self.payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ChallengerPlan:
    mutations: tuple[ResearchMutation, ...]
    max_candidates: int = 16

    def __post_init__(self) -> None:
        if not self.mutations:
            raise ValueError("challenger_plan_requires_mutations")
        if isinstance(self.max_candidates, bool) or not isinstance(self.max_candidates, int) or self.max_candidates < 1:
            raise ValueError("challenger_plan_candidate_budget_must_be_positive_integer")
        identities = tuple(mutation.fingerprint for mutation in self.mutations)
        if len(set(identities)) != len(identities):
            raise ValueError("challenger_plan_rejects_duplicate_mutations")

    @property
    def fingerprint(self) -> str:
        # Mutation order is not research meaning; canonicalize it away.
        payload = {
            "protocol": "dusty-v2-one-factor-challengers-v1",
            "mutations": sorted((mutation.payload for mutation in self.mutations), key=_payload_key),
            "max_candidates": self.max_candidates,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ChallengerDraftV2:
    parent_fingerprint: str
    plan_fingerprint: str
    mutation: ResearchMutation
    package: ReviewedResearchPackage

    @property
    def candidate_fingerprint(self) -> str:
        payload = {
            "parent": self.parent_fingerprint,
            "plan": self.plan_fingerprint,
            "mutation": self.mutation.payload,
            "package": self.package.fingerprint,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @property
    def promotion_eligible(self) -> bool:
        return False


def _payload_key(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _positive_int(value: int | float, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}_must_be_integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ValueError(f"{label}_out_of_range")
    return value


def _mutate_entry_threshold(
    package: ReviewedResearchPackage,
    mutation: ResearchMutation,
) -> ReviewedResearchPackage:
    matches: list[tuple[int, int]] = []
    for group_index, group in enumerate(package.spec.entry_groups):
        for clause_index, clause in enumerate(group.clauses):
            if clause.feature == mutation.feature and clause.op is mutation.op:
                matches.append((group_index, clause_index))
    if len(matches) != 1:
        raise ValueError("entry_threshold_mutation_requires_exactly_one_matching_clause")
    group_index, clause_index = matches[0]
    groups = list(package.spec.entry_groups)
    group = groups[group_index]
    clauses = list(group.clauses)
    clauses[clause_index] = replace(clauses[clause_index], value=mutation.value)
    groups[group_index] = RuleGroup(tuple(clauses), group.mode)
    return replace(package, spec=replace(package.spec, entry_groups=tuple(groups)))


def apply_mutation(
    package: ReviewedResearchPackage,
    mutation: ResearchMutation,
) -> ReviewedResearchPackage:
    """Apply one prespecified semantic mutation without looking at research outcomes."""
    if mutation.kind is MutationKind.ENTRY_THRESHOLD:
        return _mutate_entry_threshold(package, mutation)
    if mutation.kind is MutationKind.EXIT_HORIZON_MINUTES:
        minutes = _positive_int(mutation.value, "exit_horizon_minutes")
        timeframe = package.spec.decision_timeframe_minutes
        if minutes % timeframe:
            raise ValueError("exit_horizon_must_align_to_decision_timeframe")
        plan = replace(
            package.spec.exit_plan,
            max_hold_steps=minutes // timeframe,
            max_elapsed_minutes=minutes,
        )
        return replace(
            package,
            spec=replace(package.spec, exit_plan=plan, intended_horizon_minutes=minutes),
        )
    if mutation.kind is MutationKind.COOLDOWN_STEPS:
        steps = _positive_int(mutation.value, "cooldown_steps", allow_zero=True)
        return replace(package, spec=replace(package.spec, cooldown_steps=steps))
    if mutation.kind is MutationKind.RSI_PERIOD:
        period = _positive_int(mutation.value, "rsi_period")
        return replace(package, features=replace(package.features, rsi_period=period))
    if mutation.kind is MutationKind.FORECAST_NEUTRAL_RETURN:
        value = float(mutation.value)
        if value < 0:
            raise ValueError("forecast_neutral_return_cannot_be_negative")
        return replace(
            package,
            cognition=replace(package.cognition, forecast_neutral_return=value),
        )
    raise AssertionError("unhandled_research_mutation")  # pragma: no cover


def generate_challengers(
    parent: ReviewedResearchPackage,
    plan: ChallengerPlan,
) -> tuple[ChallengerDraftV2, ...]:
    """Create deterministic one-factor challengers and fail rather than silently trim budget."""
    drafts: list[ChallengerDraftV2] = []
    seen_packages: set[str] = {parent.fingerprint}
    for mutation in sorted(plan.mutations, key=lambda item: item.fingerprint):
        candidate = apply_mutation(parent, mutation)
        if candidate.fingerprint in seen_packages:
            continue
        seen_packages.add(candidate.fingerprint)
        drafts.append(
            ChallengerDraftV2(
                parent_fingerprint=parent.fingerprint,
                plan_fingerprint=plan.fingerprint,
                mutation=mutation,
                package=candidate,
            )
        )
    if len(drafts) > plan.max_candidates:
        raise ValueError("challenger_plan_candidate_budget_exceeded")
    return tuple(drafts)
