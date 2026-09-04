from __future__ import annotations

"""M143-M152 strategy genetics, including Carson-refined user strategy intents.

No natural-language model is trusted to invent missing rules. Inputs arrive as
reviewed structured intents/proposals, and every descendant preserves ancestry.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import itertools
import json
from typing import Iterable, Mapping, Sequence

from .research_brain import MutationAxis
from .source_intake import StrategyProposal, deduplicate_proposals, proposals_from_vibe
from .vibe_research_contract import VibeResearchEvidence


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError(f"{label} requires SHA-256 identity")


class StrategyOrigin(StrEnum):
    USER = "user"
    VIBE = "vibe"
    EXTERNAL = "external"
    DUSTY = "dusty"


class ConstraintMode(StrEnum):
    LOCKED = "locked"
    RESEARCHABLE = "researchable"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True, slots=True)
class StrategyConstraint:
    key: str
    value: str
    mode: ConstraintMode

    def __post_init__(self) -> None:
        if not self.key.strip() or not self.value.strip():
            raise ValueError("strategy constraint key/value required")


PERMANENT_FORBIDDEN = (
    StrategyConstraint("risk.martingale", "prohibited", ConstraintMode.FORBIDDEN),
    StrategyConstraint("risk.revenge_sizing", "prohibited", ConstraintMode.FORBIDDEN),
    StrategyConstraint("risk.stop_widening", "prohibited", ConstraintMode.FORBIDDEN),
    StrategyConstraint("data.future_leakage", "prohibited", ConstraintMode.FORBIDDEN),
    StrategyConstraint("execution.hft", "prohibited", ConstraintMode.FORBIDDEN),
    StrategyConstraint("execution.scalping", "prohibited", ConstraintMode.FORBIDDEN),
)


@dataclass(frozen=True, slots=True)
class UserStrategyIntent:
    intent_id: str
    title: str
    original_text: str
    created_at: datetime
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    constraints: tuple[StrategyConstraint, ...]

    def __post_init__(self) -> None:
        if not self.intent_id.strip() or not self.title.strip() or not self.original_text.strip():
            raise ValueError("user strategy intent identity/text required")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("user strategy intent timestamp must be timezone-aware")
        if not self.symbols:
            raise ValueError("user strategy intent requires a symbol")
        keys = tuple(item.key.lower() for item in self.constraints)
        if len(keys) != len(set(keys)):
            raise ValueError("user strategy constraint keys must be unique")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "intent_id": self.intent_id,
                "title": self.title,
                "original_text": self.original_text,
                "created_at": self.created_at.isoformat(),
                "symbols": tuple(value.upper() for value in self.symbols),
                "timeframes": tuple(value.upper() for value in self.timeframes),
                "constraints": tuple((row.key, row.value, row.mode.value) for row in self.constraints),
            }
        )


@dataclass(frozen=True, slots=True)
class StrategyGenome:
    genome_id: str
    origin: StrategyOrigin
    title: str
    source_fingerprint: str
    parent_fingerprints: tuple[str, ...]
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    components: tuple[str, ...]
    rules: tuple[tuple[str, str], ...]
    unresolved: tuple[str, ...]
    constraints: tuple[StrategyConstraint, ...]
    generation: int = 0
    live_write_authority: bool = False
    promotion_authority: bool = False

    def __post_init__(self) -> None:
        if not self.genome_id.strip() or not self.title.strip():
            raise ValueError("strategy genome identity required")
        _sha(self.source_fingerprint, "strategy genome source")
        if any(len(value) != 64 for value in self.parent_fingerprints):
            raise ValueError("strategy genome parents require SHA-256 identity")
        if self.generation < 0:
            raise ValueError("strategy generation cannot be negative")
        rule_keys = tuple(name.lower() for name, _ in self.rules)
        constraint_keys = tuple(row.key.lower() for row in self.constraints)
        if len(rule_keys) != len(set(rule_keys)) or len(constraint_keys) != len(set(constraint_keys)):
            raise ValueError("strategy genome rules/constraints must be unique")
        if self.live_write_authority or self.promotion_authority:
            raise ValueError("strategy genome cannot receive operational authority")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "genome_id": self.genome_id,
                "origin": self.origin.value,
                "title": self.title,
                "source": self.source_fingerprint,
                "parents": self.parent_fingerprints,
                "symbols": tuple(sorted(value.upper() for value in self.symbols)),
                "timeframes": tuple(sorted(value.upper() for value in self.timeframes)),
                "components": tuple(sorted(value.lower() for value in self.components)),
                "rules": tuple(sorted((key.lower(), value.lower()) for key, value in self.rules)),
                "unresolved": tuple(sorted(value.lower() for value in self.unresolved)),
                "constraints": tuple(sorted((row.key.lower(), row.value.lower(), row.mode.value) for row in self.constraints)),
                "generation": self.generation,
                "live_write_authority": self.live_write_authority,
                "promotion_authority": self.promotion_authority,
            }
        )

    @property
    def family_fingerprint(self) -> str:
        """Novelty identity excludes title/source marketing and generation metadata."""

        return _digest(
            {
                "symbols": tuple(sorted(value.upper() for value in self.symbols)),
                "timeframes": tuple(sorted(value.upper() for value in self.timeframes)),
                "components": tuple(sorted(value.lower() for value in self.components)),
                "rules": tuple(sorted((key.lower(), value.lower()) for key, value in self.rules)),
                "constraints": tuple(sorted((row.key.lower(), row.value.lower(), row.mode.value) for row in self.constraints)),
            }
        )

    def constraint_map(self) -> dict[str, StrategyConstraint]:
        return {row.key: row for row in self.constraints}

    def rule_map(self) -> dict[str, str]:
        return dict(self.rules)


# M143 -----------------------------------------------------------------------

def vibe_strategy_factory(evidences: Iterable[VibeResearchEvidence]) -> tuple[StrategyGenome, ...]:
    proposals: list[StrategyProposal] = []
    for evidence in evidences:
        proposals.extend(proposals_from_vibe(evidence))
    return tuple(genome_from_proposal(row, origin=StrategyOrigin.VIBE) for row in deduplicate_proposals(proposals))


# M144 -----------------------------------------------------------------------

def genome_from_proposal(proposal: StrategyProposal, *, origin: StrategyOrigin = StrategyOrigin.EXTERNAL) -> StrategyGenome:
    constraints = tuple(
        StrategyConstraint(f"unresolved.{name}", "research_required", ConstraintMode.RESEARCHABLE)
        for name in proposal.unresolved
    ) + PERMANENT_FORBIDDEN
    return StrategyGenome(
        genome_id=f"proposal:{proposal.proposal_id}",
        origin=origin,
        title=proposal.title,
        source_fingerprint=proposal.fingerprint,
        parent_fingerprints=(),
        symbols=proposal.symbols,
        timeframes=proposal.timeframes,
        components=proposal.components,
        rules=proposal.declared_rules,
        unresolved=proposal.unresolved,
        constraints=_dedupe_constraints(constraints),
        generation=0,
    )


def external_strategy_genomes(proposals: Iterable[StrategyProposal]) -> tuple[StrategyGenome, ...]:
    return tuple(genome_from_proposal(row) for row in deduplicate_proposals(proposals))


# M145-M147 ------------------------------------------------------------------

def compile_user_strategy_intent(intent: UserStrategyIntent) -> StrategyGenome:
    """Compile a Carson-refined intent; this function performs no NLP guessing."""

    constraints = _dedupe_constraints(intent.constraints + PERMANENT_FORBIDDEN)
    locked_rules = tuple(
        (row.key, row.value)
        for row in constraints
        if row.mode is ConstraintMode.LOCKED
    )
    unresolved = tuple(
        row.key for row in constraints if row.mode is ConstraintMode.RESEARCHABLE
    )
    components = tuple(sorted({row.key.split(".", 1)[0] for row in constraints if row.mode is not ConstraintMode.FORBIDDEN}))
    return StrategyGenome(
        genome_id=f"user:{intent.intent_id}",
        origin=StrategyOrigin.USER,
        title=intent.title,
        source_fingerprint=intent.fingerprint,
        parent_fingerprints=(),
        symbols=tuple(value.upper() for value in intent.symbols),
        timeframes=tuple(value.upper() for value in intent.timeframes),
        components=components,
        rules=locked_rules,
        unresolved=unresolved,
        constraints=constraints,
        generation=0,
    )


@dataclass(frozen=True, slots=True)
class StrategyExperimentVariant:
    parent_fingerprint: str
    changes: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        _sha(self.parent_fingerprint, "experiment parent")
        if not 1 <= len(self.changes) <= 2:
            raise ValueError("strategy experiment must change one or two variables")
        if len({key for key, _ in self.changes}) != len(self.changes):
            raise ValueError("strategy experiment change keys must be unique")

    @property
    def fingerprint(self) -> str:
        return _digest({"parent": self.parent_fingerprint, "changes": self.changes})


def resolve_strategy_experiments(
    genome: StrategyGenome,
    alternatives: Mapping[str, Sequence[str]],
    *,
    maximum_variants: int = 32,
) -> tuple[StrategyExperimentVariant, ...]:
    """Generate bounded 1-2 variable tests only for RESEARCHABLE constraints."""

    if maximum_variants < 1:
        raise ValueError("maximum variants must be positive")
    constraints = genome.constraint_map()
    normalized: dict[str, tuple[str, ...]] = {}
    for key, values in alternatives.items():
        if key not in constraints:
            raise ValueError(f"experiment variable not declared: {key}")
        if constraints[key].mode is not ConstraintMode.RESEARCHABLE:
            raise PermissionError(f"experiment cannot alter {constraints[key].mode.value} variable: {key}")
        clean = tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))
        if not clean:
            raise ValueError(f"experiment alternatives missing for {key}")
        normalized[key] = clean

    variants: list[StrategyExperimentVariant] = []
    for key in sorted(normalized):
        for value in normalized[key]:
            variants.append(StrategyExperimentVariant(genome.fingerprint, ((key, value),)))
            if len(variants) >= maximum_variants:
                return tuple(variants)
    keys = sorted(normalized)
    for left, right in itertools.combinations(keys, 2):
        for left_value, right_value in itertools.product(normalized[left], normalized[right]):
            variants.append(
                StrategyExperimentVariant(
                    genome.fingerprint,
                    ((left, left_value), (right, right_value)),
                )
            )
            if len(variants) >= maximum_variants:
                return tuple(variants)
    return tuple(variants)


# M148 -----------------------------------------------------------------------

def compose_in_house_strategy(
    parent: StrategyGenome,
    *,
    genome_id: str,
    hypothesis: str,
    changes: Mapping[str, str],
    lesson_fingerprints: Iterable[str] = (),
) -> StrategyGenome:
    if not genome_id.strip() or not hypothesis.strip():
        raise ValueError("in-house strategy identity/hypothesis required")
    if not 1 <= len(changes) <= 2:
        raise ValueError("in-house strategy must make one or two controlled changes")
    constraints = parent.constraint_map()
    rules = parent.rule_map()
    unresolved = set(parent.unresolved)
    for key, value in sorted(changes.items()):
        if not str(value).strip():
            raise ValueError("in-house change value cannot be empty")
        constraint = constraints.get(key)
        if constraint is not None and constraint.mode is not ConstraintMode.RESEARCHABLE:
            raise PermissionError(f"in-house strategy cannot alter {constraint.mode.value} variable: {key}")
        if constraint is None:
            raise ValueError(f"in-house strategy change not declared researchable: {key}")
        rules[key] = str(value)
        unresolved.discard(key)
    lessons = tuple(sorted(set(lesson_fingerprints)))
    for value in lessons:
        _sha(value, "strategy lesson")
    source = _digest(
        {
            "parent": parent.fingerprint,
            "hypothesis": hypothesis,
            "changes": tuple(sorted(changes.items())),
            "lessons": lessons,
        }
    )
    return StrategyGenome(
        genome_id=genome_id,
        origin=StrategyOrigin.DUSTY,
        title=f"{parent.title} — {hypothesis}",
        source_fingerprint=source,
        parent_fingerprints=(parent.fingerprint, *lessons),
        symbols=parent.symbols,
        timeframes=parent.timeframes,
        components=parent.components,
        rules=tuple(sorted(rules.items())),
        unresolved=tuple(sorted(unresolved)),
        constraints=parent.constraints,
        generation=parent.generation + 1,
    )


# M152 -----------------------------------------------------------------------

class FailureMechanism(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    REGIME = "regime"
    FORECAST = "forecast"
    COST = "cost"
    RISK = "risk"
    UNKNOWN = "unknown"


_AXIS_FOR_MECHANISM: Mapping[FailureMechanism, MutationAxis] = {
    FailureMechanism.ENTRY: MutationAxis.ENTRY,
    FailureMechanism.EXIT: MutationAxis.EXIT,
    FailureMechanism.REGIME: MutationAxis.REGIME,
    FailureMechanism.FORECAST: MutationAxis.ABSTENTION,
    FailureMechanism.COST: MutationAxis.ENTRY,
    FailureMechanism.RISK: MutationAxis.ABSTENTION,
    FailureMechanism.UNKNOWN: MutationAxis.ABSTENTION,
}


@dataclass(frozen=True, slots=True)
class FailureDiagnosis:
    subject_fingerprint: str
    mechanism: FailureMechanism
    lesson: str
    evidence_fingerprints: tuple[str, ...]
    research_variable: str
    candidate_values: tuple[str, ...]
    causal_proof: bool = False

    def __post_init__(self) -> None:
        _sha(self.subject_fingerprint, "failure subject")
        if any(len(value) != 64 for value in self.evidence_fingerprints):
            raise ValueError("failure evidence requires SHA-256 identity")
        if not self.lesson.strip() or not self.research_variable.strip():
            raise ValueError("failure diagnosis lesson/research variable required")
        if not self.candidate_values:
            raise ValueError("failure diagnosis requires bounded candidate values")

    @property
    def mutation_axis(self) -> MutationAxis:
        return _AXIS_FOR_MECHANISM[self.mechanism]


@dataclass(frozen=True, slots=True)
class FailureRedesignPlan:
    diagnosis: FailureDiagnosis
    variants: tuple[StrategyExperimentVariant, ...]
    champion_modified: bool = False

    def __post_init__(self) -> None:
        if self.champion_modified:
            raise ValueError("failure redesign cannot rewrite champion")
        if not self.variants:
            raise ValueError("failure redesign requires challengers")


def redesign_from_failure(
    genome: StrategyGenome,
    diagnosis: FailureDiagnosis,
    *,
    maximum_variants: int = 5,
) -> FailureRedesignPlan:
    if diagnosis.subject_fingerprint != genome.fingerprint:
        raise ValueError("failure diagnosis does not bind to strategy")
    variants = resolve_strategy_experiments(
        genome,
        {diagnosis.research_variable: diagnosis.candidate_values},
        maximum_variants=maximum_variants,
    )
    return FailureRedesignPlan(diagnosis, variants)


def _dedupe_constraints(rows: Iterable[StrategyConstraint]) -> tuple[StrategyConstraint, ...]:
    selected: dict[str, StrategyConstraint] = {}
    for row in rows:
        current = selected.get(row.key)
        if current is None:
            selected[row.key] = row
            continue
        rank = {ConstraintMode.RESEARCHABLE: 0, ConstraintMode.LOCKED: 1, ConstraintMode.FORBIDDEN: 2}
        if current.value != row.value and rank[current.mode] == rank[row.mode]:
            raise ValueError(f"conflicting strategy constraint: {row.key}")
        if rank[row.mode] > rank[current.mode]:
            selected[row.key] = row
    return tuple(sorted(selected.values(), key=lambda row: row.key))
