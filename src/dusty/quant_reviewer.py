from __future__ import annotations

"""Strict research-only contract for a future local Qwen reviewer."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json


PROTOCOL = "dusty-quant-review-v1"
PROMPT_VERSION = "m133-research-only-v1"


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


class QuantReviewState(StrEnum):
    RESOLVED = "resolved"
    WAIT = "wait"
    NO_TRADE = "no_trade"
    RESEARCH_REQUIRED = "research_required"


@dataclass(frozen=True, slots=True)
class QuantReviewRequest:
    request_id: str
    model_tag: str
    model_digest: str
    forecast_fingerprints: tuple[str, ...]
    strategy_fingerprints: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    scorecard_text: str
    question: str

    def __post_init__(self) -> None:
        if not self.request_id.strip() or not self.model_tag.strip() or not self.question.strip():
            raise ValueError("quant review request identity/question required")
        hashes = (
            self.model_digest,
            *self.forecast_fingerprints,
            *self.strategy_fingerprints,
            *self.evidence_fingerprints,
        )
        if any(len(value) != 64 for value in hashes):
            raise ValueError("quant review request requires SHA-256 identities")
        if not self.forecast_fingerprints:
            raise ValueError("quant review requires forecast evidence")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": PROTOCOL,
                "prompt_version": PROMPT_VERSION,
                "request_id": self.request_id,
                "model_tag": self.model_tag,
                "model_digest": self.model_digest,
                "forecast_fingerprints": self.forecast_fingerprints,
                "strategy_fingerprints": self.strategy_fingerprints,
                "evidence_fingerprints": self.evidence_fingerprints,
                "scorecard_text": self.scorecard_text,
                "question": self.question,
            }
        )

    @property
    def allowed_citations(self) -> frozenset[str]:
        return frozenset(
            self.forecast_fingerprints
            + self.strategy_fingerprints
            + self.evidence_fingerprints
        )


@dataclass(frozen=True, slots=True)
class QuantReviewEvidence:
    protocol: str
    prompt_version: str
    request_sha256: str
    model_tag: str
    model_digest: str
    state: QuantReviewState
    rationale_codes: tuple[str, ...]
    cited_fingerprints: tuple[str, ...]
    proposed_research: tuple[str, ...]
    raw_response_sha256: str
    broker_write_authority: bool = False
    entry_veto_authority: bool = False
    promotion_authority: bool = False
    risk_override_authority: bool = False

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL or self.prompt_version != PROMPT_VERSION:
            raise ValueError("quant review protocol/prompt mismatch")
        hashes = (
            self.request_sha256,
            self.model_digest,
            self.raw_response_sha256,
            *self.cited_fingerprints,
        )
        if any(len(value) != 64 for value in hashes):
            raise ValueError("quant review evidence requires SHA-256 identity")
        if not self.rationale_codes:
            raise ValueError("quant review requires rationale codes")
        if self.state is QuantReviewState.RESEARCH_REQUIRED and not self.proposed_research:
            raise ValueError("research_required must define bounded research")
        if (
            self.broker_write_authority
            or self.entry_veto_authority
            or self.promotion_authority
            or self.risk_override_authority
        ):
            raise ValueError("quant reviewer cannot receive operational authority")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": self.protocol,
                "prompt_version": self.prompt_version,
                "request_sha256": self.request_sha256,
                "model_tag": self.model_tag,
                "model_digest": self.model_digest,
                "state": self.state.value,
                "rationale_codes": self.rationale_codes,
                "cited_fingerprints": self.cited_fingerprints,
                "proposed_research": self.proposed_research,
                "raw_response_sha256": self.raw_response_sha256,
                "broker_write_authority": self.broker_write_authority,
                "entry_veto_authority": self.entry_veto_authority,
                "promotion_authority": self.promotion_authority,
                "risk_override_authority": self.risk_override_authority,
            }
        )


def build_quant_prompt_payload(request: QuantReviewRequest) -> dict[str, object]:
    """No tool definitions, broker state, credentials, or order interface."""

    return {
        "protocol": PROTOCOL,
        "prompt_version": PROMPT_VERSION,
        "role": "research_quant_reviewer",
        "constraints": [
            "research_only",
            "no_trade_authority",
            "no_risk_override",
            "no_champion_promotion",
            "cite_only_supplied_fingerprints",
            "when evidence is weak use wait, no_trade, or research_required",
            "research proposals must be bounded and testable",
        ],
        "request_sha256": request.fingerprint,
        "model_tag": request.model_tag,
        "forecast_fingerprints": request.forecast_fingerprints,
        "strategy_fingerprints": request.strategy_fingerprints,
        "evidence_fingerprints": request.evidence_fingerprints,
        "scorecard": request.scorecard_text,
        "question": request.question,
        "response_schema": {
            "state": [value.value for value in QuantReviewState],
            "rationale_codes": "nonempty string array",
            "cited_fingerprints": "string array; supplied evidence only",
            "proposed_research": "bounded string array",
        },
    }


def parse_quant_review(
    request: QuantReviewRequest,
    response_text: str,
) -> QuantReviewEvidence:
    """Fail closed on hallucinated keys, citations, or operational commands."""

    raw = json.loads(response_text)
    if not isinstance(raw, dict):
        raise ValueError("quant review response must be an object")
    expected_keys = {
        "state",
        "rationale_codes",
        "cited_fingerprints",
        "proposed_research",
    }
    if set(raw) != expected_keys:
        raise ValueError("quant review response schema mismatch")
    try:
        state = QuantReviewState(str(raw["state"]))
    except ValueError as exc:
        raise ValueError("quant review state invalid") from exc

    def strings(name: str) -> tuple[str, ...]:
        value = raw[name]
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError(f"quant review {name} must be a string array")
        return tuple(value)

    rationale = strings("rationale_codes")
    citations = strings("cited_fingerprints")
    research = strings("proposed_research")
    if not rationale:
        raise ValueError("quant review rationale cannot be empty")
    if any(value not in request.allowed_citations for value in citations):
        raise ValueError("quant review cited evidence not supplied")
    if state is QuantReviewState.RESEARCH_REQUIRED and not research:
        raise ValueError("research_required missing research agenda")
    if len(research) > 5:
        raise ValueError("quant review research agenda exceeds bounded limit")

    return QuantReviewEvidence(
        protocol=PROTOCOL,
        prompt_version=PROMPT_VERSION,
        request_sha256=request.fingerprint,
        model_tag=request.model_tag,
        model_digest=request.model_digest,
        state=state,
        rationale_codes=rationale,
        cited_fingerprints=citations,
        proposed_research=research,
        raw_response_sha256=sha256(response_text.encode("utf-8")).hexdigest(),
    )
