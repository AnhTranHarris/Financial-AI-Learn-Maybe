from __future__ import annotations

"""Provider-neutral structured-output boundary for Dusty research experts.

A provider may be Ollama/Qwen, OpenAI, or another future local/remote model, but
Dusty never trusts transport success as research evidence. The caller requests
one exact model tag/digest; the returned response must bind that same identity
before any school-specific parser sees model text.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Protocol


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _text(value: str, label: str, *, maximum: int) -> str:
    rendered = str(value).strip()
    if not rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty and <= {maximum} characters")
    return rendered


@dataclass(frozen=True, slots=True)
class StructuredResearchResponse:
    model_tag: str
    model_digest: str
    content: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_tag", _text(self.model_tag, "research model_tag", maximum=256))
        object.__setattr__(self, "model_digest", _sha(self.model_digest, "research model_digest"))
        object.__setattr__(self, "content", _text(self.content, "research response content", maximum=256_000))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-structured-research-response-v1",
            self.model_tag,
            self.model_digest,
            sha256(self.content.encode("utf-8")).hexdigest(),
        ))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def tool_authority(self) -> bool:
        return False


class StructuredResearchGenerator(Protocol):
    def generate(
        self,
        *,
        model_tag: str,
        model_digest: str,
        system_message: str,
        prompt_payload: dict[str, object],
        response_schema: dict[str, object],
    ) -> StructuredResearchResponse: ...


def validate_response_identity(
    response: StructuredResearchResponse,
    *,
    model_tag: str,
    model_digest: str,
) -> None:
    if response.model_tag != str(model_tag).strip():
        raise ValueError("research expert response model tag drift")
    if response.model_digest != _sha(model_digest, "requested research model digest"):
        raise ValueError("research expert response model digest drift")


def compact_error(exc: BaseException) -> str:
    rendered = " ".join(f"{type(exc).__name__}:{exc}".strip().split())
    return rendered[:1000] or "research_expert_unavailable"
