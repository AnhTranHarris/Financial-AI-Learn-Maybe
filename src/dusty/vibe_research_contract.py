from __future__ import annotations

"""Immutable contract for Dusty's external Vibe-Trading research contractor."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Mapping


PROTOCOL = "dusty-vibe-research-v1"
PROVIDER_ID = "vibe-trading"
EXPECTED_VIBE_VERSION = "0.1.14"

# Deliberately narrow. These are research/read-only capabilities from Vibe's
# public MCP surface. No broker connector, order, shell, file-write, or live
# trading tools are admitted here.
ALLOWED_TOOLS = frozenset(
    {
        "alpha_zoo",
        "list_strategies",
        "query_strategies",
        "get_strategy_evidence",
        "get_market_data",
        "technical_indicators",
        "pattern_recognition",
        "factor_analysis",
        "backtest",
        "web_search",
        "read_url",
    }
)

# File-bearing arguments must stay inside the contractor work root. Dusty owns
# file creation there; Vibe is never given arbitrary filesystem authority.
PATH_ARGUMENTS: Mapping[str, tuple[str, ...]] = {
    "pattern_recognition": ("run_dir",),
    "factor_analysis": ("factor_csv", "return_csv", "output_dir"),
    "backtest": ("run_dir",),
}


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value.lower())


class VibeResearchStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class VibeResearchEvidence:
    protocol: str
    provider_id: str
    tool: str
    vibe_version: str
    surface_sha256: str
    request_sha256: str
    response_sha256: str
    result_text: str
    broker_write_authority: bool = False
    promotion_authority: bool = False
    entry_veto_authority: bool = False

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL or self.provider_id != PROVIDER_ID:
            raise ValueError("vibe_research_identity_mismatch")
        if self.tool not in ALLOWED_TOOLS:
            raise ValueError("vibe_research_tool_not_allowlisted")
        if self.vibe_version != EXPECTED_VIBE_VERSION:
            raise ValueError("vibe_research_version_mismatch")
        if not self.result_text.strip():
            raise ValueError("vibe_research_result_empty")
        for digest in (self.surface_sha256, self.request_sha256, self.response_sha256):
            if not _is_sha256(digest):
                raise ValueError("vibe_research_requires_sha256_identity")
        if self.broker_write_authority or self.promotion_authority or self.entry_veto_authority:
            raise ValueError("vibe_research_cannot_receive_operational_authority")

    @property
    def fingerprint(self) -> str:
        return sha256_text(
            canonical_json(
                {
                    "protocol": self.protocol,
                    "provider_id": self.provider_id,
                    "tool": self.tool,
                    "vibe_version": self.vibe_version,
                    "surface_sha256": self.surface_sha256,
                    "request_sha256": self.request_sha256,
                    "response_sha256": self.response_sha256,
                    "result_text": self.result_text,
                    "broker_write_authority": self.broker_write_authority,
                    "promotion_authority": self.promotion_authority,
                    "entry_veto_authority": self.entry_veto_authority,
                }
            )
        )


@dataclass(frozen=True, slots=True)
class VibeResearchResult:
    status: VibeResearchStatus
    evidence: VibeResearchEvidence | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if self.status is VibeResearchStatus.AVAILABLE:
            if self.evidence is None or self.error:
                raise ValueError("available_vibe_research_requires_evidence_only")
        elif self.evidence is not None or not self.error:
            raise ValueError("unavailable_vibe_research_requires_error_only")

    @property
    def available(self) -> bool:
        return self.status is VibeResearchStatus.AVAILABLE


def build_request(tool: str, arguments: Mapping[str, object]) -> dict[str, object]:
    if tool not in ALLOWED_TOOLS:
        raise ValueError("vibe_research_tool_not_allowlisted")
    if any(not isinstance(key, str) or not key.strip() for key in arguments):
        raise ValueError("vibe_research_argument_keys_invalid")
    # Ensure arguments are canonical-JSON serializable before any subprocess is
    # launched. This also rejects NaN/Infinity via allow_nan=False.
    canonical_json(dict(arguments))
    return {
        "protocol": PROTOCOL,
        "provider_id": PROVIDER_ID,
        "vibe_version": EXPECTED_VIBE_VERSION,
        "tool": tool,
        "arguments": dict(arguments),
    }
