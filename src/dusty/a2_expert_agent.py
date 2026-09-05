from __future__ import annotations

"""M191 bounded A2 quant-profitability/robustness expert.

The A2 expert interrogates already-discovered A1 research against supplied
robustness evidence. It may propose discriminating robustness tests, but it
cannot certify profitability, invent policy thresholds, mutate strategies,
score M160 priorities, trade, resize risk, promote a Champion, or override
Guardian. M174/M160 remain the deterministic decision authorities.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Iterable

from .a1_expert_agent import A1ExpertEvidence, A1ExpertState
from .autonomous_research_campaign import CampaignCheckpoint, CampaignStatus
from .research_expert_runtime import (
    StructuredResearchGenerator,
    compact_error,
    validate_response_identity,
)


PROTOCOL = "dusty-a2-robustness-expert-v1"
PROMPT_VERSION = "m191-a2-robustness-critic-v1"
_MAX_CONCERNS = 4
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
    if (
        not isinstance(value, list)
        or len(value) > maximum_items
        or any(not isinstance(item, str) for item in value)
    ):
        raise ValueError(f"{label} must be a string array with at most {maximum_items} items")
    rows = tuple(_line(item, label, maximum=maximum_length) for item in value)
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} values must be unique")
    return rows


class A2ExpertState(StrEnum):
    ROBUSTNESS_TESTS = "robustness_tests"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class A2TestFamily(StrEnum):
    WALK_FORWARD = "walk_forward"
    PURGED_TEMPORAL_VALIDATION = "purged_temporal_validation"
    PARAMETER_STABILITY = "parameter_stability"
    REGIME_TORTURE = "regime_torture"
    COST_SLIPPAGE_TORTURE = "cost_slippage_torture"
    FORWARD_DECAY = "forward_decay"
    TAIL_RISK = "tail_risk"
    DEPENDENCY_STRESS = "dependency_stress"
    ROBUSTNESS_EVIDENCE_GAP = "robustness_evidence_gap"


@dataclass(frozen=True, slots=True)
class A2ExpertRequest:
    request_id: str
    model_tag: str
    model_digest: str
    campaign_checkpoint: CampaignCheckpoint
    context_fingerprint: str
    a1_evidence: tuple[A1ExpertEvidence, ...]
    strategy_fingerprints: tuple[str, ...]
    walk_forward_fingerprints: tuple[str, ...] = ()
    purged_validation_fingerprints: tuple[str, ...] = ()
    parameter_stability_fingerprints: tuple[str, ...] = ()
    regime_torture_fingerprints: tuple[str, ...] = ()
    cost_torture_fingerprints: tuple[str, ...] = ()
    forward_decay_fingerprints: tuple[str, ...] = ()
    tail_risk_fingerprints: tuple[str, ...] = ()
    dependency_fingerprints: tuple[str, ...] = ()
    robustness_gate_fingerprints: tuple[str, ...] = ()
    evidence_summary: str = ""
    question: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _line(self.request_id, "A2 request_id", maximum=128))
        object.__setattr__(self, "model_tag", _line(self.model_tag, "A2 model_tag", maximum=256))
        object.__setattr__(self, "model_digest", _sha(self.model_digest, "A2 model digest"))
        if self.campaign_checkpoint.status is not CampaignStatus.ACTIVE:
            raise ValueError("A2 expert requires an active M189 campaign checkpoint")
        if self.campaign_checkpoint.school_index != 1:
            raise ValueError("A2 expert cannot run outside A2 campaign school")
        object.__setattr__(self, "context_fingerprint", _sha(self.context_fingerprint, "A2 context"))
        a1_rows = tuple(self.a1_evidence)
        if not a1_rows:
            raise ValueError("A2 expert requires upstream M190 evidence")
        if any(row.state is not A1ExpertState.HYPOTHESES or not row.hypotheses for row in a1_rows):
            raise ValueError("A2 expert requires hypothesis-bearing M190 evidence")
        if len({row.fingerprint for row in a1_rows}) != len(a1_rows):
            raise ValueError("A2 upstream M190 evidence must be unique")
        object.__setattr__(self, "a1_evidence", a1_rows)
        names = (
            "strategy_fingerprints",
            "walk_forward_fingerprints",
            "purged_validation_fingerprints",
            "parameter_stability_fingerprints",
            "regime_torture_fingerprints",
            "cost_torture_fingerprints",
            "forward_decay_fingerprints",
            "tail_risk_fingerprints",
            "dependency_fingerprints",
            "robustness_gate_fingerprints",
        )
        all_hashes = [row.fingerprint for row in a1_rows]
        for name in names:
            rows = _hashes(getattr(self, name), f"A2 {name}")
            object.__setattr__(self, name, rows)
            all_hashes.extend(rows)
        if not self.strategy_fingerprints:
            raise ValueError("A2 expert requires frozen strategy identity")
        robustness_count = sum(len(getattr(self, name)) for name in names[1:])
        if robustness_count < 1:
            raise ValueError("A2 expert requires supplied robustness evidence")
        if len(all_hashes) != len(set(all_hashes)):
            raise ValueError("A2 evidence fingerprint cannot masquerade as multiple evidence classes")
        object.__setattr__(self, "evidence_summary", _line(self.evidence_summary, "A2 evidence summary", maximum=24_000))
        object.__setattr__(self, "question", _line(self.question, "A2 question", maximum=1_024))

    @property
    def a1_fingerprints(self) -> tuple[str, ...]:
        return tuple(sorted(row.fingerprint for row in self.a1_evidence))

    @property
    def a1_hypothesis_fingerprints(self) -> tuple[str, ...]:
        return tuple(sorted({h.fingerprint for row in self.a1_evidence for h in row.hypotheses}))

    @property
    def allowed_citations(self) -> frozenset[str]:
        values = set(self.a1_fingerprints)
        values.update(self.a1_hypothesis_fingerprints)
        for name in (
            "strategy_fingerprints",
            "walk_forward_fingerprints",
            "purged_validation_fingerprints",
            "parameter_stability_fingerprints",
            "regime_torture_fingerprints",
            "cost_torture_fingerprints",
            "forward_decay_fingerprints",
            "tail_risk_fingerprints",
            "dependency_fingerprints",
            "robustness_gate_fingerprints",
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
            "a1_evidence": self.a1_fingerprints,
            "a1_hypotheses": self.a1_hypothesis_fingerprints,
            "strategy": self.strategy_fingerprints,
            "walk_forward": self.walk_forward_fingerprints,
            "purged_validation": self.purged_validation_fingerprints,
            "parameter_stability": self.parameter_stability_fingerprints,
            "regime_torture": self.regime_torture_fingerprints,
            "cost_torture": self.cost_torture_fingerprints,
            "forward_decay": self.forward_decay_fingerprints,
            "tail_risk": self.tail_risk_fingerprints,
            "dependency": self.dependency_fingerprints,
            "robustness_gate": self.robustness_gate_fingerprints,
            "summary": self.evidence_summary,
            "question": self.question,
        })


@dataclass(frozen=True, slots=True)
class A2Concern:
    concern_key: str
    concern: str
    failure_condition: str
    test_family: A2TestFamily
    test_plan: str
    cited_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        key = _line(self.concern_key, "A2 concern_key", maximum=64)
        if _KEY.fullmatch(key) is None:
            raise ValueError("A2 concern_key contains unsupported characters")
        object.__setattr__(self, "concern_key", key)
        object.__setattr__(self, "concern", _line(self.concern, "A2 concern", maximum=512))
        object.__setattr__(self, "failure_condition", _line(self.failure_condition, "A2 failure_condition", maximum=512))
        object.__setattr__(self, "test_plan", _line(self.test_plan, "A2 test_plan", maximum=512))
        citations = _hashes(self.cited_fingerprints, "A2 concern citation")
        if not citations:
            raise ValueError("A2 concern requires supplied evidence citations")
        object.__setattr__(self, "cited_fingerprints", citations)

    @property
    def research_shape(self) -> tuple[object, ...]:
        return (self.concern, self.failure_condition, self.test_family.value, self.test_plan, self.cited_fingerprints)

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-a2-concern-v1", self.concern_key, *self.research_shape))

    @property
    def profitability_certified(self) -> bool:
        return False

    @property
    def mutation_authority(self) -> bool:
        return False

    @property
    def scheduler_priority_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class A2ExpertEvidence:
    request_fingerprint: str
    model_tag: str
    model_digest: str
    state: A2ExpertState
    rationale_codes: tuple[str, ...]
    concerns: tuple[A2Concern, ...]
    raw_response_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_fingerprint", _sha(self.request_fingerprint, "A2 evidence request"))
        object.__setattr__(self, "model_tag", _line(self.model_tag, "A2 evidence model_tag", maximum=256))
        object.__setattr__(self, "model_digest", _sha(self.model_digest, "A2 evidence model digest"))
        rationale = tuple(_line(value, "A2 rationale code", maximum=96) for value in self.rationale_codes)
        if not 1 <= len(rationale) <= 12 or len(rationale) != len(set(rationale)):
            raise ValueError("A2 evidence requires 1-12 unique rationale codes")
        object.__setattr__(self, "rationale_codes", rationale)
        rows = tuple(self.concerns)
        if len(rows) > _MAX_CONCERNS:
            raise ValueError("A2 expert evidence exceeds bounded concern count")
        if len({row.concern_key for row in rows}) != len(rows):
            raise ValueError("A2 expert concern keys must be unique")
        if len({row.research_shape for row in rows}) != len(rows):
            raise ValueError("A2 expert concerns must be structurally distinct")
        if self.state is A2ExpertState.ROBUSTNESS_TESTS and not rows:
            raise ValueError("A2 robustness_tests state requires at least one concern")
        if self.state is A2ExpertState.INSUFFICIENT_EVIDENCE and rows:
            raise ValueError("insufficient A2 evidence cannot carry concerns")
        object.__setattr__(self, "concerns", rows)
        object.__setattr__(self, "raw_response_sha256", _sha(self.raw_response_sha256, "A2 raw response"))

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
            "concerns": tuple(row.fingerprint for row in self.concerns),
            "raw_response_sha256": self.raw_response_sha256,
        })

    @property
    def profitability_certified(self) -> bool:
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


A2_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": [value.value for value in A2ExpertState]},
        "rationale_codes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 96},
            "minItems": 1,
            "maxItems": 12,
            "uniqueItems": True,
        },
        "concerns": {
            "type": "array",
            "maxItems": _MAX_CONCERNS,
            "items": {
                "type": "object",
                "properties": {
                    "concern_key": {"type": "string", "minLength": 1, "maxLength": 64},
                    "concern": {"type": "string", "minLength": 1, "maxLength": 512},
                    "failure_condition": {"type": "string", "minLength": 1, "maxLength": 512},
                    "test_family": {"type": "string", "enum": [value.value for value in A2TestFamily]},
                    "test_plan": {"type": "string", "minLength": 1, "maxLength": 512},
                    "cited_fingerprints": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 64, "maxLength": 64},
                        "minItems": 1,
                        "maxItems": 20,
                        "uniqueItems": True,
                    },
                },
                "required": ["concern_key", "concern", "failure_condition", "test_family", "test_plan", "cited_fingerprints"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["state", "rationale_codes", "concerns"],
    "additionalProperties": False,
}


def build_a2_prompt_payload(request: A2ExpertRequest) -> dict[str, object]:
    return {
        "protocol": PROTOCOL,
        "prompt_version": PROMPT_VERSION,
        "role": "a2_quant_profitability_robustness_expert",
        "school": "A2",
        "request_fingerprint": request.fingerprint,
        "campaign_checkpoint_fingerprint": request.campaign_checkpoint.fingerprint,
        "context_fingerprint": request.context_fingerprint,
        "constraints": [
            "research_only",
            "interrogate_existing_A1_hypotheses_not_invent_new_edge_hypotheses",
            "do_not_certify_profitability_or_A2_pass",
            "do_not_invent_numeric_policy_thresholds",
            "no_broker_or_order_actions",
            "no_position_sizing_or_risk_changes",
            "no_guardian_override",
            "no_champion_promotion",
            "no_scheduler_score_priority_or_resource_decision",
            "no_executable_strategy_mutation_fields_or_parameter_updates",
            "cite_only_supplied_fingerprints",
            "use_only_A2_test_families",
            "return_at_most_four_structurally_distinct_concerns",
            "when_evidence_is_insufficient_return_insufficient_evidence",
            "M174_and_M160_remain_decision_authorities",
        ],
        "upstream_A1": {
            "evidence_fingerprints": request.a1_fingerprints,
            "hypothesis_fingerprints": request.a1_hypothesis_fingerprints,
        },
        "supplied_evidence": {
            "strategy": request.strategy_fingerprints,
            "walk_forward": request.walk_forward_fingerprints,
            "purged_temporal_validation": request.purged_validation_fingerprints,
            "parameter_stability": request.parameter_stability_fingerprints,
            "regime_torture": request.regime_torture_fingerprints,
            "cost_slippage_torture": request.cost_torture_fingerprints,
            "forward_decay": request.forward_decay_fingerprints,
            "tail_risk": request.tail_risk_fingerprints,
            "dependency_stress": request.dependency_fingerprints,
            "robustness_gate": request.robustness_gate_fingerprints,
            "summary": request.evidence_summary,
        },
        "question": request.question,
        "allowed_test_families": [value.value for value in A2TestFamily],
    }


def parse_a2_expert_response(request: A2ExpertRequest, response_text: str) -> A2ExpertEvidence:
    raw = json.loads(response_text)
    if not isinstance(raw, dict) or set(raw) != {"state", "rationale_codes", "concerns"}:
        raise ValueError("A2 expert response schema mismatch")
    if not isinstance(raw["state"], str):
        raise ValueError("A2 expert state must be a string")
    try:
        state = A2ExpertState(raw["state"])
    except ValueError as exc:
        raise ValueError("A2 expert state invalid") from exc
    rationale = _string_array(raw["rationale_codes"], "A2 rationale_codes", maximum_items=12, maximum_length=96)
    if not rationale:
        raise ValueError("A2 expert rationale cannot be empty")
    rows = raw["concerns"]
    if not isinstance(rows, list) or len(rows) > _MAX_CONCERNS:
        raise ValueError("A2 concerns must be a bounded array")
    expected = {"concern_key", "concern", "failure_condition", "test_family", "test_plan", "cited_fingerprints"}
    text_fields = ("concern_key", "concern", "failure_condition", "test_family", "test_plan")
    concerns: list[A2Concern] = []
    for item in rows:
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("A2 concern schema mismatch")
        if any(not isinstance(item[name], str) for name in text_fields):
            raise ValueError("A2 concern text fields must be strings")
        citations = _string_array(item["cited_fingerprints"], "A2 cited_fingerprints", maximum_items=20, maximum_length=64)
        normalized = tuple(_sha(value, "A2 cited fingerprint") for value in citations)
        if any(value not in request.allowed_citations for value in normalized):
            raise ValueError("A2 concern cited evidence not supplied")
        try:
            family = A2TestFamily(item["test_family"])
        except ValueError as exc:
            raise ValueError("A2 concern test family is outside A2 scope") from exc
        concerns.append(A2Concern(
            concern_key=item["concern_key"],
            concern=item["concern"],
            failure_condition=item["failure_condition"],
            test_family=family,
            test_plan=item["test_plan"],
            cited_fingerprints=normalized,
        ))
    return A2ExpertEvidence(
        request_fingerprint=request.fingerprint,
        model_tag=request.model_tag,
        model_digest=request.model_digest,
        state=state,
        rationale_codes=rationale,
        concerns=tuple(concerns),
        raw_response_sha256=sha256(response_text.encode("utf-8")).hexdigest(),
    )


class A2ExpertAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class A2ExpertRunResult:
    status: A2ExpertAvailability
    evidence: A2ExpertEvidence | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.status is A2ExpertAvailability.AVAILABLE:
            if self.evidence is None or self.error:
                raise ValueError("available A2 expert result requires evidence only")
        elif self.evidence is not None or not self.error.strip():
            raise ValueError("unavailable A2 expert result requires error only")

    @property
    def available(self) -> bool:
        return self.status is A2ExpertAvailability.AVAILABLE

    @property
    def broker_write_authority(self) -> bool:
        return False


class A2ExpertAgent:
    def __init__(self, generator: StructuredResearchGenerator) -> None:
        self._generator = generator

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def profitability_certification_authorized(self) -> bool:
        return False

    @property
    def mutation_authorized(self) -> bool:
        return False

    @property
    def scheduler_priority_authorized(self) -> bool:
        return False

    def research(self, request: A2ExpertRequest) -> A2ExpertRunResult:
        try:
            response = self._generator.generate(
                model_tag=request.model_tag,
                model_digest=request.model_digest,
                system_message=(
                    "You are Dusty Dragon's A2 quant-profitability robustness expert. "
                    "Interrogate supplied A1 research using only supplied robustness evidence. "
                    "Return schema-valid concerns/tests only; you cannot certify profitability, trade, "
                    "change risk, override Guardian, promote, schedule, or mutate strategies."
                ),
                prompt_payload=build_a2_prompt_payload(request),
                response_schema=A2_RESPONSE_SCHEMA,
            )
            validate_response_identity(response, model_tag=request.model_tag, model_digest=request.model_digest)
            evidence = parse_a2_expert_response(request, response.content)
            return A2ExpertRunResult(A2ExpertAvailability.AVAILABLE, evidence=evidence)
        except (ValueError, TypeError, RuntimeError, OSError, json.JSONDecodeError) as exc:
            return A2ExpertRunResult(
                A2ExpertAvailability.UNAVAILABLE,
                error=f"a2_expert_unavailable:{compact_error(exc)}",
            )
