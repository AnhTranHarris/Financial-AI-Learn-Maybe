from __future__ import annotations

"""Bounded, research-only Ollama health diagnostic for M196.8 recovery.

This module never reconstructs a strategy and never retries a failed canary. It
only determines whether the local model, a tiny chat, and a tiny structured
response are healthy enough to justify a later evidence-gated recovery attempt.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from time import monotonic
from typing import Callable

from .ollama_quant_reviewer import Transport, _urllib_transport


KEEP_ALIVE = "10m"
BASIC_CHAT_TIMEOUT = 60.0
STRUCTURED_CHAT_TIMEOUT = 90.0


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OllamaDiagnosticStage:
    name: str
    status: str
    elapsed_seconds: float
    detail: str

    def __post_init__(self) -> None:
        if self.status not in {"pass", "fail", "skip"}:
            raise ValueError("diagnostic stage status invalid")
        if self.elapsed_seconds < 0:
            raise ValueError("diagnostic elapsed time cannot be negative")


@dataclass(frozen=True, slots=True)
class OllamaReconstructionDiagnostic:
    model_tag: str
    model_digest: str
    stages: tuple[OllamaDiagnosticStage, ...]

    @property
    def status(self) -> str:
        return "pass" if self.stages and all(row.status == "pass" for row in self.stages) else "fail"

    @property
    def fingerprint(self) -> str:
        return _fingerprint(self.to_payload(include_fingerprint=False))

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol": "dusty-m19681-ollama-reconstruction-diagnostic-v1",
            "status": self.status,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "stages": [
                {
                    "name": row.name,
                    "status": row.status,
                    "elapsed_seconds": round(row.elapsed_seconds, 6),
                    "detail": row.detail,
                }
                for row in self.stages
            ],
            "canary_retry_authorized": False,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
                "guardian_override": False,
            },
        }
        if include_fingerprint:
            payload["diagnostic_fingerprint"] = _fingerprint(payload)
        return payload


def diagnose_ollama_reconstruction(
    *,
    model_tag: str,
    model_digest: str,
    base_url: str = "http://127.0.0.1:11434",
    transport: Transport = _urllib_transport,
    clock: Callable[[], float] = monotonic,
) -> OllamaReconstructionDiagnostic:
    tag = str(model_tag).strip()
    digest = str(model_digest).strip().lower()
    if not tag or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("diagnostic requires exact model tag and SHA-256 digest")
    if base_url.rstrip("/") not in {"http://127.0.0.1:11434", "http://localhost:11434"}:
        raise ValueError("diagnostic Ollama endpoint must be localhost:11434")
    root = base_url.rstrip("/")
    stages: list[OllamaDiagnosticStage] = []

    try:
        response, elapsed = _timed_call(clock, lambda: transport("GET", f"{root}/api/tags", None, 30.0))
        models = response.get("models")
        matches = [
            str(row.get("digest", "")).lower()
            for row in models
            if isinstance(models, list) and isinstance(row, dict) and tag in {str(row.get("name", "")), str(row.get("model", ""))}
        ] if isinstance(models, list) else []
        if matches != [digest]:
            stages.append(OllamaDiagnosticStage("model_identity", "fail", elapsed, "model tag/digest missing or drifted"))
            return OllamaReconstructionDiagnostic(tag, digest, tuple(stages))
        stages.append(OllamaDiagnosticStage("model_identity", "pass", elapsed, "exact model digest present"))
    except Exception as exc:  # diagnostic boundary intentionally records transport faults
        stages.append(OllamaDiagnosticStage("model_identity", "fail", 0.0, _error(exc)))
        return OllamaReconstructionDiagnostic(tag, digest, tuple(stages))

    try:
        response, elapsed = _timed_call(clock, lambda: transport(
            "POST",
            f"{root}/api/chat",
            {
                "model": tag,
                "messages": [{"role": "user", "content": "Reply with exactly OK."}],
                "stream": False,
                "think": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"temperature": 0, "num_ctx": 1024, "num_predict": 8},
            },
            BASIC_CHAT_TIMEOUT,
        ))
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            stages.append(OllamaDiagnosticStage("basic_chat", "fail", elapsed, "chat returned no content"))
            return OllamaReconstructionDiagnostic(tag, digest, tuple(stages))
        stages.append(OllamaDiagnosticStage("basic_chat", "pass", elapsed, "bounded non-thinking chat returned content"))
    except Exception as exc:
        stages.append(OllamaDiagnosticStage("basic_chat", "fail", 0.0, _error(exc)))
        return OllamaReconstructionDiagnostic(tag, digest, tuple(stages))

    schema = {
        "type": "object",
        "properties": {"status": {"type": "string", "enum": ["ok"]}},
        "required": ["status"],
        "additionalProperties": False,
    }
    try:
        response, elapsed = _timed_call(clock, lambda: transport(
            "POST",
            f"{root}/api/chat",
            {
                "model": tag,
                "messages": [{"role": "user", "content": "Return JSON with status set to ok."}],
                "stream": False,
                "think": False,
                "format": schema,
                "keep_alive": KEEP_ALIVE,
                "options": {"temperature": 0, "num_ctx": 1024, "num_predict": 32},
            },
            STRUCTURED_CHAT_TIMEOUT,
        ))
        message = response.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        parsed = json.loads(content) if isinstance(content, str) else None
        if parsed != {"status": "ok"}:
            stages.append(OllamaDiagnosticStage("structured_chat", "fail", elapsed, "structured response did not match exact schema"))
            return OllamaReconstructionDiagnostic(tag, digest, tuple(stages))
        stages.append(OllamaDiagnosticStage("structured_chat", "pass", elapsed, "minimal JSON schema honored"))
    except Exception as exc:
        stages.append(OllamaDiagnosticStage("structured_chat", "fail", 0.0, _error(exc)))

    return OllamaReconstructionDiagnostic(tag, digest, tuple(stages))


def _timed_call(clock: Callable[[], float], fn: Callable[[], dict[str, object]]) -> tuple[dict[str, object], float]:
    start = clock()
    result = fn()
    elapsed = max(0.0, clock() - start)
    if not isinstance(result, dict):
        raise ValueError("Ollama diagnostic response must be an object")
    return result, elapsed


def _error(exc: Exception) -> str:
    return " ".join(f"{type(exc).__name__}:{exc}".split())[:500]


broker_write_authority = False
live_write_authority = False
promotion_authority = False
retry_authority = False
risk_override_authority = False
