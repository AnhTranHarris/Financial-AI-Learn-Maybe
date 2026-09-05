from __future__ import annotations

"""M159 strategy-family, novelty, lineage, and exhaustion intelligence.

M159 decides whether research is genuinely new and whether a family is becoming
unproductive.  It does not itself move a family to the Graveyard; M160 owns the
loop-governor transitions.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import fmean
from typing import Iterable

from .controlled_evolution import ExperimentOutcomeType
from .strategy_genome_v2 import CompiledStrategyGenomeV2


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


def _unit(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0.0 or rendered > 1.0:
        raise ValueError(f"{label} must be finite in [0, 1]")
    return rendered


def _jaccard_distance(left: set[str], right: set[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return 1.0 - len(left & right) / len(union)


def structural_tokens(strategy: CompiledStrategyGenomeV2) -> frozenset[str]:
    """Shape identity, deliberately excluding mutable values and provenance."""

    tokens = {f"symbol:{value}" for value in strategy.symbols}
    tokens.update(f"timeframe:{value}" for value in strategy.timeframes)
    for clause in strategy.clauses:
        prefix = f"clause:{clause.kind.value}:{clause.source_key}"
        tokens.add(prefix)
        tokens.add(f"resolution:{clause.clause_id}:{clause.resolution.value}")
        for feature in clause.features:
            tokens.add(f"feature:{clause.clause_id}:{feature.name.lower()}@{feature.version}")
        for key, _ in clause.parameters:
            tokens.add(f"parameter:{clause.clause_id}:{key.lower()}")
    return frozenset(tokens)


def structural_family_fingerprint(strategy: CompiledStrategyGenomeV2) -> str:
    return _digest(("dusty-m159-structural-family-v1", tuple(sorted(structural_tokens(strategy)))))


def _numeric_distance(left: str, right: str) -> float | None:
    try:
        a = float(left)
        b = float(right)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(a) or not math.isfinite(b):
        return None
    scale = max(abs(a), abs(b), 1.0)
    return min(abs(a - b) / scale, 1.0)


def _value_distance(left: str, right: str) -> float:
    if left == right:
        return 0.0
    numeric = _numeric_distance(left, right)
    return 1.0 if numeric is None else numeric


@dataclass(frozen=True, slots=True)
class SemanticDistance:
    structural: float
    clause_values: float
    parameters: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "structural", _unit(self.structural, "structural distance"))
        object.__setattr__(self, "clause_values", _unit(self.clause_values, "clause-value distance"))
        object.__setattr__(self, "parameters", _unit(self.parameters, "parameter distance"))

    def combined(self, policy: "NoveltyPolicy") -> float:
        return (
            self.structural * policy.structural_weight
            + self.clause_values * policy.clause_value_weight
            + self.parameters * policy.parameter_weight
        )


def semantic_distance(left: CompiledStrategyGenomeV2, right: CompiledStrategyGenomeV2) -> SemanticDistance:
    structural = _jaccard_distance(set(structural_tokens(left)), set(structural_tokens(right)))

    left_clauses = {(row.kind.value, row.source_key): row for row in left.clauses}
    right_clauses = {(row.kind.value, row.source_key): row for row in right.clauses}
    clause_keys = set(left_clauses) | set(right_clauses)
    clause_distances: list[float] = []
    parameter_distances: list[float] = []
    for key in sorted(clause_keys):
        a = left_clauses.get(key)
        b = right_clauses.get(key)
        if a is None or b is None:
            clause_distances.append(1.0)
            parameter_distances.append(1.0)
            continue
        clause_distances.append(_value_distance(a.value, b.value))
        a_params = {name.lower(): value for name, value in a.parameters}
        b_params = {name.lower(): value for name, value in b.parameters}
        parameter_keys = set(a_params) | set(b_params)
        if not parameter_keys:
            continue
        for parameter in sorted(parameter_keys):
            if parameter not in a_params or parameter not in b_params:
                parameter_distances.append(1.0)
            else:
                parameter_distances.append(_value_distance(a_params[parameter], b_params[parameter]))

    return SemanticDistance(
        structural,
        fmean(clause_distances) if clause_distances else 0.0,
        fmean(parameter_distances) if parameter_distances else 0.0,
    )


@dataclass(frozen=True, slots=True)
class BehaviorSignature:
    evaluation_fingerprint: str
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluation_fingerprint", _sha(self.evaluation_fingerprint, "behavior evaluation"))
        values = tuple(float(value) for value in self.values)
        if len(values) < 2 or any(not math.isfinite(value) for value in values):
            raise ValueError("behavior signature requires at least two finite observations")
        object.__setattr__(self, "values", values)

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m159-behavior-v1", self.evaluation_fingerprint, self.values))


def behavior_correlation(left: BehaviorSignature, right: BehaviorSignature) -> float | None:
    if left.evaluation_fingerprint != right.evaluation_fingerprint:
        raise ValueError("behavior signatures are comparable only on the same evaluation evidence")
    if len(left.values) != len(right.values):
        raise ValueError("behavior signatures require aligned observation counts")
    a_mean = fmean(left.values)
    b_mean = fmean(right.values)
    a_dev = tuple(value - a_mean for value in left.values)
    b_dev = tuple(value - b_mean for value in right.values)
    a_ss = sum(value * value for value in a_dev)
    b_ss = sum(value * value for value in b_dev)
    if a_ss == 0.0 or b_ss == 0.0:
        return None
    return sum(a * b for a, b in zip(a_dev, b_dev)) / math.sqrt(a_ss * b_ss)


@dataclass(frozen=True, slots=True)
class NoveltyPolicy:
    structural_weight: float = 0.40
    clause_value_weight: float = 0.35
    parameter_weight: float = 0.25
    near_duplicate_distance: float = 0.08
    family_variant_distance: float = 0.25
    behavior_duplicate_correlation: float = 0.99
    behavior_variant_correlation: float = 0.90
    version: str = "m159-novelty-v1"

    def __post_init__(self) -> None:
        weights = (self.structural_weight, self.clause_value_weight, self.parameter_weight)
        if any(not math.isfinite(value) or value < 0.0 for value in weights) or not math.isclose(sum(weights), 1.0):
            raise ValueError("novelty weights must be nonnegative and sum to 1")
        object.__setattr__(self, "near_duplicate_distance", _unit(self.near_duplicate_distance, "near-duplicate distance"))
        object.__setattr__(self, "family_variant_distance", _unit(self.family_variant_distance, "family-variant distance"))
        if self.near_duplicate_distance > self.family_variant_distance:
            raise ValueError("near-duplicate threshold cannot exceed family-variant threshold")
        for name in ("behavior_duplicate_correlation", "behavior_variant_correlation"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < -1.0 or value > 1.0:
                raise ValueError(f"{name} must be finite in [-1, 1]")
        if self.behavior_variant_correlation > self.behavior_duplicate_correlation:
            raise ValueError("behavior variant threshold cannot exceed duplicate threshold")
        object.__setattr__(self, "version", _text(self.version, "novelty policy version"))


class NoveltyClass(StrEnum):
    EXACT_DUPLICATE = "exact_duplicate"
    NEAR_DUPLICATE = "near_duplicate"
    FAMILY_VARIANT = "family_variant"
    NOVEL = "novel"


@dataclass(frozen=True, slots=True)
class NoveltyAssessment:
    classification: NoveltyClass
    distance: SemanticDistance
    combined_distance: float
    behavior_correlation: float | None
    same_structural_family: bool
    policy_version: str


def assess_novelty(
    candidate: CompiledStrategyGenomeV2,
    incumbent: CompiledStrategyGenomeV2,
    *,
    policy: NoveltyPolicy = NoveltyPolicy(),
    candidate_behavior: BehaviorSignature | None = None,
    incumbent_behavior: BehaviorSignature | None = None,
) -> NoveltyAssessment:
    distance = semantic_distance(candidate, incumbent)
    combined = distance.combined(policy)
    correlation: float | None = None
    if (candidate_behavior is None) != (incumbent_behavior is None):
        raise ValueError("behavior comparison requires signatures for both strategies")
    if candidate_behavior is not None and incumbent_behavior is not None:
        correlation = behavior_correlation(candidate_behavior, incumbent_behavior)

    same_family = structural_family_fingerprint(candidate) == structural_family_fingerprint(incumbent)
    if candidate.execution_fingerprint == incumbent.execution_fingerprint:
        classification = NoveltyClass.EXACT_DUPLICATE
    elif same_family and combined <= policy.near_duplicate_distance:
        classification = NoveltyClass.NEAR_DUPLICATE
    elif correlation is not None and correlation >= policy.behavior_duplicate_correlation and combined <= policy.family_variant_distance:
        classification = NoveltyClass.NEAR_DUPLICATE
    elif same_family and combined <= policy.family_variant_distance:
        classification = NoveltyClass.FAMILY_VARIANT
    elif correlation is not None and correlation >= policy.behavior_variant_correlation:
        classification = NoveltyClass.FAMILY_VARIANT
    else:
        classification = NoveltyClass.NOVEL
    return NoveltyAssessment(classification, distance, combined, correlation, same_family, policy.version)


class StrategyLineageIndex:
    """In-memory ancestry graph; unknown external roots are valid leaf references."""

    def __init__(self) -> None:
        self._parents: dict[str, tuple[str, ...]] = {}
        self._family: dict[str, str] = {}

    def register(self, strategy: CompiledStrategyGenomeV2) -> None:
        node = _sha(strategy.source_genome_fingerprint, "lineage node")
        parents = tuple(sorted({_sha(value, "lineage parent") for value in strategy.parent_fingerprints}))
        if node in parents:
            raise ValueError("strategy cannot be its own parent")
        existing = self._parents.get(node)
        if existing is not None and existing != parents:
            raise ValueError("lineage node cannot change parents")

        had_parent = node in self._parents
        old_parents = self._parents.get(node)
        had_family = node in self._family
        old_family = self._family.get(node)
        self._parents[node] = parents
        self._family[node] = structural_family_fingerprint(strategy)
        try:
            self._assert_acyclic()
        except Exception:
            if had_parent:
                assert old_parents is not None
                self._parents[node] = old_parents
            else:
                self._parents.pop(node, None)
            if had_family:
                assert old_family is not None
                self._family[node] = old_family
            else:
                self._family.pop(node, None)
            raise

    def _assert_acyclic(self) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visited or node not in self._parents:
                return
            if node in visiting:
                raise ValueError(f"strategy lineage cycle detected at {node}")
            visiting.add(node)
            for parent in self._parents[node]:
                visit(parent)
            visiting.remove(node)
            visited.add(node)

        for node in tuple(self._parents):
            visit(node)

    def parents(self, fingerprint: str) -> tuple[str, ...]:
        return self._parents.get(_sha(fingerprint, "lineage lookup"), ())

    def ancestors(self, fingerprint: str) -> tuple[str, ...]:
        node = _sha(fingerprint, "lineage lookup")
        found: set[str] = set()
        stack = list(self._parents.get(node, ()))
        while stack:
            parent = stack.pop()
            if parent in found:
                continue
            found.add(parent)
            stack.extend(self._parents.get(parent, ()))
        return tuple(sorted(found))

    def family_members(self, family_fingerprint: str) -> tuple[str, ...]:
        family = _sha(family_fingerprint, "family fingerprint")
        return tuple(sorted(node for node, value in self._family.items() if value == family))


@dataclass(frozen=True, slots=True)
class FamilyExperimentEvidence:
    family_fingerprint: str
    execution_fingerprint: str
    outcome: ExperimentOutcomeType
    mutation_axis: str
    novelty_score: float
    improvement_score: float
    evidence_fingerprint: str
    research_sequence: int
    failure_mechanism: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "family_fingerprint", _sha(self.family_fingerprint, "family evidence"))
        object.__setattr__(self, "execution_fingerprint", _sha(self.execution_fingerprint, "family execution"))
        object.__setattr__(self, "mutation_axis", _text(self.mutation_axis, "mutation axis").lower())
        object.__setattr__(self, "novelty_score", _unit(self.novelty_score, "novelty score"))
        object.__setattr__(self, "improvement_score", _unit(self.improvement_score, "improvement score"))
        object.__setattr__(self, "evidence_fingerprint", _sha(self.evidence_fingerprint, "family evidence artifact"))
        if isinstance(self.research_sequence, bool) or int(self.research_sequence) != self.research_sequence:
            raise ValueError("research sequence must be an integer")
        if int(self.research_sequence) < 0:
            raise ValueError("research sequence cannot be negative")
        object.__setattr__(self, "research_sequence", int(self.research_sequence))
        object.__setattr__(self, "failure_mechanism", str(self.failure_mechanism).strip().lower())

    @property
    def is_research_attempt(self) -> bool:
        return self.outcome is not ExperimentOutcomeType.INFRASTRUCTURE_FAILED


class ExhaustionSignal(StrEnum):
    NONE = "none"
    WARNING = "warning"
    STRONG = "strong"


@dataclass(frozen=True, slots=True)
class ExhaustionPolicy:
    minimum_research_attempts: int = 12
    recent_window: int = 8
    minimum_mutation_axes: int = 3
    warning_max_recent_novelty: float = 0.20
    strong_max_recent_novelty: float = 0.10
    warning_max_recent_improvement: float = 0.10
    strong_max_recent_improvement: float = 0.04
    warning_failure_fraction: float = 0.75
    strong_failure_fraction: float = 0.90
    dominant_failure_fraction: float = 0.60
    version: str = "m159-exhaustion-v1"

    def __post_init__(self) -> None:
        if self.minimum_research_attempts < 1 or self.recent_window < 2 or self.minimum_mutation_axes < 1:
            raise ValueError("exhaustion count bounds must be positive")
        if self.recent_window > self.minimum_research_attempts:
            raise ValueError("recent window cannot exceed minimum research attempts")
        for name in (
            "warning_max_recent_novelty",
            "strong_max_recent_novelty",
            "warning_max_recent_improvement",
            "strong_max_recent_improvement",
            "warning_failure_fraction",
            "strong_failure_fraction",
            "dominant_failure_fraction",
        ):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        if self.strong_max_recent_novelty > self.warning_max_recent_novelty:
            raise ValueError("strong novelty ceiling must be no greater than warning ceiling")
        if self.strong_max_recent_improvement > self.warning_max_recent_improvement:
            raise ValueError("strong improvement ceiling must be no greater than warning ceiling")
        if self.strong_failure_fraction < self.warning_failure_fraction:
            raise ValueError("strong failure fraction must be at least warning fraction")
        object.__setattr__(self, "version", _text(self.version, "exhaustion policy version"))


@dataclass(frozen=True, slots=True)
class ExhaustionAssessment:
    signal: ExhaustionSignal
    research_attempts: int
    mutation_axes: int
    recent_mean_novelty: float
    recent_mean_improvement: float
    recent_failure_fraction: float
    dominant_failure_fraction: float
    dominant_failure_mechanism: str
    policy_version: str
    reasons: tuple[str, ...]


def assess_exhaustion(
    evidence: Iterable[FamilyExperimentEvidence],
    *,
    policy: ExhaustionPolicy = ExhaustionPolicy(),
) -> ExhaustionAssessment:
    rows = tuple(row for row in evidence if row.is_research_attempt)
    if not rows:
        return ExhaustionAssessment(ExhaustionSignal.NONE, 0, 0, 0.0, 0.0, 0.0, 0.0, "", policy.version, ("no research evidence",))
    family_ids = {row.family_fingerprint for row in rows}
    if len(family_ids) != 1:
        raise ValueError("exhaustion evidence must belong to one structural family")
    sequences = tuple(row.research_sequence for row in rows)
    if len(sequences) != len(set(sequences)):
        raise ValueError("research evidence sequence must be unique within a family")
    rows = tuple(sorted(rows, key=lambda row: row.research_sequence))

    recent = rows[-policy.recent_window :]
    novelty = fmean(row.novelty_score for row in recent)
    improvement = fmean(row.improvement_score for row in recent)
    failures = [row for row in recent if row.outcome is ExperimentOutcomeType.RESEARCH_FAILED]
    failure_fraction = len(failures) / len(recent)
    axes = len({row.mutation_axis for row in rows})

    mechanisms: dict[str, int] = {}
    for row in failures:
        if row.failure_mechanism:
            mechanisms[row.failure_mechanism] = mechanisms.get(row.failure_mechanism, 0) + 1
    dominant_mechanism = max(mechanisms, key=mechanisms.get) if mechanisms else ""
    dominant_fraction = (mechanisms.get(dominant_mechanism, 0) / len(failures)) if failures else 0.0

    sufficient = len(rows) >= policy.minimum_research_attempts and axes >= policy.minimum_mutation_axes
    warning = (
        sufficient
        and novelty <= policy.warning_max_recent_novelty
        and improvement <= policy.warning_max_recent_improvement
        and failure_fraction >= policy.warning_failure_fraction
    )
    strong = (
        warning
        and novelty <= policy.strong_max_recent_novelty
        and improvement <= policy.strong_max_recent_improvement
        and failure_fraction >= policy.strong_failure_fraction
        and dominant_fraction >= policy.dominant_failure_fraction
    )

    signal = ExhaustionSignal.STRONG if strong else ExhaustionSignal.WARNING if warning else ExhaustionSignal.NONE
    reasons = (
        f"research_attempts={len(rows)}",
        f"mutation_axes={axes}",
        f"recent_mean_novelty={novelty:.6f}",
        f"recent_mean_improvement={improvement:.6f}",
        f"recent_failure_fraction={failure_fraction:.6f}",
        f"dominant_failure_fraction={dominant_fraction:.6f}",
    )
    return ExhaustionAssessment(
        signal,
        len(rows),
        axes,
        novelty,
        improvement,
        failure_fraction,
        dominant_fraction,
        dominant_mechanism,
        policy.version,
        reasons,
    )
