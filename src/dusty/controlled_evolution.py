from __future__ import annotations

"""M158 controlled evolution and failure-directed Challenger creation.

Research failure may create bounded descendants. Infrastructure failure may not.
The module reuses the existing M143-M152 genetics and M157 compiler rather than
creating a second strategy mutation system.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Iterable

from .feature_registry import FeatureRegistry
from .strategy_genome_v2 import (
    ClauseResolution,
    CompiledStrategyGenomeV2,
    GenomeClauseSpec,
    compile_strategy_genome_v2,
)
from .strategy_lab import (
    ConstraintMode,
    FailureDiagnosis,
    StrategyConstraint,
    StrategyGenome,
    compose_in_house_strategy,
)


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _text(value: str, label: str) -> str:
    rendered = str(value).strip()
    if not rendered:
        raise ValueError(f"{label} required")
    return rendered


class ExperimentOutcomeType(StrEnum):
    PASSED = "passed"
    RESEARCH_FAILED = "research_failed"
    INFRASTRUCTURE_FAILED = "infrastructure_failed"


class EvolutionAction(StrEnum):
    ADVANCE = "advance"
    RETRY_EXACT = "retry_exact"
    CREATE_CHALLENGER = "create_challenger"
    STOP_RESEARCH = "stop_research"


class InfrastructureFailureKind(StrEnum):
    PROVIDER = "provider"
    MT5 = "mt5"
    DATA = "data"
    STORAGE = "storage"
    RESOURCE = "resource"
    PROCESS = "process"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    subject_fingerprint: str
    outcome: ExperimentOutcomeType
    reason: str
    evidence_fingerprints: tuple[str, ...] = ()
    infrastructure_kind: InfrastructureFailureKind | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject_fingerprint", _sha(self.subject_fingerprint, "outcome subject"))
        object.__setattr__(self, "reason", _text(self.reason, "outcome reason"))
        evidence = tuple(sorted({_sha(value, "outcome evidence") for value in self.evidence_fingerprints}))
        object.__setattr__(self, "evidence_fingerprints", evidence)
        if self.outcome is ExperimentOutcomeType.INFRASTRUCTURE_FAILED:
            if self.infrastructure_kind is None:
                raise ValueError("infrastructure failure requires failure kind")
        elif self.infrastructure_kind is not None:
            raise ValueError("infrastructure kind is only valid for infrastructure failure")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m158-outcome-v1",
                "subject": self.subject_fingerprint,
                "outcome": self.outcome.value,
                "reason": self.reason,
                "evidence": self.evidence_fingerprints,
                "infrastructure_kind": None if self.infrastructure_kind is None else self.infrastructure_kind.value,
            }
        )


@dataclass(frozen=True, slots=True)
class FeatureReplacement:
    clause_id: str
    from_feature: str
    to_feature: str
    mutation_family: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", _text(self.clause_id, "feature replacement clause"))
        object.__setattr__(self, "from_feature", _text(self.from_feature, "feature replacement source").lower())
        object.__setattr__(self, "to_feature", _text(self.to_feature, "feature replacement target").lower())
        object.__setattr__(self, "mutation_family", _text(self.mutation_family, "feature mutation family").lower())


@dataclass(frozen=True, slots=True)
class MutationInstruction:
    source_key: str
    new_value: str
    rationale: str
    feature_replacement: FeatureReplacement | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_key", _text(self.source_key, "mutation source key").lower())
        object.__setattr__(self, "new_value", _text(self.new_value, "mutation value"))
        object.__setattr__(self, "rationale", _text(self.rationale, "mutation rationale"))

    @property
    def payload(self) -> dict[str, object]:
        replacement = self.feature_replacement
        return {
            "source_key": self.source_key,
            "new_value": self.new_value,
            "rationale": self.rationale,
            "feature_replacement": None
            if replacement is None
            else {
                "clause_id": replacement.clause_id,
                "from_feature": replacement.from_feature,
                "to_feature": replacement.to_feature,
                "mutation_family": replacement.mutation_family,
            },
        }


@dataclass(frozen=True, slots=True)
class ChallengerCandidate:
    parent_genome_fingerprint: str
    outcome_fingerprint: str
    mutation_fingerprint: str
    source_genome: StrategyGenome
    compiled_genome: CompiledStrategyGenomeV2
    instructions: tuple[MutationInstruction, ...]

    def __post_init__(self) -> None:
        _sha(self.parent_genome_fingerprint, "challenger parent")
        _sha(self.outcome_fingerprint, "challenger outcome")
        _sha(self.mutation_fingerprint, "challenger mutation")
        if not 1 <= len(self.instructions) <= 2:
            raise ValueError("challenger must contain one or two meaningful mutations")
        if self.source_genome.generation < 1:
            raise ValueError("challenger must be a descendant generation")
        if self.parent_genome_fingerprint not in self.source_genome.parent_fingerprints:
            raise ValueError("challenger lineage must preserve parent fingerprint")
        if self.compiled_genome.source_genome_fingerprint != self.source_genome.fingerprint:
            raise ValueError("compiled challenger must bind exact child genome")
        if (
            self.compiled_genome.broker_write_authority
            or self.compiled_genome.risk_override_authority
            or self.compiled_genome.promotion_authority
        ):
            raise ValueError("challenger cannot acquire operational authority")


@dataclass(frozen=True, slots=True)
class EvolutionDecision:
    action: EvolutionAction
    subject_fingerprint: str
    outcome_fingerprint: str
    reason: str
    exact_retry_execution_fingerprint: str | None = None
    challengers: tuple[ChallengerCandidate, ...] = ()

    def __post_init__(self) -> None:
        _sha(self.subject_fingerprint, "evolution subject")
        _sha(self.outcome_fingerprint, "evolution outcome")
        _text(self.reason, "evolution reason")
        if self.action is EvolutionAction.RETRY_EXACT:
            if self.exact_retry_execution_fingerprint is None:
                raise ValueError("exact retry requires execution fingerprint")
            _sha(self.exact_retry_execution_fingerprint, "retry execution")
            if self.challengers:
                raise ValueError("infrastructure retry cannot create challengers")
        elif self.exact_retry_execution_fingerprint is not None:
            raise ValueError("retry fingerprint only valid for exact retry")
        if self.action is EvolutionAction.CREATE_CHALLENGER and not self.challengers:
            raise ValueError("challenger action requires descendants")
        if self.action is not EvolutionAction.CREATE_CHALLENGER and self.challengers:
            raise ValueError("only challenger action may carry descendants")


def _specs_from_compiled(compiled: CompiledStrategyGenomeV2) -> tuple[GenomeClauseSpec, ...]:
    return tuple(
        GenomeClauseSpec(
            clause_id=row.clause_id,
            kind=row.kind,
            source_key=row.source_key,
            resolution=row.resolution,
            value=row.value,
            feature_keys=tuple(f"{feature.name}@{feature.version}" for feature in row.features),
            parameters=row.parameters,
        )
        for row in compiled.clauses
    )


def _constraint_for(parent: StrategyGenome, source_key: str) -> StrategyConstraint:
    constraints = {row.key.lower(): row for row in parent.constraints}
    key = source_key.lower()
    candidates = (key, f"unresolved.{key}") if not key.startswith("unresolved.") else (
        key,
        key.removeprefix("unresolved."),
    )
    for candidate in candidates:
        if candidate in constraints:
            return constraints[candidate]
    raise ValueError(f"mutation source is not declared by parent: {source_key}")


def _validate_feature_replacement(
    replacement: FeatureReplacement,
    *,
    specs: tuple[GenomeClauseSpec, ...],
    registry: FeatureRegistry,
) -> None:
    matches = [row for row in specs if row.clause_id == replacement.clause_id]
    if len(matches) != 1:
        raise ValueError(f"feature replacement clause not found: {replacement.clause_id}")
    spec = matches[0]
    if replacement.from_feature not in tuple(value.lower() for value in spec.feature_keys):
        raise ValueError(f"feature replacement source not bound to clause: {replacement.from_feature}")
    source_definition = registry.get(replacement.from_feature)
    registry.get(replacement.to_feature)  # target must already be a registered M156 feature
    if replacement.mutation_family not in source_definition.compatible_mutations:
        raise PermissionError(
            f"feature {source_definition.key} does not allow mutation family {replacement.mutation_family}"
        )


def _apply_instruction_to_specs(
    specs: tuple[GenomeClauseSpec, ...],
    instruction: MutationInstruction,
    registry: FeatureRegistry,
) -> tuple[GenomeClauseSpec, ...]:
    replacement = instruction.feature_replacement
    if replacement is not None:
        _validate_feature_replacement(replacement, specs=specs, registry=registry)
    updated: list[GenomeClauseSpec] = []
    matched_source = False
    for spec in specs:
        source_matches = spec.source_key.lower() == instruction.source_key
        if not source_matches and spec.source_key.lower().startswith("unresolved."):
            source_matches = spec.source_key.lower().removeprefix("unresolved.") == instruction.source_key
        if source_matches:
            matched_source = True
            spec = GenomeClauseSpec(
                clause_id=spec.clause_id,
                kind=spec.kind,
                source_key=spec.source_key,
                resolution=ClauseResolution.RESOLVED,
                value=instruction.new_value,
                feature_keys=spec.feature_keys,
                parameters=spec.parameters,
            )
        if replacement is not None and spec.clause_id == replacement.clause_id:
            feature_keys = tuple(
                replacement.to_feature if key.lower() == replacement.from_feature else key
                for key in spec.feature_keys
            )
            spec = GenomeClauseSpec(
                clause_id=spec.clause_id,
                kind=spec.kind,
                source_key=spec.source_key,
                resolution=spec.resolution,
                value=spec.value,
                feature_keys=feature_keys,
                parameters=spec.parameters,
            )
        updated.append(spec)
    if not matched_source:
        raise ValueError(f"mutation source is not represented by compiled strategy: {instruction.source_key}")
    return tuple(updated)


def create_challenger(
    parent: StrategyGenome,
    compiled_parent: CompiledStrategyGenomeV2,
    registry: FeatureRegistry,
    outcome: ExperimentOutcome,
    instructions: Iterable[MutationInstruction],
) -> ChallengerCandidate:
    """Materialize one bounded descendant while preserving the parent unchanged."""

    if outcome.outcome is not ExperimentOutcomeType.RESEARCH_FAILED:
        raise ValueError("challengers may only be created from research failure")
    if outcome.subject_fingerprint != parent.fingerprint:
        raise ValueError("outcome does not belong to parent genome")
    if compiled_parent.source_genome_fingerprint != parent.fingerprint:
        raise ValueError("compiled parent does not bind parent genome")
    if not registry.frozen:
        raise ValueError("feature registry must be frozen before evolution")

    rows = tuple(instructions)
    if not 1 <= len(rows) <= 2:
        raise ValueError("controlled evolution permits one or two mutations")
    keys = tuple(row.source_key for row in rows)
    if len(keys) != len(set(keys)):
        raise ValueError("controlled evolution mutation source keys must be unique")

    changes: dict[str, str] = {}
    specs = _specs_from_compiled(compiled_parent)
    for row in rows:
        constraint = _constraint_for(parent, row.source_key)
        if constraint.mode is not ConstraintMode.RESEARCHABLE:
            raise PermissionError(
                f"controlled evolution cannot alter {constraint.mode.value} variable: {constraint.key}"
            )
        changes[constraint.key] = row.new_value
        specs = _apply_instruction_to_specs(specs, row, registry)

    mutation_fingerprint = _digest(tuple(row.payload for row in rows))
    hypothesis = " | ".join(row.rationale for row in rows)
    child = compose_in_house_strategy(
        parent,
        genome_id=f"m158:{parent.fingerprint[:12]}:{mutation_fingerprint[:12]}",
        hypothesis=hypothesis,
        changes=changes,
        lesson_fingerprints=outcome.evidence_fingerprints,
    )
    compiled_child = compile_strategy_genome_v2(child, specs, registry)
    if child.generation != parent.generation + 1:
        raise ValueError("controlled evolution must advance exactly one generation")
    return ChallengerCandidate(
        parent_genome_fingerprint=parent.fingerprint,
        outcome_fingerprint=outcome.fingerprint,
        mutation_fingerprint=mutation_fingerprint,
        source_genome=child,
        compiled_genome=compiled_child,
        instructions=rows,
    )


def instructions_from_failure(diagnosis: FailureDiagnosis) -> tuple[tuple[MutationInstruction, ...], ...]:
    """Convert an existing bounded M152 diagnosis into one-change candidate instructions."""

    return tuple(
        (
            MutationInstruction(
                source_key=diagnosis.research_variable,
                new_value=value,
                rationale=diagnosis.lesson,
            ),
        )
        for value in diagnosis.candidate_values
    )


def decide_evolution(
    parent: StrategyGenome,
    compiled_parent: CompiledStrategyGenomeV2,
    registry: FeatureRegistry,
    outcome: ExperimentOutcome,
    *,
    diagnosis: FailureDiagnosis | None = None,
    candidate_instructions: Iterable[Iterable[MutationInstruction]] = (),
    maximum_challengers: int = 5,
) -> EvolutionDecision:
    """Failure-directed governor slice for M158.

    Exhaustion and family-level novelty are deliberately deferred to M159/M160.
    This function only answers whether this result advances, retries identically,
    creates bounded children, or has no defensible M158 mutation available.
    """

    if maximum_challengers < 1:
        raise ValueError("maximum challengers must be positive")
    if outcome.subject_fingerprint != parent.fingerprint:
        raise ValueError("experiment outcome does not belong to parent")
    if compiled_parent.source_genome_fingerprint != parent.fingerprint:
        raise ValueError("compiled strategy does not belong to parent")

    if outcome.outcome is ExperimentOutcomeType.PASSED:
        return EvolutionDecision(
            EvolutionAction.ADVANCE,
            parent.fingerprint,
            outcome.fingerprint,
            outcome.reason,
        )

    if outcome.outcome is ExperimentOutcomeType.INFRASTRUCTURE_FAILED:
        return EvolutionDecision(
            EvolutionAction.RETRY_EXACT,
            parent.fingerprint,
            outcome.fingerprint,
            outcome.reason,
            exact_retry_execution_fingerprint=compiled_parent.execution_fingerprint,
        )

    groups = tuple(tuple(group) for group in candidate_instructions)
    if diagnosis is not None:
        if diagnosis.subject_fingerprint != parent.fingerprint:
            raise ValueError("failure diagnosis does not belong to parent")
        if not groups:
            groups = instructions_from_failure(diagnosis)

    if not groups:
        return EvolutionDecision(
            EvolutionAction.STOP_RESEARCH,
            parent.fingerprint,
            outcome.fingerprint,
            "research failure has no bounded defensible mutation",
        )

    challengers: list[ChallengerCandidate] = []
    seen_mutations: set[str] = set()
    for group in groups:
        challenger = create_challenger(parent, compiled_parent, registry, outcome, group)
        if challenger.mutation_fingerprint in seen_mutations:
            continue
        seen_mutations.add(challenger.mutation_fingerprint)
        challengers.append(challenger)
        if len(challengers) >= maximum_challengers:
            break

    if not challengers:
        return EvolutionDecision(
            EvolutionAction.STOP_RESEARCH,
            parent.fingerprint,
            outcome.fingerprint,
            "research failure produced no novel bounded challenger",
        )
    return EvolutionDecision(
        EvolutionAction.CREATE_CHALLENGER,
        parent.fingerprint,
        outcome.fingerprint,
        outcome.reason,
        challengers=tuple(challengers),
    )
