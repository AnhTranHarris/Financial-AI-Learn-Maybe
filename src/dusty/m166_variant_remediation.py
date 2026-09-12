from __future__ import annotations

"""Deterministic remediation for unsupported M166 reconstruction hypotheses.

The current use case is deliberately narrow: an Ollama reconstruction supplied a
non-zero ``event_exclusion_minutes`` value even though the archived source contains
no event/news/calendar rule and no unresolved event requirement.  Historical PIT
calendar evidence must not be fabricated merely to satisfy that model-authored
field.  Instead, this module derives a new immutable research reconstruction with
the unsupported event exclusion disabled.

The parent reconstruction is never mutated.  This module owns no broker, live,
custody, promotion, retry, or risk authority.
"""

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Mapping

from .trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)


PROTOCOL = "dusty-m166-event-hypothesis-remediation-v1"
REQUIREMENT_PROTOCOL = "dusty-m166-event-requirement-inspector-v3"
_EVENT_TOKENS = ("event", "news", "calendar", "macro", "release")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: object, label: str) -> str:
    rendered = str(value or "").strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256")
    return rendered


def _event_related(value: str) -> bool:
    rendered = value.casefold()
    return any(token in rendered for token in _EVENT_TOKENS)


def _event_rules(reconstruction: StrategyReconstruction) -> tuple[ReconstructionRule, ...]:
    return tuple(
        rule
        for rule in reconstruction.rules
        if _event_related(rule.name) or _event_related(rule.value)
    )


def _event_source_rules(reconstruction: StrategyReconstruction) -> tuple[ReconstructionRule, ...]:
    return tuple(
        rule
        for rule in _event_rules(reconstruction)
        if rule.basis is ReconstructionRuleBasis.SOURCE_DECLARED
    )


def _event_unresolved_rules(reconstruction: StrategyReconstruction) -> tuple[str, ...]:
    return tuple(value for value in reconstruction.unresolved_source_rules if _event_related(value))


