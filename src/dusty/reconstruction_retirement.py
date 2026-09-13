from __future__ import annotations

"""Content-addressed retirement evidence for semantically dead reconstructions.

This is narrower than M159/M160 family exhaustion: it records that one exact
reconstruction lineage cannot activate on its bounded training evidence. It does
not declare the underlying trading theory false and grants no Graveyard,
promotion, broker, retry, or trading authority.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Mapping


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: object, label: str) -> str:
    rendered = str(value or "").strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256")
    return rendered


@dataclass(frozen=True, slots=True)
class ReconstructionRetirement:
    strategy_fingerprint: str
    reconstruction_fingerprint: str
    semantic_audit_fingerprint: str
    parent_strategy_fingerprint: str
    parent_reconstruction_fingerprint: str
    reason: str

    def __post_init__(self) -> None:
        for name in (
            "strategy_fingerprint",
            "reconstruction_fingerprint",
            "semantic_audit_fingerprint",
            "parent_strategy_fingerprint",
            "parent_reconstruction_fingerprint",
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        if self.reason != "dead_entry_conjunction_on_bounded_pit_training":
            raise ValueError("unsupported reconstruction retirement reason")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-reconstruction-retirement-v1",
            "strategy_fingerprint": self.strategy_fingerprint,
            "reconstruction_fingerprint": self.reconstruction_fingerprint,
            "semantic_audit_fingerprint": self.semantic_audit_fingerprint,
            "parent_strategy_fingerprint": self.parent_strategy_fingerprint,
            "parent_reconstruction_fingerprint": self.parent_reconstruction_fingerprint,
            "reason": self.reason,
            "scope": "exact_reconstruction_lineage_only",
            "underlying_trading_theory_rejected": False,
            "threshold_tuning_authorized": False,
            "automatic_retry_authorized": False,
            "authority": {
                "broker_write": False,
                "custody_write": False,
                "live_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def retire_dead_reconstruction(
    child_audit: Mapping[str, object],
    child_receipt: Mapping[str, object],
) -> ReconstructionRetirement:
    assessment = child_audit.get("assessment")
    if not isinstance(assessment, Mapping):
        raise ValueError("child semantic audit assessment missing")
    if assessment.get("status") != "dead_reconstruction":
        raise ValueError("only a dead reconstruction may receive this retirement receipt")
    if int(assessment.get("entry_match_count", -1)) != 0:
        raise ValueError("dead reconstruction retirement requires zero entry matches")

    child_strategy = _sha(child_receipt.get("child_strategy_fingerprint"), "child strategy")
    child_reconstruction = _sha(child_receipt.get("child_reconstruction_fingerprint"), "child reconstruction")
    if _sha(child_audit.get("strategy_fingerprint"), "audit strategy") != child_strategy:
        raise ValueError("child audit/receipt strategy identity mismatch")
    if _sha(child_audit.get("reconstruction_fingerprint"), "audit reconstruction") != child_reconstruction:
        raise ValueError("child audit/receipt reconstruction identity mismatch")
    if child_receipt.get("parent_preserved") is not True:
        raise ValueError("retirement requires preserved parent evidence")
    if child_receipt.get("threshold_tuning_performed") is not False:
        raise ValueError("retirement refuses threshold-tuned remediation evidence")

    return ReconstructionRetirement(
        child_strategy,
        child_reconstruction,
        _sha(child_audit.get("audit_fingerprint"), "child semantic audit"),
        _sha(child_receipt.get("parent_strategy_fingerprint"), "parent strategy"),
        _sha(child_receipt.get("parent_reconstruction_fingerprint"), "parent reconstruction"),
        "dead_entry_conjunction_on_bounded_pit_training",
    )


broker_write_authority = False
custody_write_authority = False
live_write_authority = False
promotion_authority = False
retry_authority = False
risk_override_authority = False
