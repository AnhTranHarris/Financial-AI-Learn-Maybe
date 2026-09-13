from __future__ import annotations

"""Fail-closed remediation for empirically dead reconstructed entry clauses.

The remediation never tunes thresholds and never claims to recover source intent.
It may only remove research-hypothesis clauses that a bounded PIT semantic audit
proved impossible. The parent reconstruction remains immutable and the child is
new research evidence with no trading or promotion authority.
"""

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from typing import Iterable

from .reconstruction_semantics import ReconstructionSemanticAssessment
from .strategy_ir import RuleGroup, StrategySpecV2
from .trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SemanticRemediationReceipt:
    parent_reconstruction_fingerprint: str
    parent_strategy_fingerprint: str
    audit_fingerprint: str
    removed_clause_coordinates: tuple[tuple[int, int], ...]
    child_strategy_fingerprint: str
    reason: str

    @property
    def fingerprint(self) -> str:
        return _digest({
            "protocol": "dusty-m166-semantic-remediation-v1",
            "parent_reconstruction_fingerprint": self.parent_reconstruction_fingerprint,
            "parent_strategy_fingerprint": self.parent_strategy_fingerprint,
            "audit_fingerprint": self.audit_fingerprint,
            "removed_clause_coordinates": self.removed_clause_coordinates,
            "child_strategy_fingerprint": self.child_strategy_fingerprint,
            "reason": self.reason,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        })


def build_semantic_child(
    parent: StrategyReconstruction,
    assessment: ReconstructionSemanticAssessment,
    *,
    audit_fingerprint: str,
) -> tuple[StrategySpecV2, tuple[ReconstructionRule, ...], SemanticRemediationReceipt]:
    """Remove only dead research-hypothesis clauses proven by the supplied audit."""

    if assessment.status != "dead_reconstruction":
        raise ValueError("semantic remediation requires dead_reconstruction evidence")

    dead = tuple(
        (row.group_index, row.clause_index)
        for row in assessment.clauses
        if row.dead
    )
    if not dead:
        raise ValueError("semantic remediation requires at least one empirically dead clause")

    hypothesis_by_coordinate: dict[tuple[int, int], ReconstructionRule] = {}
    for rule in parent.rules:
        if rule.basis is not ReconstructionRuleBasis.RESEARCH_HYPOTHESIS:
            continue
        parts = rule.name.split(".")
        if len(parts) == 4 and parts[:2] == ["hypothesis", "entry"]:
            try:
                coordinate = (int(parts[2]), int(parts[3]))
            except ValueError:
                continue
            hypothesis_by_coordinate[coordinate] = rule

    missing = tuple(coord for coord in dead if coord not in hypothesis_by_coordinate)
    if missing:
        raise ValueError("dead clause lacks exact research-hypothesis provenance")

    groups: list[RuleGroup] = []
    for group_index, group in enumerate(parent.candidate_spec.entry_groups):
        kept = tuple(
            clause for clause_index, clause in enumerate(group.clauses)
            if (group_index, clause_index) not in dead
        )
        if kept:
            groups.append(RuleGroup(kept, group.mode))
    if not groups:
        raise ValueError("semantic remediation cannot remove every entry clause")

    child_id = parent.candidate_spec.strategy_id + "-semantic-" + audit_fingerprint[:12]
    child = replace(
        parent.candidate_spec,
        strategy_id=child_id,
        entry_groups=tuple(groups),
    )

    removed_names = {hypothesis_by_coordinate[coord].name for coord in dead}
    retained_rules = tuple(rule for rule in parent.rules if rule.name not in removed_names)
    remediation_rules = tuple(
        ReconstructionRule(
            f"remediation.semantic_dead_clause.{group_index}.{clause_index}",
            "removed only because bounded PIT audit proved this research hypothesis never activated",
            ReconstructionRuleBasis.RESEARCH_HYPOTHESIS,
        )
        for group_index, clause_index in dead
    )
    rules = (*retained_rules, *remediation_rules)

    receipt = SemanticRemediationReceipt(
        parent.fingerprint,
        parent.candidate_spec.strategy_hash,
        audit_fingerprint,
        dead,
        child.strategy_hash,
        "empirically_dead_research_hypothesis_clause",
    )
    return child, rules, receipt


broker_write_authority = False
live_write_authority = False
promotion_authority = False
retry_authority = False
risk_override_authority = False
