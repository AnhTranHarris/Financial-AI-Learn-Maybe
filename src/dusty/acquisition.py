from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Mapping

from .experience import TradeSide
from .research import Clause, RuleOp, StrategySpec


class StrategyAccess(StrEnum):
    OPEN_SOURCE = "open_source"
    AUTHORIZED_PRIVATE = "authorized_private"
    DESCRIPTION_ONLY = "description_only"
    PERFORMANCE_ONLY = "performance_only"


class AcquisitionState(StrEnum):
    DISCOVERED = "discovered"
    UNDERSTOOD = "understood"
    QUARANTINED = "quarantined"


class LineageRelation(StrEnum):
    RECONSTRUCTION = "reconstruction"
    MUTATION = "mutation"
    FORK = "fork"


@dataclass(frozen=True, slots=True)
class ExternalRule:
    feature: str
    op: RuleOp
    value: bool | int | float | str

    def __post_init__(self) -> None:
        if not self.feature:
            raise ValueError("external rule feature is required")


@dataclass(frozen=True, slots=True)
class ExternalStrategy:
    source_id: str
    platform: str
    source_url: str
    external_id: str
    title: str
    direction: TradeSide
    rules: tuple[ExternalRule, ...] = ()
    access: StrategyAccess = StrategyAccess.DESCRIPTION_ONLY
    license_name: str | None = None
    code_text: str | None = None
    rationale: str = ""
    symbols: tuple[str, ...] = ()
    timeframe: str = ""
    reported_metrics: tuple[tuple[str, float], ...] = ()

    @classmethod
    def of(
        cls,
        *,
        source_id: str,
        platform: str,
        source_url: str,
        external_id: str,
        title: str,
        direction: TradeSide,
        rules: tuple[ExternalRule, ...] = (),
        access: StrategyAccess = StrategyAccess.DESCRIPTION_ONLY,
        license_name: str | None = None,
        code_text: str | None = None,
        rationale: str = "",
        symbols: tuple[str, ...] = (),
        timeframe: str = "",
        reported_metrics: Mapping[str, float] | None = None,
    ) -> "ExternalStrategy":
        return cls(
            source_id=source_id,
            platform=platform,
            source_url=source_url,
            external_id=external_id,
            title=title,
            direction=direction,
            rules=rules,
            access=access,
            license_name=license_name,
            code_text=code_text,
            rationale=rationale,
            symbols=tuple(sorted(symbol.upper() for symbol in symbols)),
            timeframe=timeframe.upper(),
            reported_metrics=tuple(sorted((reported_metrics or {}).items())),
        )


@dataclass(frozen=True, slots=True)
class AcquisitionAssessment:
    state: AcquisitionState
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrategyTranslation:
    spec: StrategySpec
    source_id: str
    external_id: str
    family_hash: str


@dataclass(frozen=True, slots=True)
class LineageEdge:
    parent_hash: str
    child_hash: str
    relation: LineageRelation
    source_id: str


def assess_external_strategy(strategy: ExternalStrategy) -> AcquisitionAssessment:
    reasons: list[str] = []
    if not all((strategy.source_id, strategy.platform, strategy.source_url, strategy.external_id, strategy.title)):
        reasons.append("missing_provenance")
    if strategy.code_text is not None:
        if strategy.access not in {StrategyAccess.OPEN_SOURCE, StrategyAccess.AUTHORIZED_PRIVATE}:
            reasons.append("hidden_code_not_authorized")
        if not strategy.license_name:
            reasons.append("code_license_unknown")
    if reasons:
        return AcquisitionAssessment(AcquisitionState.QUARANTINED, tuple(reasons))
    if not strategy.rules:
        return AcquisitionAssessment(AcquisitionState.DISCOVERED, ("rules_not_understood",))
    return AcquisitionAssessment(AcquisitionState.UNDERSTOOD, ())


def strategy_family_hash(direction: TradeSide, rules: tuple[ExternalRule, ...]) -> str:
    """Fingerprint structural idea while deliberately ignoring thresholds and source popularity."""
    payload = {
        "direction": direction.value,
        "structure": sorted((rule.feature, rule.op.value) for rule in rules),
    }
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def translate_external_strategy(strategy: ExternalStrategy) -> StrategyTranslation:
    assessment = assess_external_strategy(strategy)
    if assessment.state is not AcquisitionState.UNDERSTOOD:
        raise ValueError(f"strategy is not translatable: {','.join(assessment.reasons)}")
    clauses = tuple(Clause(rule.feature, rule.op, rule.value) for rule in strategy.rules)
    spec = StrategySpec(
        strategy_id=f"external:{strategy.platform}:{strategy.external_id}",
        direction=strategy.direction,
        clauses=clauses,
    )
    return StrategyTranslation(
        spec=spec,
        source_id=strategy.source_id,
        external_id=strategy.external_id,
        family_hash=strategy_family_hash(strategy.direction, strategy.rules),
    )


def lineage_edge(
    parent: StrategySpec,
    child: StrategySpec,
    *,
    relation: LineageRelation,
    source_id: str,
) -> LineageEdge:
    if parent.strategy_hash == child.strategy_hash:
        raise ValueError("lineage edge requires a changed strategy")
    return LineageEdge(parent.strategy_hash, child.strategy_hash, relation, source_id)