@dataclass(frozen=True, slots=True)
class EventHypothesisVariant:
    parent_reconstruction_fingerprint: str
    parent_strategy_fingerprint: str
    parent_parameter_fingerprint: str
    requirement_fingerprint: str
    original_event_exclusion_minutes: int
    reconstruction: StrategyReconstruction

    def __post_init__(self) -> None:
        for name in (
            "parent_reconstruction_fingerprint",
            "parent_strategy_fingerprint",
            "parent_parameter_fingerprint",
            "requirement_fingerprint",
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        if isinstance(self.original_event_exclusion_minutes, bool) or self.original_event_exclusion_minutes <= 0:
            raise ValueError("original event exclusion must be positive")
        if self.reconstruction.candidate_spec.event_exclusion_minutes != 0:
            raise ValueError("remediated reconstruction must disable event exclusion")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": PROTOCOL,
            "parent_reconstruction_fingerprint": self.parent_reconstruction_fingerprint,
            "parent_strategy_fingerprint": self.parent_strategy_fingerprint,
            "parent_parameter_fingerprint": self.parent_parameter_fingerprint,
            "requirement_fingerprint": self.requirement_fingerprint,
            "original_event_exclusion_minutes": self.original_event_exclusion_minutes,
            "variant_reconstruction_fingerprint": self.reconstruction.fingerprint,
            "variant_strategy_fingerprint": self.reconstruction.candidate_spec.strategy_hash,
            "variant_strategy_id": self.reconstruction.candidate_spec.strategy_id,
            "variant_event_exclusion_minutes": self.reconstruction.candidate_spec.event_exclusion_minutes,
            "reason": "unsupported_ollama_event_exclusion_hypothesis_without_source_event_rule",
            "parent_preserved": True,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def derive_event_hypothesis_variant(
    reconstruction: StrategyReconstruction,
    *,
    parent_parameter_fingerprint: str,
    requirement: Mapping[str, object],
) -> EventHypothesisVariant:
    """Derive a new research reconstruction after proving the event filter is unsupported.

    The decision is intentionally stricter than simply checking ``actor=ollama``.
    The exact inspector result must match the parent identities, label the event
    exclusion as a research hypothesis, and prove there are no source-declared,
    unresolved, or hidden reconstruction event rules.  The reconstruction itself
    is independently scanned so a stale/tampered inspector artifact cannot relax
    the gate.
    """

    if requirement.get("protocol") != REQUIREMENT_PROTOCOL:
        raise ValueError("unsupported event requirement protocol")
    parent_recon = reconstruction.fingerprint
    parent_strategy = reconstruction.candidate_spec.strategy_hash
    parent_parameter = _sha(parent_parameter_fingerprint, "parent parameter fingerprint")
    if _sha(requirement.get("reconstruction_fingerprint"), "requirement reconstruction") != parent_recon:
        raise ValueError("event requirement reconstruction identity mismatch")
    if _sha(requirement.get("strategy_fingerprint"), "requirement strategy") != parent_strategy:
        raise ValueError("event requirement strategy identity mismatch")
    requirement_fp = _sha(requirement.get("requirement_fingerprint"), "event requirement")

    original_minutes = reconstruction.candidate_spec.event_exclusion_minutes
    if original_minutes <= 0:
        raise ValueError("parent reconstruction has no event exclusion to remediate")
    if int(requirement.get("event_exclusion_minutes", -1)) != original_minutes:
        raise ValueError("event requirement minutes differ from parent strategy")
    if requirement.get("event_exclusion_basis") != "research_hypothesis":
        raise PermissionError("event exclusion is not proven to be a research hypothesis")
    if reconstruction.actor is not ReconstructionActor.OLLAMA:
        raise PermissionError("event hypothesis remediation requires an Ollama parent")

    reported_source_rules = requirement.get("source_declared_event_rules")
    reported_unresolved = requirement.get("unresolved_event_rules")
    reported_all = requirement.get("event_related_rules")
    if not isinstance(reported_source_rules, list) or reported_source_rules:
        raise PermissionError("source-declared event rules prevent automatic remediation")
    if not isinstance(reported_unresolved, list) or reported_unresolved:
        raise PermissionError("unresolved event rules prevent automatic remediation")
    if not isinstance(reported_all, list) or reported_all:
        raise PermissionError("event-related reconstruction rules require manual semantic review")

    if _event_source_rules(reconstruction):
        raise PermissionError("parent reconstruction contains source-declared event rules")
    if _event_unresolved_rules(reconstruction):
        raise PermissionError("parent reconstruction contains unresolved event rules")
    if _event_rules(reconstruction):
        # Independent parent scan prevents an incomplete inspector artifact from
        # hiding model-authored event semantics and enabling a destructive rewrite.
        raise PermissionError("parent reconstruction contains event-related rules")

    parent_spec = reconstruction.candidate_spec
    variant_id = f"{parent_spec.strategy_id}-eventless-{parent_recon[:12]}"
    variant_spec = replace(
        parent_spec,
        strategy_id=variant_id,
        event_exclusion_minutes=0,
    )
    if variant_spec.strategy_hash == parent_strategy:
        raise RuntimeError("event remediation failed to change executable strategy identity")

    remediation_rule = ReconstructionRule(
        "remediation.event_exclusion_minutes",
        f"0; parent Ollama hypothesis was {original_minutes} minutes and had no source event rule",
        ReconstructionRuleBasis.RESEARCH_HYPOTHESIS,
    )
    rules = tuple(reconstruction.rules) + (remediation_rule,)
    actor_fingerprint = _digest(
        {
            "protocol": PROTOCOL,
            "parent_reconstruction_fingerprint": parent_recon,
            "parent_strategy_fingerprint": parent_strategy,
            "parent_parameter_fingerprint": parent_parameter,
            "requirement_fingerprint": requirement_fp,
            "original_event_exclusion_minutes": original_minutes,
            "variant_event_exclusion_minutes": 0,
            "variant_strategy_hash": variant_spec.strategy_hash,
        }
    )
    variant = StrategyReconstruction(
        reconstruction.proposal_fingerprint,
        reconstruction.source_id,
        reconstruction.source_url,
        reconstruction.source_content_sha256,
        reconstruction.source_family_fingerprint,
        reconstruction.title,
        reconstruction.symbols,
        reconstruction.timeframe,
        variant_spec,
        rules,
        reconstruction.unresolved_source_rules,
        ReconstructionActor.DUSTY_RESEARCH,
        actor_fingerprint,
        # Preserve the parent hypothesis epoch so repeated materialization of this
        # purely deterministic variant yields one stable content-addressed identity.
        # The materialization timestamp belongs in the external remediation receipt.
        reconstruction.created_at,
        reconstruction.schema_version,
    )
    if variant.fingerprint == parent_recon:
        raise RuntimeError("event remediation failed to change reconstruction identity")
    return EventHypothesisVariant(
        parent_recon,
        parent_strategy,
        parent_parameter,
        requirement_fp,
        original_minutes,
        variant,
    )


broker_write_authority = False
live_write_authority = False
custody_write_authority = False
promotion_authority = False
retry_authority = False
risk_override_authority = False
