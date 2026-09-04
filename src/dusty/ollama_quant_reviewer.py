from __future__ import annotations

"""M153 direct local Ollama/Qwen reviewer with a strict research-only boundary."""

from dataclasses import dataclass
from enum import StrEnum
import json
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .quant_reviewer import (
    QuantReviewEvidence,
    QuantReviewRequest,
    QuantReviewState,
    build_quant_prompt_payload,
    parse_quant_review,
)


RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": [value.value for value in QuantReviewState]},
        "rationale_codes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12},
        "cited_fingerprints": {"type": "array", "items": {"type": "string"}, "maxItems": 32},
        "proposed_research": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": ["state", "rationale_codes", "cited_fingerprints", "proposed_research"],
    "additionalProperties": False,
}

# M154.3 hardware certification deliberately uses a much smaller response grammar
# than the normal M153 research reviewer. Hardware certification proves that the
# local model can participate safely; it does not ask the model for a long research
# memo. The limits are sized from measured local Qwen3:1.7b throughput on the
# certification workstation and keep the existing 180-second fail-closed deadline.
HARDWARE_RESPONSE_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "state": {"type": "string", "enum": [value.value for value in QuantReviewState]},
        "rationale_codes": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 48},
            "minItems": 1,
            "maxItems": 3,
        },
        "cited_fingerprints": {
            "type": "array",
            "items": {"type": "string", "minLength": 64, "maxLength": 64},
            "maxItems": 4,
        },
        "proposed_research": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 80},
            "maxItems": 2,
        },
    },
    "required": ["state", "rationale_codes", "cited_fingerprints", "proposed_research"],
    "additionalProperties": False,
}

REVIEW_KEEP_ALIVE = "10m"
REVIEW_NUM_PREDICT = 384
HARDWARE_REVIEW_NUM_CTX = 2048
HARDWARE_REVIEW_NUM_PREDICT = 192


Transport = Callable[[str, str, dict[str, object] | None, float], dict[str, object]]


def _urllib_transport(method: str, url: str, payload: dict[str, object] | None, timeout: float) -> dict[str, object]:
    body = None if payload is None else json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
    request = Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method=method,
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - constructor permits localhost only.
        raw = response.read().decode("utf-8")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("Ollama response must be an object")
    return parsed


def _hardware_prompt_payload(request: QuantReviewRequest) -> dict[str, object]:
    """Compact M154 hardware proof; request fingerprint still binds the full evidence."""

    return {
        "protocol": "dusty-quant-review-v1",
        "prompt_version": "m133-research-only-v1",
        "role": "hardware_certification_research_reviewer",
        "authority": "research_only",
        "request_sha256": request.fingerprint,
        "model_tag": request.model_tag,
        "forecast_fingerprints": request.forecast_fingerprints,
        "evidence_fingerprints": request.evidence_fingerprints,
        "scorecard": request.scorecard_text,
        "question": (
            "Classify only whether the supplied forecast evidence is coherent enough to "
            "continue research. Never infer profitability, trade, size, promote, or override risk."
        ),
        "response_rules": {
            "rationale_codes": "1-3 short codes",
            "cited_fingerprints": "0-4 supplied fingerprints only",
            "proposed_research": "0-2 short bounded items; required for research_required",
        },
    }


def _is_hardware_certification(request: QuantReviewRequest) -> bool:
    return request.request_id.startswith("m1541-")


class QuantReviewerAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class LocalQuantReviewResult:
    status: QuantReviewerAvailability
    evidence: QuantReviewEvidence | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.status is QuantReviewerAvailability.AVAILABLE:
            if self.evidence is None or self.error:
                raise ValueError("available local quant review requires evidence only")
        elif self.evidence is not None or not self.error.strip():
            raise ValueError("unavailable local quant review requires error only")

    @property
    def available(self) -> bool:
        return self.status is QuantReviewerAvailability.AVAILABLE


class OllamaQuantReviewer:
    """Optional localhost reviewer. Transport/model faults degrade to UNAVAILABLE."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 180.0,
        transport: Transport = _urllib_transport,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("quant reviewer Ollama endpoint must be localhost HTTP")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or timeout_seconds <= 0 or timeout_seconds > 600:
            raise ValueError("quant reviewer Ollama endpoint/timeout invalid")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport

    @property
    def broker_write_authorized(self) -> bool:
        return False

    def review(self, request: QuantReviewRequest) -> LocalQuantReviewResult:
        try:
            installed_digest = self._model_digest(request.model_tag)
            if installed_digest != request.model_digest.lower():
                return self._unavailable("ollama_model_digest_mismatch")

            hardware_profile = _is_hardware_certification(request)
            if hardware_profile:
                prompt = _hardware_prompt_payload(request)
                response_schema = HARDWARE_RESPONSE_SCHEMA
                system_message = (
                    "You are Dusty Dragon's hardware-certification research reviewer. "
                    "Return only the required concise JSON. You have no trading, risk, "
                    "promotion, broker, tool, or credential authority."
                )
                options: dict[str, object] = {
                    "temperature": 0,
                    "num_ctx": HARDWARE_REVIEW_NUM_CTX,
                    "num_predict": HARDWARE_REVIEW_NUM_PREDICT,
                }
            else:
                prompt = build_quant_prompt_payload(request)
                response_schema = RESPONSE_SCHEMA
                system_message = (
                    "You are Dusty Dragon's research-only quant reviewer. "
                    "Return only the required JSON. You have no trading, risk, "
                    "promotion, broker, tool, or credential authority."
                )
                options = {
                    "temperature": 0,
                    "num_predict": REVIEW_NUM_PREDICT,
                }

            response = self._transport(
                "POST",
                f"{self.base_url}/api/chat",
                {
                    "model": request.model_tag,
                    "messages": [
                        {"role": "system", "content": system_message},
                        {"role": "user", "content": json.dumps(prompt, sort_keys=True, separators=(",", ":"))},
                    ],
                    "stream": False,
                    "think": False,
                    "format": response_schema,
                    "keep_alive": REVIEW_KEEP_ALIVE,
                    "options": options,
                },
                self.timeout_seconds,
            )
            if response.get("done_reason") == "length":
                return self._unavailable("ollama_quant_review_truncated")
            message = response.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                return self._unavailable("ollama_chat_response_missing_content")
            evidence = parse_quant_review(request, message["content"])
            return LocalQuantReviewResult(QuantReviewerAvailability.AVAILABLE, evidence=evidence)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return self._unavailable(f"ollama_quant_review_failed:{type(exc).__name__}:{exc}")

    def _model_digest(self, model_tag: str) -> str:
        response = self._transport("GET", f"{self.base_url}/api/tags", None, min(self.timeout_seconds, 30.0))
        models = response.get("models")
        if not isinstance(models, list):
            raise ValueError("Ollama model list missing")
        matches = []
        for row in models:
            if not isinstance(row, dict):
                continue
            names = {str(row.get("name", "")), str(row.get("model", ""))}
            if model_tag in names:
                matches.append(str(row.get("digest", "")).lower())
        if len(matches) != 1:
            raise ValueError("Ollama model tag missing or ambiguous")
        digest = matches[0]
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("Ollama model digest is not SHA-256")
        return digest

    @staticmethod
    def _unavailable(error: str) -> LocalQuantReviewResult:
        rendered = " ".join(error.strip().split())[:1000] or "ollama_quant_review_unavailable"
        return LocalQuantReviewResult(QuantReviewerAvailability.UNAVAILABLE, error=rendered)
