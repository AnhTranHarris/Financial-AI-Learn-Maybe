from __future__ import annotations

"""M157 typed, feature-bound strategy genome compiler.

This layer compiles an already reviewed M143-M152 ``StrategyGenome`` into a
strict research IR.  It performs no NLP completion and grants no trading
or promotion authority.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Iterable

from .experiment_manifest import FeatureRef
from .feature_registry import FeatureRegistry
from .strategy_lab import ConstraintMode, StrategyConstraint, StrategyGenome, StrategyOrigin


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _text(value: str, label: str) -> str:
    rendered = str(value).strip()
    if not rendered:
        raise ValueError(f"{label} required")
    return rendered


def _pairs(values: Iterable[tuple[str, str]], label: str) -> tuple[tuple[str, str], ...]:
    rendered = tuple((_text(key, f"{label} key"), _text(value, f"{label} value")) for key, value in values)
    keys = tuple(key.lower() for key, _ in rendered)
    if len(keys) != len(set(keys)):
        raise ValueError(f"{label} keys must be unique")
    return tuple(sorted(rendered, key=lambda item: item[0].lower()))


def _feature_key(value: str) -> str:
    rendered = _text(value, "feature key").lower()
    name, separator, version = rendered.partition("@")
    if separator != "@" or not name or not version or "@" in version:
        raise ValueError("strategy feature must use name@version")
    return f"{name}@{version}"


def _source_key(value: str) -> str:
    rendered = _text(value, "strategy source key").lower()
    if any(ch.isspace() for ch in rendered):
        raise ValueError("strategy source key cannot contain whitespace")
    return rendered


def _unresolved_aliases(values: Iterable[str]) -> set[str]:
    aliases: set[str] = set()
    for value in values:
        key = _source_key(value)
        aliases.add(key)
        if key.startswith("unresolved."):
            aliases.add(key.removeprefix("unresolved."))
        else:
            aliases.add(f"unresolved.{key}")
    return aliases


class ClauseKind(StrEnum):
    UNIVERSE = "universe"
    CONTEXT = "context"
    REGIME = "regime"
    SETUP = "setup"
    TRIGGER = "trigger"
    INVALIDATION = "invalidation"
    MANAGEMENT = "management"
    EXIT = "exit"
    SESSION = "session"
    FORECAST = "forecast"
    RISK = "risk"


class ClauseResolution(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


_DECISION_KINDS = frozenset(
    {
        ClauseKind.CONTEXT,
        ClauseKind.REGIME,
        ClauseKind.SETUP,
        ClauseKind.TRIGGER,
        ClauseKind.INVALIDATION,
        ClauseKind.MANAGEMENT,
        ClauseKind.EXIT,
        ClauseKind.SESSION,
        ClauseKind.FORECAST,
        ClauseKind.RISK,
    }
)
_REQUIRED_KINDS = frozenset({ClauseKind.TRIGGER, ClauseKind.EXIT, ClauseKind.RISK})


@dataclass(frozen=True, slots=True)
class GenomeClauseSpec:
    """Explicit compiler input; no rule is invented from free text."""

    clause_id: str
    kind: ClauseKind
    source_key: str
    resolution: ClauseResolution
    value: str
    feature_keys: tuple[str, ...] = ()
    parameters: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "clause_id", _source_key(self.clause_id))
        object.__setattr__(self, "source_key", _source_key(self.source_key))
        object.__setattr__(self, "value", _text(self.value, "strategy clause value"))
        features = tuple(sorted({_feature_key(value) for value in self.feature_keys}))
        object.__setattr__(self, "feature_keys", features)
        object.__setattr__(self, "parameters", _pairs(self.parameters, "strategy clause parameter"))


@dataclass(frozen=True, slots=True)
class CompiledGenomeClause:
    clause_id: str
    kind: ClauseKind
    source_key: str
    constraint_mode: ConstraintMode
    resolution: ClauseResolution
    value: str
    features: tuple[FeatureRef, ...]
    parameters: tuple[tuple[str, str], ...]

    @property
    def payload(self) -> dict[str, object]:
        return {
            "clause_id": self.clause_id,
            "kind": self.kind.value,
            "source_key": self.source_key,
            "constraint_mode": self.constraint_mode.value,
            "resolution": self.resolution.value,
            "value": self.value,
            "features": tuple(row.payload for row in self.features),
            "parameters": self.parameters,
        }


@dataclass(frozen=True, slots=True)
class CompiledStrategyGenomeV2:
    source_genome_fingerprint: str
    source_family_fingerprint: str
    source_origin: StrategyOrigin
    source_provenance_fingerprint: str
    parent_fingerprints: tuple[str, ...]
    generation: int
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    clauses: tuple[CompiledGenomeClause, ...]
    constraints: tuple[StrategyConstraint, ...]
    feature_set_fingerprint: str
    compiler_protocol: str = "dusty-strategy-genome-v2"

    def __post_init__(self) -> None:
        for value, label in (
            (self.source_genome_fingerprint, "source genome"),
            (self.source_family_fingerprint, "source family"),
            (self.source_provenance_fingerprint, "source provenance"),
            (self.feature_set_fingerprint, "feature set"),
        ):
            if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
                raise ValueError(f"{label} requires SHA-256 identity")
        if any(len(value) != 64 for value in self.parent_fingerprints):
            raise ValueError("strategy parent requires SHA-256 identity")
        if self.generation < 0:
            raise ValueError("strategy generation cannot be negative")
        if not self.symbols or not self.timeframes:
            raise ValueError("compiled strategy requires explicit symbols and timeframes")
        clause_ids = tuple(row.clause_id for row in self.clauses)
        source_keys = tuple(row.source_key for row in self.clauses)
        if len(clause_ids) != len(set(clause_ids)) or len(source_keys) != len(set(source_keys)):
            raise ValueError("compiled strategy clauses require unique IDs and source keys")
        if not _REQUIRED_KINDS.issubset({row.kind for row in self.clauses}):
            missing = sorted(kind.value for kind in _REQUIRED_KINDS - {row.kind for row in self.clauses})
            raise ValueError(f"compiled strategy missing required clause kinds: {','.join(missing)}")

    @property
    def fully_specified(self) -> bool:
        return all(row.resolution is ClauseResolution.RESOLVED for row in self.clauses)

    @property
    def feature_refs(self) -> tuple[FeatureRef, ...]:
        unique: dict[tuple[str, str], FeatureRef] = {}
        for clause in self.clauses:
            for feature in clause.features:
                unique[(feature.name.lower(), feature.version)] = feature
        return tuple(sorted(unique.values(), key=lambda row: (row.name.lower(), row.version)))

    @property
    def manifest_ready(self) -> bool:
        return self.fully_specified and bool(self.feature_refs)

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def execution_payload(self) -> dict[str, object]:
        """Semantic identity for M155/M163; author/title/provenance are excluded."""

        return {
            "protocol": self.compiler_protocol,
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "clauses": tuple(row.payload for row in self.clauses),
            "feature_set_fingerprint": self.feature_set_fingerprint,
            "authority": {
                "broker_write": False,
                "risk_override": False,
                "promotion": False,
            },
        }

    @property
    def execution_fingerprint(self) -> str:
        return _digest(self.execution_payload)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": self.compiler_protocol,
            "source_genome_fingerprint": self.source_genome_fingerprint,
            "source_family_fingerprint": self.source_family_fingerprint,
            "source_origin": self.source_origin.value,
            "source_provenance_fingerprint": self.source_provenance_fingerprint,
            "parent_fingerprints": self.parent_fingerprints,
            "generation": self.generation,
            "execution_fingerprint": self.execution_fingerprint,
            "constraints": tuple(
                sorted(
                    (row.key.lower(), row.value, row.mode.value)
                    for row in self.constraints
                )
            ),
        }

    @property
    def fingerprint(self) -> str:
        """Research-record identity preserving authorship, ancestry, and mutation policy."""

        return _digest(self.payload)


@dataclass(frozen=True, slots=True)
class _SourceBinding:
    key: str
    mode: ConstraintMode
    value: str
    resolved: bool


def _lookup_source(
    source_key: str,
    *,
    rules: dict[str, str],
    constraints: dict[str, StrategyConstraint],
    unresolved_aliases: set[str],
) -> _SourceBinding:
    candidates = (source_key, f"unresolved.{source_key}") if not source_key.startswith("unresolved.") else (
        source_key,
        source_key.removeprefix("unresolved."),
    )
    key = next((candidate for candidate in candidates if candidate in rules or candidate in constraints), None)
    if key is None:
        raise ValueError(f"strategy clause source is not declared by genome: {source_key}")

    constraint = constraints.get(key)
    if key in rules:
        mode = constraint.mode if constraint is not None else ConstraintMode.LOCKED
        if mode is ConstraintMode.FORBIDDEN:
            raise PermissionError(f"forbidden source cannot become a strategy clause: {key}")
        # A rule is the resolved semantic truth.  Some older external genomes
        # may retain a bare unresolved alias after a bounded child supplied the
        # prefixed rule; the compiler repairs that legacy bookkeeping at this
        # boundary rather than treating known semantics as unknown.
        return _SourceBinding(key, mode, rules[key], True)

    assert constraint is not None
    if constraint.mode is ConstraintMode.FORBIDDEN:
        raise PermissionError(f"forbidden source cannot become a strategy clause: {key}")
    if constraint.mode is ConstraintMode.LOCKED:
        return _SourceBinding(key, constraint.mode, constraint.value, True)
    return _SourceBinding(key, constraint.mode, constraint.value, key not in unresolved_aliases)


def compile_strategy_genome_v2(
    genome: StrategyGenome,
    clause_specs: Iterable[GenomeClauseSpec],
    registry: FeatureRegistry,
) -> CompiledStrategyGenomeV2:
    """Compile a reviewed research genome into typed, feature-bound semantics.

    The function never guesses missing strategy rules. Researchable unknowns
    remain explicit until a bounded child genome actually carries a resolved
    rule. Resolved decision clauses may only consume M156 decision-eligible
    feature dependency closures.
    """

    if not registry.frozen:
        raise ValueError("feature registry must be validated and frozen before strategy compilation")
    if not genome.symbols or not genome.timeframes:
        raise ValueError("strategy genome requires explicit symbol/timeframe before M157 compilation")

    rules = {key.lower(): value for key, value in genome.rules}
    constraints = {row.key.lower(): row for row in genome.constraints}
    unresolved_aliases = _unresolved_aliases(genome.unresolved)
    specs = tuple(clause_specs)
    if not specs:
        raise ValueError("strategy compiler requires explicit clause specifications")
    if len({row.clause_id for row in specs}) != len(specs):
        raise ValueError("strategy clause IDs must be unique")

    compiled: list[CompiledGenomeClause] = []
    bound_sources: set[str] = set()
    all_feature_keys: set[str] = set()
    for spec in specs:
        source = _lookup_source(
            spec.source_key,
            rules=rules,
            constraints=constraints,
            unresolved_aliases=unresolved_aliases,
        )
        if source.key in bound_sources:
            raise ValueError(f"strategy source key bound more than once: {source.key}")
        bound_sources.add(source.key)

        expected_resolution = ClauseResolution.RESOLVED if source.resolved else ClauseResolution.UNRESOLVED
        if spec.resolution is not expected_resolution:
            raise ValueError(
                f"strategy clause {spec.clause_id} resolution mismatch: "
                f"source requires {expected_resolution.value}"
            )
        if source.resolved and spec.value != source.value:
            raise ValueError(f"strategy clause {spec.clause_id} cannot rewrite resolved source value")
        if not source.resolved and source.mode is not ConstraintMode.RESEARCHABLE:
            raise ValueError("only researchable source may remain unresolved")

        features = tuple(registry.to_manifest_ref(key) for key in spec.feature_keys)
        all_feature_keys.update(spec.feature_keys)
        if spec.resolution is ClauseResolution.RESOLVED and spec.kind in _DECISION_KINDS:
            for key in spec.feature_keys:
                if not registry.decision_eligible(key):
                    reasons = ",".join(registry.eligibility_reasons(key)) or "unknown"
                    raise ValueError(f"decision clause {spec.clause_id} uses ineligible feature {key}: {reasons}")
        if spec.kind is ClauseKind.TRIGGER and spec.resolution is ClauseResolution.RESOLVED and not features:
            raise ValueError("resolved trigger clause requires at least one versioned M156 feature")

        compiled.append(
            CompiledGenomeClause(
                clause_id=spec.clause_id,
                kind=spec.kind,
                source_key=source.key,
                constraint_mode=source.mode,
                resolution=spec.resolution,
                value=spec.value,
                features=features,
                parameters=spec.parameters,
            )
        )

    kinds = {row.kind for row in compiled}
    if not _REQUIRED_KINDS.issubset(kinds):
        missing = sorted(kind.value for kind in _REQUIRED_KINDS - kinds)
        raise ValueError(f"strategy compiler requires clause kinds: {','.join(missing)}")

    feature_set_fingerprint = (
        registry.feature_set_fingerprint(all_feature_keys)
        if all_feature_keys
        else _digest(("no-bound-features",))
    )
    return CompiledStrategyGenomeV2(
        source_genome_fingerprint=genome.fingerprint,
        source_family_fingerprint=genome.family_fingerprint,
        source_origin=genome.origin,
        source_provenance_fingerprint=genome.source_fingerprint,
        parent_fingerprints=tuple(sorted(genome.parent_fingerprints)),
        generation=genome.generation,
        symbols=tuple(sorted({value.upper() for value in genome.symbols})),
        timeframes=tuple(sorted({value.upper() for value in genome.timeframes})),
        clauses=tuple(sorted(compiled, key=lambda row: (row.kind.value, row.clause_id))),
        constraints=tuple(sorted(genome.constraints, key=lambda row: row.key.lower())),
        feature_set_fingerprint=feature_set_fingerprint,
    )
