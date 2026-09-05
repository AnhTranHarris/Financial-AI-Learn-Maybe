from __future__ import annotations

"""M190 bounded A1 edge-discovery expert.

The expert may propose falsifiable research hypotheses from supplied evidence.
It cannot trade, size risk, score M160 priorities, mutate M158 strategies,
promote Champions, or claim that an edge has been proven. Provider output is
untrusted until the exact schema, citations, model identity and A1 campaign
state are validated by Dusty.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from typing import Iterable

from .autonomous_research_campaign import CampaignCheckpoint, CampaignStatus
from .research_expert_runtime import (
    StructuredResearchGenerator,
    compact_error,
    validate_response_identity,
)


PROTOCOL = "dusty-a1-edge-expert-v1"
PROMPT_VERSION = "m190-a1-edge-hypothesis-v1"
_MAX_HYPOTHESES = 3
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
    rendered = " ".join(str(value).strip().split())
    if not rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty and <= {maximum} characters")
    return rendered


def _hashes(values: Iterable[str], label: str) -> tuple[str, ...]:
    rows = tuple(sorted(_sha(value, label) for value in values))
    if len(rows) != len(set(rows)):
        raise ValueError(f"{label} fingerprints must be unique")
    return rows


def _strings(value: object, label: str, *, maximum_items: int, maximum_length: int) -> tuple[str, ...]:
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


class A1ExpertState(StrEnum):
    HYPOTHESES = "hypotheses"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class A1TestFamily(StrEnum):
    FEATURE_ABLATION = "feature_ablation"
    ENTRY_RULE_ABLATION = "entry_rule_ablation"
    EXIT_RULE_ABLATION = "exit_rule_ablation"
    REGIME_SLICE = "regime_slice"
    SESSION_SLICE = "session_slice"
    FORECAST_ABLATION = "forecast_ablation"
    NEGATIVE_CONTROL = "negative_control"
    DATA_QUALITY = "data_quality"
    SAMPLE_EXPANSION = "sample_expansion"


@dataclass(frozen=True, slots=True)
class A1ExpertRequest:
    request_id: str
    model_tag: str
    model_digest: str
    campaign_checkpoint: CampaignCheckpoint
    context_fingerprint: str
    strategy_fingerprints: tuple[str, ...]
    forecast_fingerprints: tuple[str, ...]
    diagnosis_fingerprints: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    evidence_summary: str
    question: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _line(self.request_id, "A1 request_id", maximum=128))
        object.__setattr__(self, "model_tag", _line(self.model_tag, "A1 model_tag", maximum=256))
        object.__setattr__(self, "model_digest", _sha(self.model_digest, "A1 model digest"))
        if self.campaign_checkpoint.status is not CampaignStatus.ACTIVE:
            raise ValueError("A1 expert requires an active M189 campaign checkpoint")
        if self.campaign_checkpoint.school_index != 0:
            raise ValueError("A1 expert cannot run outside A1 campaign school")
        object.__setattr__(self, "context_fingerprint", _sha(self.context_fingerprint, "A1 context"))
        categories = []
        for name in (
            "strategy_fingerprints",
            "forecast_fingerprints",
            "diagnosis_fingerprints",
            "evidence_fingerprints",
        ):
            rows = _hashes(getattr(self, name), f"A1 {name}")
            object.__setattr__(self, name, rows)
            categories.extend(rows)
        if not categories:
            raise ValueError("A1 expert requires supplied research evidence")
        if len(categories) != len(set(categories)):
            raise ValueError("A1 evidence fingerprint cannot masquerade as multiple evidence classes")
        object.__setattr__(self, "evidence_summary", _line(self.evidence_summary, "A1 evidence summary", maximum=20_000))
        object.__setattr__(self, "question", _line(self.question, "A1 question", maximum=1_024))

    @property
    def allowed_citations(self) -> frozenset[str]:
        return frozenset(
            self.strategy_fingerprints
            + self.forecast_fingerprints
            + self.diagnosis_fingerprints
            + self.evidence_fingerprints
        )

    @property
    def fingerprint(self) -> str:
        return _digest({
            "protocol": PROTOCOL,
            "prompt_version": PROMPT_VERSION,
            "request_id": self.request_id,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "campaign_checkpoint_fingerprint": self.campaign_checkpoint.fingerprint,
            "context_fingerprint": self.context_fingerprint,
            "strategy_fingerprints": self.strategy_fingerprints,
            "forecast_fingerprints": self.forecast_fingerprints,
            "diagnosis_fingerprints": self.diagnosis_fingerprints,
            "evidence_fingerprints": self.evidence_fingerprints,
            "evidence_summary": self.evidence_summary,
            "question": self.question,
        })


@dataclass(frozen=True, slots=True)
class A1Hypothesis:
    hypothesis_key: str
    statement: str
    falsification: str
    test_family: A1TestFamily
    test_plan: str
    cited_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        key = _line(self.hypothesis_key, "A1 hypothesis_key", maximum=64)
        if _KEY.fullmatch(key) is None:
            raise ValueError("A1 hypothesis_key contains unsupported characters")
        object.__setattr__(self, "hypothesis_key", key)
        object.__setattr__(self, "statement", _line(self.statement, "A1 hypothesis statement", maximum=512))
        object.__setattr__(self, "falsification", _line(self.falsification, "A1 hypothesis falsification", maximum=512))
        object.__setattr__(self, "test_plan", _line(self.test_plan, "A1 test plan", maximum=512))
        citations = _hashes(self.cited_fingerprints, "A1 hypothesis citation")
        if not citations:
            raise ValueError("A1 hypothesis requires supplied evidence citations")
        object.__setattr__(self, "cited_fingerprints", citations)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-a1-hypothesis-v1",
            self.hypothesis_key,
            self.statement,
            self.falsification,
            self.test_family.value,
            self.test_plan,
            self.cited_fingerprints,
        ))

    @property
    def causal_claimed(self) -> bool:
        return False

    @property
    def edge_proven(self) -> bool:
        return False

    @property
    def mutation_authority(self) -> bool:
        return False

    @property
    def scheduler_priority_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class A1ExpertEvidence:
    request_fingerprint: str
    model_tag: str
    model_digest: str
    state: A1ExpertState
    rationale_codes: tuple[str, ...]
    hypotheses: tuple[A1Hypothesis, ...]
    raw_response_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_fingerprint", _sha(self.request_fingerprint, "A1 evidence request"))
        object.__setattr__(self, "model_tag", _line(self.model_tag, "A1 evidence model_tag", maximum=256))
        object.__setattr__(self, "model_digest", _sha(self.model_digest, "A1 evidence model digest"))
        rationale = tuple(_line(row, "A1 rationale code", maximum=96) for row in self.rationale_codes)
        if not 1 <= len(rationale) <= 12 or len(rationale) != len(set(rationale)):
            raise ValueError("A1 evidence requires 1-12 unique rationale codes")
        object.__setattr__(self, "rationale_codes", rationale)
        hypotheses = tuple(self.hypotheses)
        if len(hypotheses) > _MAX_HYPOTHESES:
            raise ValueError("A1 expert evidence exceeds bounded hypothesis count")
        if len({row.hypothesis_key for row in hypotheses}) != len(hypotheses):
            raise ValueError("A1 expert hypothesis keys must be unique")
        if len({row.fingerprint for row in hypotheses}) != len(hypotheses):
            raise ValueError("A1 expert hypotheses must be structurally distinct")
        if self.state is A1ExpertState.HYPOTHESES and not hypotheses:
            raise ValueError("A1 hypotheses state requires at least one hypothesis")
        if self.state is A1ExpertState.INSUFFICIENT_EVIDENCE and hypotheses:
            raise ValueError("insufficient A1 evidence cannot carry hypotheses")
        object.__setattr__(self, "hypotheses", hypotheses)
        object.__setattr__(self, "raw_response_sha256", _sha(self.raw_response_sha256, "A1 raw response"))

    @property
    def fingerprint(self) -> str:
        return _digest({
            "protocol": PROTOCOL,
            "prompt_version": PROMPT_VERSION,
            "request_fingerprint": self.request_fingerprint,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "state": self.state.value,
            "rationale_codes": self.rationale_codes,
            "hypothesis_fingerprints": tuple(row.fingerprint for row in self.hypotheses),
            "raw_response_sha256": self.raw_response_sha256,
        })

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


A1_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": [value.value for value in A1ExpertState]},
        "rationale_codes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 96},
            "minItems": 1,
            "maxItems": 12,
            "uniqueItems": True,
        },
        "hypotheses": {
            "type": "array",
            "maxItems": _MAX_HYPOTHESES,
            "items": {
                "type": "object",
                "properties": {
                    "hypothesis_key": {"type": "string", "minLength": 1, "maxLength": 64},
                    "statement": {"type": "string", "minLength": 1, "maxLength": 512},
                    "falsification": {"type": "string", "minLength": 1, "maxLength": 512},
                    "test_family": {"type": "string", "enum": [value.value for value in A1TestFamily]},
                    "test_plan": {"type": "string", "minLength": 1, "maxLength": 512},
                    "cited_fingerprints": {
                        "type": "array",
                        "items": {"type": "string", "minLength": 64, "maxLength": 64},
                        "minItems": 1,
                        "maxItems": 16,
                        "uniqueItems": True,
                    },
                },
                "required": [
                    "hypothesis_key",
                    "statement",
                    "falsification",
                    "test_family",
                    "test_plan",
                    "cited_fingerprints",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["state", "rationale_codes", "hypotheses"],
    "additionalProperties": False,
}


def build_a1_prompt_payload(request: A1ExpertRequest) -> dict[str, object]:
    return {
        "protocol": PROTOCOL,
        "prompt_version": PROMPT_VERSION,
        "role": "a1_edge_discovery_expert",
        "school": "A1",
        "request_fingerprint": request.fingerprint,
        "campaign_checkpoint_fingerprint": request.campaign_checkpoint.fingerprint,
        "context_fingerprint": request.context_fingerprint,
        "constraints": [
            "research_only",
            "generate_falsifiable_hypotheses_not_trade_recommendations",
            "do_not_claim_edge_proven_or_causal_proof",
            "no_broker_or_order_actions",
            "no_position_sizing_or_risk_changes",
            "no_guardian_override",
            "no_champion_promotion",
            "no_scheduler_score_priority_or_resource_decision",
            "no_executable_strategy_mutation_fields_or_parameter_updates",
            "cite_only_supplied_fingerprints",
            "use_only_A1_test_families",
            "return_at_most_three_distinct_hypotheses",
            "when_evidence_is_insufficient_return_insufficient_evidence",
        ],
        "supplied_evidence": {
            "strategy_fingerprints": request.strategy_fingerprints,
            "forecast_fingerprints": request.forecast_fingerprints,
            "diagnosis_fingerprints": request.diagnosis_fingerprints,
            "evidence_fingerprints": request.evidence_fingerprints,
            "summary": request.evidence_summary,
        },
        "question": request.question,
        "allowed_test_families": [value.value for value in A1TestFamily],
    }


def parse_a1_expert_response(request: A1ExpertRequest, response_text: str) -> A1ExpertEvidence:
    raw = json.loads(response_text)
    if not isinstance(raw, dict) or set(raw) != {"state", "rationale_codes", "hypotheses"}:
        raise ValueError("A1 expert response schema mismatch")
    if not isinstance(raw["state"], str):
        raise ValueError("A1 expert state must be a string")
    try:
        state = A1ExpertState(raw["state"])
    except ValueError as exc:
        raise ValueError("A1 expert state invalid") from exc
    rationale = _strings(raw["rationale_codes"], "A1 rationale_codes", maximum_items=12, maximum_length=96)
    if not rationale:
        raise ValueError("A1 expert rationale cannot be empty")
    hypotheses_raw = raw["hypotheses"]
    if not isinstance(hypotheses_raw, list) or len(hypotheses_raw) > _MAX_HYPOTHESES:
        raise ValueError("A1 hypotheses must be a bounded array")
    hypotheses: list[A1Hypothesis] = []
    expected = {"hypothesis_key", "statement", "falsification", "test_family", "test_plan", "cited_fingerprints"}
    text_fields = ("hypothesis_key", "statement", "falsification", "test_family", "test_plan")
    for item in hypotheses_raw:
        if not isinstance(item, dict) or set(item) != expected:
            raise ValueError("A1 hypothesis schema mismatch")
        if any(not isinstance(item[name], str) for name in text_fields):
            raise ValueError("A1 hypothesis text fields must be strings")
        citations = _strings(
            item["cited_fingerprints"],
            "A1 cited_fingerprints",
            maximum_items=16,
            maximum_length=64,
        )
        normalized = tuple(_sha(value, "A1 cited fingerprint") for value in citations)
        if any(value not in request.allowed_citations for value in normalized):
            raise ValueError("A1 hypothesis cited evidence not supplied")
        try:
            family = A1TestFamily(item["test_family"])
        except ValueError as exc:
            raise ValueError("A1 hypothesis test family is outside A1 scope") from exc
        hypotheses.append(
            A1Hypothesis(
                hypothesis_key=item["hypothesis_key"],
                statement=item["statement"],
                falsification=item["falsification"],
                test_family=family,
                test_plan=item["test_plan"],
                cited_fingerprints=normalized,
            )
        )
    return A1ExpertEvidence(
        request_fingerprint=request.fingerprint,
        model_tag=request.model_tag,
        model_digest=request.model_digest,
        state=state,
        rationale_codes=rationale,
        hypotheses=tuple(hypotheses),
        raw_response_sha256=sha256(response_text.encode("utf-8")).hexdigest(),
    )


class A1ExpertAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class A1ExpertRunResult:
    status: A1ExpertAvailability
    evidence: A1ExpertEvidence | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.status is A1ExpertAvailability.AVAILABLE:
            if self.evidence is None or self.error:
                raise ValueError("available A1 expert result requires evidence only")
        elif self.evidence is not None or not self.error.strip():
            raise ValueError("unavailable A1 expert result requires error only")

    @property
    def available(self) -> bool:
        return self.status is A1ExpertAvailability.AVAILABLE

    @property
    def broker_write_authority(self) -> bool:
        return False


class A1ExpertAgent:
    def __init__(self, generator: StructuredResearchGenerator) -> None:
        self._generator = generator

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def mutation_authorized(self) -> bool:
        return False

    @property
    def scheduler_priority_authorized(self) -> bool:
        return False

    def research(self, request: A1ExpertRequest) -> A1ExpertRunResult:
        try:
            response = self._generator.generate(
                model_tag=request.model_tag,
                model_digest=request.model_digest,
                system_message=(
                    "You are Dusty Dragon's A1 edge-discovery research expert. "
                    "Return only schema-valid research hypotheses. You have no trading, broker, "
                    "risk, Guardian, promotion, scheduler, or mutation authority."
                ),
                prompt_payload=build_a1_prompt_payload(request),
                response_schema=A1_RESPONSE_SCHEMA,
            )
            validate_response_identity(
                response,
                model_tag=request.model_tag,
                model_digest=request.model_digest,
            )
            evidence = parse_a1_expert_response(request, response.content)
            return A1ExpertRunResult(A1ExpertAvailability.AVAILABLE, evidence=evidence)
        except (ValueError, TypeError, RuntimeError, OSError, json.JSONDecodeError) as exc:
            return A1ExpertRunResult(
                A1ExpertAvailability.UNAVAILABLE,
                error=f"a1_expert_unavailable:{compact_error(exc)}",
            )
