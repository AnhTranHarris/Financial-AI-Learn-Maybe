from __future__ import annotations

"""Bounded local Ollama classifier for human-facing quant strategy identity.

The classifier cannot name a strategy freely. It may only select one value from
each Dusty-owned taxonomy enum. The final UI title is rendered deterministically
by ``strategy_taxonomy``. JSON-schema enforcement is treated as advisory; every
field is validated again after parsing because some Ollama backends have ignored
structured-output constraints. Special catalyst/structure/session labels must
also be supported by explicit reconstruction evidence.
"""

from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from .ollama_quant_reviewer import Transport, _urllib_transport
from .strategy_taxonomy import (
    QuantStrategyIdentity,
    StrategyArchetype,
    StrategyCatalyst,
    StrategySessionProfile,
    StrategyStructure,
    validate_quant_identity_support,
)
from .trading_skills import ReconstructionRule, ReconstructionRuleBasis, StrategyReconstruction


KEEP_ALIVE = "10m"
NUM_PREDICT = 192


class ClassificationAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class QuantStrategyClassification:
    identity: QuantStrategyIdentity
    model_tag: str
    model_digest: str
    raw_response_sha256: str

    def __post_init__(self) -> None:
        if not self.model_tag.strip() or "\n" in self.model_tag or "\r" in self.model_tag:
            raise ValueError("classifier model tag invalid")
        for value in (self.model_digest, self.raw_response_sha256):
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError("classification provenance requires SHA-256")

    @property
    def fingerprint(self) -> str:
        payload = {
            "protocol": "dusty-m1965-quant-strategy-classification-v1",
            "identity": self.identity.payload,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "raw_response_sha256": self.raw_response_sha256,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class QuantStrategyClassificationResult:
    status: ClassificationAvailability
    classification: QuantStrategyClassification | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.status is ClassificationAvailability.AVAILABLE:
            if self.classification is None or self.error:
                raise ValueError("available classification requires classification only")
        elif self.classification is not None or not self.error.strip():
            raise ValueError("unavailable classification requires error only")

    @property
    def available(self) -> bool:
        return self.status is ClassificationAvailability.AVAILABLE


class OllamaStrategyClassifier:
    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 90.0,
        transport: Transport = _urllib_transport,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("strategy classifier Ollama endpoint must be localhost HTTP")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or not 0 < timeout_seconds <= 300:
            raise ValueError("strategy classifier Ollama endpoint/timeout invalid")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport

    def classify(
        self,
        reconstruction: StrategyReconstruction,
        *,
        model_tag: str,
        model_digest: str,
    ) -> QuantStrategyClassificationResult:
        try:
            model_tag = _one_line(model_tag, "classifier model tag", 128)
            expected = _sha(model_digest, "classifier model digest")
            if self._model_digest(model_tag) != expected:
                return self._unavailable("ollama_classifier_model_digest_mismatch")
            schema = _response_schema()
            response = self._transport(
                "POST",
                f"{self.base_url}/api/chat",
                {
                    "model": model_tag,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Classify one trading research hypothesis into Dusty Dragon's bounded quant taxonomy. "
                                "Return only the required JSON enums. Do not create a marketing title, claim profitability, "
                                "infer hidden source authorship, alter strategy rules, or grant any trading authority. "
                                "Named catalysts, Fibonacci structure, and geographic/session handoffs must be explicit in the supplied evidence."
                            ),
                        },
                        {"role": "user", "content": json.dumps(_prompt(reconstruction), sort_keys=True, separators=(",", ":"))},
                    ],
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "keep_alive": KEEP_ALIVE,
                    "options": {"temperature": 0, "num_predict": NUM_PREDICT},
                },
                self.timeout_seconds,
            )
            if response.get("done_reason") == "length":
                return self._unavailable("ollama_strategy_classification_truncated")
            message = response.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                return self._unavailable("ollama_strategy_classification_missing_content")
            raw_text = message["content"]
            raw_sha = sha256(raw_text.encode("utf-8")).hexdigest()
            identity = _parse(raw_text)
            validate_quant_identity_support(reconstruction, identity)
            return QuantStrategyClassificationResult(
                ClassificationAvailability.AVAILABLE,
                QuantStrategyClassification(identity, model_tag, expected, raw_sha),
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return self._unavailable(f"ollama_strategy_classification_failed:{type(exc).__name__}:{exc}")

    def _model_digest(self, model_tag: str) -> str:
        response = self._transport("GET", f"{self.base_url}/api/tags", None, min(self.timeout_seconds, 30.0))
        models = response.get("models")
        if not isinstance(models, list):
            raise ValueError("Ollama model list missing")
        matches = []
        for row in models:
            if isinstance(row, dict) and model_tag in {str(row.get("name", "")), str(row.get("model", ""))}:
                matches.append(str(row.get("digest", "")).lower())
        if len(matches) != 1:
            raise ValueError("Ollama classifier model tag missing or ambiguous")
        return _sha(matches[0], "installed classifier model digest")

    @staticmethod
    def _unavailable(error: str) -> QuantStrategyClassificationResult:
        return QuantStrategyClassificationResult(
            ClassificationAvailability.UNAVAILABLE,
            error=" ".join(error.strip().split())[:1000] or "ollama_strategy_classification_unavailable",
        )


def _one_line(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    value = value.strip()
    if not value or "\n" in value or "\r" in value or len(value) > maximum:
        raise ValueError(f"{label} invalid")
    return value


def _sha(value: object, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256")
    return rendered


def _prompt(row: StrategyReconstruction) -> dict[str, object]:
    return {
        "protocol": "dusty-m1965-quant-strategy-classification-v1",
        "source_title": row.title,
        "symbols": row.symbols,
        "timeframe": row.timeframe,
        "direction": row.candidate_spec.direction.value,
        "session_filters": row.candidate_spec.session_filters,
        "rules": [(rule.name, rule.value, rule.basis.value) for rule in row.rules],
        "unresolved_source_rules": row.unresolved_source_rules,
        "taxonomy": {
            "archetype": [value.value for value in StrategyArchetype],
            "catalyst": [value.value for value in StrategyCatalyst],
            "structure": [value.value for value in StrategyStructure],
            "session_profile": [value.value for value in StrategySessionProfile],
        },
    }


def _response_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "archetype": {"type": "string", "enum": [value.value for value in StrategyArchetype]},
            "catalyst": {"type": "string", "enum": [value.value for value in StrategyCatalyst]},
            "structure": {"type": "string", "enum": [value.value for value in StrategyStructure]},
            "session_profile": {"type": "string", "enum": [value.value for value in StrategySessionProfile]},
        },
        "required": ["archetype", "catalyst", "structure", "session_profile"],
        "additionalProperties": False,
    }


def _parse(text: str) -> QuantStrategyIdentity:
    raw = json.loads(text)
    expected = {"archetype", "catalyst", "structure", "session_profile"}
    if not isinstance(raw, dict) or set(raw) != expected:
        raise ValueError("Ollama strategy classification schema mismatch")
    return QuantStrategyIdentity(
        StrategyArchetype(_one_line(raw["archetype"], "archetype", 64)),
        StrategyCatalyst(_one_line(raw["catalyst"], "catalyst", 64)),
        StrategyStructure(_one_line(raw["structure"], "structure", 64)),
        StrategySessionProfile(_one_line(raw["session_profile"], "session_profile", 64)),
    )


def attach_quant_identity(
    reconstruction: StrategyReconstruction,
    classification: QuantStrategyClassification,
) -> StrategyReconstruction:
    """Return a new immutable reconstruction carrying classification provenance."""

    validate_quant_identity_support(reconstruction, classification.identity)
    rules = tuple(rule for rule in reconstruction.rules if not rule.name.startswith("identity."))
    identity_rules = (
        ReconstructionRule("identity.archetype", classification.identity.archetype.value, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("identity.catalyst", classification.identity.catalyst.value, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("identity.structure", classification.identity.structure.value, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("identity.session_profile", classification.identity.session_profile.value, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("identity.classifier_model_digest", classification.model_digest, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("identity.classifier_raw_sha256", classification.raw_response_sha256, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
    )
    return replace(reconstruction, rules=(*rules, *identity_rules))
