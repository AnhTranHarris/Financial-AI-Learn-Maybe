from __future__ import annotations

"""M192 bounded A3 profit-velocity expert.

A3 studies whether already-proven A1/A2 research can realize robust expectancy
more efficiently in time/capital. It may propose bounded efficiency tests only.
It cannot increase risk/leverage/size/frequency, force trades, loosen stops,
bypass A1/A2, mutate strategies, set M160 priority, certify A3, trade, promote,
or override Guardian. Deterministic research governance remains authoritative.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Iterable

from .a2_expert_agent import A2ExpertEvidence, A2ExpertState
from .autonomous_research_campaign import CampaignCheckpoint, CampaignStatus
from .research_expert_runtime import StructuredResearchGenerator, compact_error, validate_response_identity


PROTOCOL = "dusty-a3-profit-velocity-expert-v1"
PROMPT_VERSION = "m192-a3-efficiency-hypothesis-v1"
_MAX_OPPORTUNITIES = 4
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _line(value: str, label: str, *, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    rendered = " ".join(value.strip().split())
    if not rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty and <= {maximum} characters")
    return rendered


def _hashes(values: Iterable[str], label: str) -> tuple[str, ...]:
    rows = tuple(sorted(_sha(value, label) for value in values))
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} fingerprints must be unique")
    return rows


def _string_array(value: object, label: str, *, maximum_items: int, maximum_length: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items or any(not isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a string array with at most {maximum_items} items")
    rows = tuple(_line(item, label, maximum=maximum_length) for item in value)
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} values must be unique")
    return rows


class A3ExpertState(StrEnum):
    VELOCITY_TESTS = "velocity_tests"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class A3TestFamily(StrEnum):
    ENTRY_TIMING_EFFICIENCY = "entry_timing_efficiency"
    HOLDING_DURATION_EFFICIENCY = "holding_duration_efficiency"
    CAPITAL_OCCUPANCY = "capital_occupancy"
    ABSTENTION_EFFICIENCY = "abstention_efficiency"
    EXECUTION_LATENCY_SENSITIVITY = "execution_latency_sensitivity"
    SIGNAL_DECAY = "signal_decay"
    RESOURCE_EFFICIENCY = "resource_efficiency"
    OPPORTUNITY_SELECTION = "opportunity_selection"


class A3ProtectedInvariant(StrEnum):
    A1_EVIDENCE = "a1_evidence"
    A2_ROBUSTNESS = "a2_robustness"
    RISK_CONSTITUTION = "risk_constitution"
    GUARDIAN = "guardian"
    ABSTENTION_NO_FORCED_TRADE = "abstention_no_forced_trade"
    FROZEN_CHAMPION = "frozen_champion"


_REQUIRED_INVARIANTS = frozenset(A3ProtectedInvariant)


@dataclass(frozen=True, slots=True)
class A3ExpertRequest:
    request_id: str
    model_tag: str
    model_digest: str
    campaign_checkpoint: CampaignCheckpoint
    context_fingerprint: str
    a2_evidence: tuple[A2ExpertEvidence, ...]
    strategy_fingerprints: tuple[str, ...]
    capital_allocation_fingerprints: tuple[str, ...] = ()
    timing_fingerprints: tuple[str, ...] = ()
    holding_period_fingerprints: tuple[str, ...] = ()
    execution_fingerprints: tuple[str, ...] = ()
    abstention_fingerprints: tuple[str, ...] = ()
    forward_performance_fingerprints: tuple[str, ...] = ()
    resource_efficiency_fingerprints: tuple[str, ...] = ()
    evidence_summary: str = ""
    question: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _line(self.request_id, "A3 request_id", maximum=128))
        object.__setattr__(self, "model_tag", _line(self.model_tag, "A3 model_tag", maximum=256))
        object.__setattr__(self, "model_digest", _sha(self.model_digest, "A3 model digest"))
        if self.campaign_checkpoint.status is not CampaignStatus.ACTIVE:
            raise ValueError("A3 expert requires an active M189 campaign checkpoint")
        if self.campaign_checkpoint.school_index != 2:
            raise ValueError("A3 expert cannot run outside A3 campaign school")
        object.__setattr__(self, "context_fingerprint", _sha(self.context_fingerprint, "A3 context"))

        upstream = tuple(self.a2_evidence)
        if not upstream:
            raise ValueError("A3 expert requires upstream M191 evidence")
        if any(row.state is not A2ExpertState.ROBUSTNESS_TESTS or not row.concerns for row in upstream):
            raise ValueError("A3 expert requires concern-bearing M191 robustness evidence")
        if len({row.fingerprint for row in upstream}) != len(upstream):
            raise ValueError("A3 upstream M191 evidence must be unique")
        object.__setattr__(self, "a2_evidence", upstream)

        names = (
            "strategy_fingerprints",
            "capital_allocation_fingerprints",
            "timing_fingerprints",
            "holding_period_fingerprints",
            "execution_fingerprints",
            "abstention_fingerprints",
            "forward_performance_fingerprints",
            "resource_efficiency_fingerprints",
        )
        identities = [row.fingerprint for row in upstream]
        for name in names:
            rows = _hashes(getattr(self, name), f"A3 {name}")
            object.__setattr__(self, name, rows)
            identities.extend(rows)
        if not self.strategy_fingerprints:
            raise ValueError("A3 expert requires frozen strategy identity")
        if sum(len(getattr(self, name)) for name in names[1:]) < 1:
            raise ValueError("A3 expert requires supplied efficiency evidence")
        if len(identities) != len(set(identities)):
            raise ValueError("A3 evidence fingerprint cannot masquerade as multiple evidence classes")
        object.__setattr__(self, "evidence_summary", _line(self.evidence_summary, "A3 evidence summary", maximum=24_000))
        object.__setattr__(self, "question", _line(self.question, "A3 question", maximum=1_024))

    @property
    def a2_fingerprints(self) -> tuple[str, ...]:
        return tuple(sorted(row.fingerprint for row in self.a2_evidence))

    @property
    def a2_concern_fingerprints(self) -> tuple[str, ...]:
        return tuple(sorted({concern.fingerprint for row in self.a2_evidence for concern in row.concerns}))

    @property
    def allowed_citations(self) -> frozenset[str]:
        values = set(self.a2_fingerprints)
        values.update(self.a2_concern_fingerprints)
        for name in (
            "strategy_fingerprints",
            "capital_allocation_fingerprints",
            "timing_fingerprints",
            "holding_period_fingerprints",
            "execution_fingerprints",
            "abstention_fingerprints",
            "forward_performance_fingerprints",
            "resource_efficiency_fingerprints",
        ):
            values.update(getattr(self, name))
        return frozenset(values)

    @property
    def fingerprint(self) -> str:
        return _digest({
            "protocol": PROTOCOL,
            "prompt_version": PROMPT_VERSION,
            "request_id": self.request_id,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "campaign_checkpoint": self.campaign_checkpoint.fingerprint,
            "context": self.context_fingerprint,
            "a2_evidence": self.a2_fingerprints,
            "a2_concerns": self.a2_concern_fingerprints,
            "strategy": self.strategy_fingerprints,
            "capital_allocation": self.capital_allocation_fingerprints,
            "timing": self.timing_fingerprints,
            "holding_period": self.holding_period_fingerprints,
            "execution": self.execution_fingerprints,
            "abstention": self.abstention_fingerprints,
            "forward_performance": self.forward_performance_fingerprints,
            "resource_efficiency": self.resource_efficiency_fingerprints,
            "summary": self.evidence_summary,
            "question": self.question,
        })


@dataclass(frozen=True, slots=True)
class A3VelocityOpportunity:
    opportunity_key: str
    efficiency_hypothesis: str
    failure_condition: str
    test_family: A3TestFamily
    test_plan: str
    protected_invariants: tuple[A3ProtectedInvariant, ...]
    cited_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        key = _line(self.opportunity_key, "A3 opportunity_key", maximum=64)
        if _KEY.fullmatch(key) is None:
            raise ValueError("A3 opportunity_key contains unsupported characters")
        object.__setattr__(self, "opportunity_key", key)
        object.__setattr__(self, "efficiency_hypothesis", _line(self.efficiency_hypothesis, "A3 efficiency_hypothesis", maximum=512))
        object.__setattr__(self, "failure_condition", _line(self.failure_condition, "A3 failure_condition", maximum=512))
        object.__setattr__(self, "test_plan", _line(self.test_plan, "A3 test_plan", maximum=512))
        invariants = tuple(sorted((A3ProtectedInvariant(value) for value in self.protected_invariants), key=lambda value: value.value))
        if len(invariants) != len(set(invariants)) or frozenset(invariants) != _REQUIRED_INVARIANTS:
            raise ValueError("A3 opportunity must preserve every required non-relaxation invariant")
        object.__setattr__(self, "protected_invariants", invariants)
        citations = _hashes(self.cited_fingerprints, "A3 opportunity citation")
        if not citations:
            raise ValueError("A3 opportunity requires supplied evidence citations")
        object.__setattr__(self, "cited_fingerprints", citations)

    @property
    def research_shape(self) -> tuple[object, ...]:
        return (
            self.efficiency_hypothesis,
            self.failure_condition,
            self.test_family.value,
            self.test_plan,
            tuple(value.value for value in self.protected_invariants),
            self.cited_fingerprints,
        )

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-a3-velocity-opportunity-v1", self.opportunity_key, *self.research_shape))

    @property
    def a3_certified(self) -> bool:
        return False

    @property
    def risk_relaxation_authority(self) -> bool:
        return False

    @property
    def frequency_relaxation_authority(self) -> bool:
        return False

    @property
    def mutation_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class A3ExpertEvidence:
    request_fingerprint: str
    model_tag: str
    model_digest: str
    state: A3ExpertState
    rationale_codes: tuple[str, ...]
    opportunities: tuple[A3VelocityOpportunity, ...]
    raw_response_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_fingerprint", _sha(self.request_fingerprint, "A3 evidence request"))
        object.__setattr__(self, "model_tag", _line(self.model_tag, "A3 evidence model_tag", maximum=256))
        object.__setattr__(self, "model_digest", _sha(self.model_digest, "A3 evidence model digest"))
        rationale = tuple(_line(value, "A3 rationale code", maximum=96) for value in self.rationale_codes)
        if not 1 <= len(rationale) <= 12 or len(rationale) != len(set(rationale)):
            raise ValueError("A3 evidence requires 1-12 unique rationale codes")
        object.__setattr__(self, "rationale_codes", rationale)
        rows = tuple(self.opportunities)
        if len(rows) > _MAX_OPPORTUNITIES:
            raise ValueError("A3 expert evidence exceeds bounded opportunity count")
        if len({row.opportunity_key for row in rows}) != len(rows):
            raise ValueError("A3 opportunity keys must be unique")
        if len({row.research_shape for row in rows}) != len(rows):
            raise ValueError("A3 opportunities must be structurally distinct")
        if self.state is A3ExpertState.VELOCITY_TESTS and not rows:
            raise ValueError("A3 velocity_tests state requires at least one opportunity")
        if self.state is A3ExpertState.INSUFFICIENT_EVIDENCE and rows:
            raise ValueError("insufficient A3 evidence cannot carry opportunities")
        object.__setattr__(self, "opportunities", rows)
        object.__setattr__(self, "raw_response_sha256", _sha(self.raw_response_sha256, "A3 raw response"))

    @property
    def fingerprint(self) -> str:
        return _digest({
            "protocol": PROTOCOL,
            "prompt_version": PROMPT_VERSION,
            "request": self.request_fingerprint,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "state": self.state.value,
            "rationale_codes": self.rationale_codes,
            "opportunities": tuple(row.fingerprint for row in self.opportunities),
            "raw_response_sha256": self.raw_response_sha256,
        })

    @property
    def a3_certified(self) -> bool:
        return False

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def mutation_authority(self) -> bool:
        return False

    @property
    def scheduler_priority_authority(self) -> bool:
        return False


A3_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": [value.value for value in A3ExpertState]},
        "rationale_codes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 96},
            "minItems": 1,
            "maxItems": 12,
            "uniqueItems": True,
        },
        "opportunities": {
            "type": "array",
            "maxItems": _MAX_OPPORTUNITIES,
            "items": {
                "type": "object",
                "properties": {
                    "opportunity_key": {"type": "string", "minLength": 1, "maxLength": 64},
                    "efficiency_hypothesis": {"type": "string", "minLength": 1, "maxLength": 512},
                    "failure_condition": {"type": "string", "minLength": 1, "maxLength": 512},
                    "test_family": {"type": "string", "enum": [value.value for value in A3TestFamily]},
                    "test_plan": {"type": "string", "minLength": 1, "maxLength": 512},
                    "protected_invariants": {
                        "type": "array",
                        "items": {"type": "string", "enum": [value.value for value in A3ProtectedInvariant]},
                        "minItems": len(_REQUIRED_INVARIANTS),
                        "maxItems": len(_REQUIRED_INVARIANTS),
                        "uniqueItems": True,
                    },
                    "cited_fingerprints": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 64, "maxLength": 64},
                        "minItems": 1,
                        "maxItems": 20,
                        "uniqueItems": True,
                    },
                },
                "required": [
                    "opportunity_key",
                    "efficiency_hypothesis",
                    "failure_condition",
                    "test_family",
                    "test_plan",
                    "protected_invariants",
                    "cited_fingerprints",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["state", "rationale_codes", "opportunities"],
    "additionalProperties": False,
}


def build_a3_prompt_payload(request: A3ExpertRequest) -> dict[str, object]:
    return {
        "protocol": PROTOCOL,
        "prompt_version": PROMPT_VERSION,
        "role": "a3_profit_velocity_efficiency_expert",
        "school": "A3",
        "request_fingerprint": request.fingerprint,
        "campaign_checkpoint_fingerprint": request.campaign_checkpoint.fingerprint,
        "context_fingerprint": request.context_fingerprint,
        "constraints": [
            "research_only",
            "improve_time_or_capital_efficiency_without_relaxing_A1_or_A2",
            "do_not_certify_A3_pass_or_profitability",
            "do_not_increase_risk_fraction_leverage_or_position_size",
            "do_not_increase_trade_frequency_to_manufacture_velocity",
            "do_not_force_trades_or_weaken_abstention",
            "do_not_widen_or_loosen_risk_invalidation",
            "no_broker_or_order_actions",
            "no_guardian_override",
            "no_champion_promotion",
            "no_scheduler_score_priority_or_resource_decision",
            "no_executable_strategy_mutation_fields_or_parameter_updates",
            "cite_only_supplied_fingerprints",
            "use_only_A3_test_families",
            "every_opportunity_must_preserve_all_protected_invariants",
            "return_at_most_four_structurally_distinct_opportunities",
            "when_evidence_is_insufficient_return_insufficient_evidence",
            "deterministic_A3_evaluation_and_M160_remain_decision_authorities",
        ],
        "protected_invariants": [value.value for value in sorted(_REQUIRED_INVARIANTS, key=lambda value: value.value)],
        "upstream_A2": {
            "evidence_fingerprints": request.a2_fingerprints,
            "concern_fingerprints": request.a2_concern_fingerprints,
        },
        "supplied_evidence": {
            "strategy": request.strategy_fingerprints,
            "capital_allocation": request.capital_allocation_fingerprints,
            "timing": request.timing_fingerprints,
            "holding_period": request.holding_period_fingerprints,
            "execution": request.execution_fingerprints,
            "abstention": request.abstention_fingerprints,
            "forward_performance": request.forward_performance_fingerprints,
            "resource_efficiency": request.resource_efficiency_fingerprints,
            "summary": request.evidence_summary,
        },
        "question": request.question,
        "allowed_test_families": [value.value for value in A3TestFamily],
    }


def parse_a3_expert_response(request: A3ExpertRequest, response_text: str) -> A3ExpertEvidence:
    raw = json.loads(response_text)
    if not isinstance(raw, dict) or set(raw) != {"state", "rationale_codes", "opportunities"}:
        raise ValueError("A3 expert response schema mismatch")
    if not isinstance(raw["state"], str):
        raise ValueError("A3 expert state must be a string")
    try:
        state = A3ExpertState(raw["state"])
    except ValueError as exc:
        raise ValueError("A3 expert state invalid") from exc
    rationale = _string_array(raw["rationale_codes"], "A3 rationale_codes", maximum_items=12, maximum_length=96)
    if not rationale:
        raise ValueError("A3 expert rationale cannot be empty")
    rows = raw["opportunities"]
    if not isinstance(rows, list) or len(rows) > _MAX_OPPORTUNITIES:
        raise ValueError("A3 opportunities must be a bounded array")
    expected = {
        "opportunity_key",
        "efficiency_hypothesis",
        "failure_condition",
        "test_family",
        "test_plan",
        "protected_invariants",
        "cited_fingerprints",
    }
    text_fields = ("opportunity_key", "efficiency_hypothesis", "failure_condition", "test_family", "test_plan")
    opportunities: list[A3VelocityOpportunity] = []
    for item in rows:
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("A3 opportunity schema mismatch")
        if any(not isinstance(item[name], str) for name in text_fields):
            raise ValueError("A3 opportunity text fields must be strings")
        invariant_names = _string_array(
            item["protected_invariants"],
            "A3 protected_invariants",
            maximum_items=len(_REQUIRED_INVARIANTS),
            maximum_length=64,
        )
        try:
            invariants = tuple(A3ProtectedInvariant(value) for value in invariant_names)
        except ValueError as exc:
            raise ValueError("A3 protected invariant is invalid") from exc
        if frozenset(invariants) != _REQUIRED_INVARIANTS:
            raise ValueError("A3 opportunity omitted a required protected invariant")
        citations = _string_array(item["cited_fingerprints"], "A3 cited_fingerprints", maximum_items=20, maximum_length=64)
        normalized = tuple(_sha(value, "A3 cited fingerprint") for value in citations)
        if any(value not in request.allowed_citations for value in normalized):
            raise ValueError("A3 opportunity cited evidence not supplied")
        try:
            family = A3TestFamily(item["test_family"])
        except ValueError as exc:
            raise ValueError("A3 opportunity test family is outside A3 scope") from exc
        opportunities.append(A3VelocityOpportunity(
            opportunity_key=item["opportunity_key"],
            efficiency_hypothesis=item["efficiency_hypothesis"],
            failure_condition=item["failure_condition"],
            test_family=family,
            test_plan=item["test_plan"],
            protected_invariants=invariants,
            cited_fingerprints=normalized,
        ))
    return A3ExpertEvidence(
        request_fingerprint=request.fingerprint,
        model_tag=request.model_tag,
        model_digest=request.model_digest,
        state=state,
        rationale_codes=rationale,
        opportunities=tuple(opportunities),
        raw_response_sha256=sha256(response_text.encode("utf-8")).hexdigest(),
    )


class A3ExpertAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class A3ExpertRunResult:
    status: A3ExpertAvailability
    evidence: A3ExpertEvidence | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.status is A3ExpertAvailability.AVAILABLE:
            if self.evidence is None or self.error:
                raise ValueError("available A3 expert result requires evidence only")
        elif self.evidence is not None or not self.error.strip():
            raise ValueError("unavailable A3 expert result requires error only")

    @property
    def available(self) -> bool:
        return self.status is A3ExpertAvailability.AVAILABLE

    @property
    def broker_write_authority(self) -> bool:
        return False


class A3ExpertAgent:
    def __init__(self, generator: StructuredResearchGenerator) -> None:
        self._generator = generator

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def a3_certification_authorized(self) -> bool:
        return False

    @property
    def mutation_authorized(self) -> bool:
        return False

    @property
    def scheduler_priority_authorized(self) -> bool:
        return False

    def research(self, request: A3ExpertRequest) -> A3ExpertRunResult:
        try:
            response = self._generator.generate(
                model_tag=request.model_tag,
                model_digest=request.model_digest,
                system_message=(
                    "You are Dusty Dragon's A3 profit-velocity efficiency expert. Study only how to test "
                    "time/capital efficiency while preserving A1, A2, risk, Guardian, abstention, and the frozen Champion. "
                    "You cannot trade, increase risk/leverage/size/frequency, force trades, certify A3, promote, schedule, or mutate."
                ),
                prompt_payload=build_a3_prompt_payload(request),
                response_schema=A3_RESPONSE_SCHEMA,
            )
            validate_response_identity(response, model_tag=request.model_tag, model_digest=request.model_digest)
            evidence = parse_a3_expert_response(request, response.content)
            return A3ExpertRunResult(A3ExpertAvailability.AVAILABLE, evidence=evidence)
        except (ValueError, TypeError, RuntimeError, OSError, json.JSONDecodeError) as exc:
            return A3ExpertRunResult(
                A3ExpertAvailability.UNAVAILABLE,
                error=f"a3_expert_unavailable:{compact_error(exc)}",
            )
