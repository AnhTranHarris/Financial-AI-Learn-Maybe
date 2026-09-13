from __future__ import annotations

"""Semantic-contract Ollama reconstruction used by the normalized canary lane.

This adapter leaves the certified M196.5 reconstructor untouched. It adds the
explicit unit/domain contract to the prompt and validates the parsed candidate
before exposing it as AVAILABLE. Structured JSON is transport assistance, not a
trust boundary.
"""

from datetime import datetime
from hashlib import sha256
import json
from urllib.error import HTTPError, URLError

from .ollama_quant_reviewer import Transport, _urllib_transport
from .ollama_strategy_reconstruction import (
    KEEP_ALIVE,
    NUM_CTX,
    NUM_PREDICT,
    LocalStrategyReconstructionResult,
    OllamaReconstructionRequest,
    OllamaStrategyReconstructor,
    ReconstructionAvailability,
    _canonical,
    _parse_response,
    _response_schema,
)
from .ollama_strategy_reconstruction_retry import TruncationRetryTransport
from .reconstruction_feature_contract import prompt_feature_contract, validate_reconstruction_spec


class SemanticContractOllamaStrategyReconstructor(OllamaStrategyReconstructor):
    """Existing fail-closed adapter plus explicit model-facing feature semantics."""

    def reconstruct(
        self,
        request: OllamaReconstructionRequest,
        *,
        created_at: datetime,
    ) -> LocalStrategyReconstructionResult:
        try:
            installed = self._model_digest(request.model_tag)
            if installed != request.model_digest:
                return self._unavailable("ollama_model_digest_mismatch")
            schema = _response_schema(request)
            response = self._transport(
                "POST",
                f"{self.base_url}/api/chat",
                {
                    "model": request.model_tag,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You reconstruct trading theories for research only. Return only the required JSON. "
                                "Every field you add is a hypothesis to falsify, never a claim about what the source used. "
                                "Feature suffix numbers are lookback periods, never indicator values. "
                                "Thresholds must obey each feature's unit and numeric domain exactly. "
                                "You have no broker, trading, promotion, risk, Guardian, capital, tool, or credential authority."
                            ),
                        },
                        {"role": "user", "content": _canonical(_semantic_prompt_payload(request))},
                    ],
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "keep_alive": KEEP_ALIVE,
                    "options": {
                        "temperature": 0,
                        "num_ctx": NUM_CTX,
                        "num_predict": NUM_PREDICT,
                    },
                },
                self.timeout_seconds,
            )
            if response.get("done_reason") == "length":
                return self._unavailable("ollama_reconstruction_truncated")
            message = response.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                return self._unavailable("ollama_chat_response_missing_content")
            raw_text = message["content"]
            raw_sha = sha256(raw_text.encode("utf-8")).hexdigest()
            reconstruction = _parse_response(request, raw_text, raw_sha, created_at)
            validate_reconstruction_spec(reconstruction.candidate_spec)
            return LocalStrategyReconstructionResult(
                ReconstructionAvailability.AVAILABLE,
                reconstruction,
                request.fingerprint,
                raw_sha,
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return self._unavailable(f"ollama_semantic_reconstruction_failed:{type(exc).__name__}:{exc}")


class BoundedRetrySemanticOllamaStrategyReconstructor(SemanticContractOllamaStrategyReconstructor):
    """Semantic reconstructor with the existing single truncation-only retry."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 240.0,
        transport: Transport = _urllib_transport,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=TruncationRetryTransport(transport),
        )


def _semantic_prompt_payload(request: OllamaReconstructionRequest) -> dict[str, object]:
    proposal = request.proposal
    contracts = prompt_feature_contract(request.allowed_features)
    return {
        "protocol": "dusty-m1968-semantic-ollama-reconstruction-v1",
        "request_sha256": request.fingerprint,
        "source": {
            "proposal_fingerprint": proposal.fingerprint,
            "title": proposal.title,
            "symbols": proposal.symbols,
            "timeframes": proposal.timeframes,
            "components": proposal.components,
            "declared_rules": proposal.declared_rules,
            "unresolved": proposal.unresolved,
        },
        "allowed": {
            "symbols": request.allowed_symbols,
            "timeframes": request.allowed_timeframes,
            "feature_contract": contracts,
            "feature_names": tuple(row["name"] for row in contracts),
            "sessions": request.allowed_sessions,
            "directions": ("long", "short"),
            "execution_sensitivity": ("low", "normal", "high"),
        },
        "instructions": (
            "Build one small falsifiable candidate using only feature_names. "
            "Interpret every threshold using feature_contract units and domains. "
            "Do not use a lookback period as a feature value. Do not infer source authorship or performance. "
            "Prefer simple rules; testing, not extra clauses, decides whether an edge exists."
        ),
    }


broker_write_authority = False
live_write_authority = False
promotion_authority = False
risk_override_authority = False
retry_authority = False
