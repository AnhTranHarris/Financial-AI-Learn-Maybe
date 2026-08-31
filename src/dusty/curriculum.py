from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from math import inf
from typing import Iterable, Mapping

from .experience import SourceGrade


class SourcePlatform(StrEnum):
    FOREX_FACTORY = "forex_factory"
    MYFXBOOK = "myfxbook"
    TRADINGVIEW = "tradingview"
    QUANTPEDIA = "quantpedia"
    QUANTCONNECT = "quantconnect"
    GITHUB = "github"
    PAPER = "paper"
    OTHER = "other"


class RelevanceTier(StrEnum):
    EXACT = "exact"
    RELATED = "related"
    TRANSFER = "transfer"


class NoveltyState(StrEnum):
    NEW_FAMILY = "new_family"
    MEANINGFUL_VARIANT = "meaningful_variant"
    EXACT_DUPLICATE = "exact_duplicate"


class TradingConcept(StrEnum):
    TREND = "trend"
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"
    VOLATILITY = "volatility"
    SESSION = "session"
    EVENT = "event"
    CARRY = "carry"
    RELATIVE_VALUE = "relative_value"
    PRICE_ACTION = "price_action"
    EXECUTION = "execution"
    RISK = "risk"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class SymbolIdentity:
    raw: str
    canonical: str
    alias_verified: bool


def resolve_symbol(raw: str, aliases: Mapping[str, str] | None = None) -> SymbolIdentity:
    if not raw.strip():
        raise ValueError("symbol is required")
    label = raw.strip().upper()
    compact = "".join(ch for ch in label if ch.isalnum())
    alias_map = {
        key.strip().upper(): "".join(ch for ch in value.strip().upper() if ch.isalnum())
        for key, value in (aliases or {}).items()
    }
    if label in alias_map:
        return SymbolIdentity(raw, alias_map[label], True)
    if compact in alias_map:
        return SymbolIdentity(raw, alias_map[compact], True)
    if len(compact) == 6 and compact.isalpha():
        return SymbolIdentity(raw, compact, False)
    return SymbolIdentity(raw, compact, False)


@dataclass(frozen=True, slots=True)
class CurriculumCandidate:
    source_id: str
    external_id: str
    platform: SourcePlatform
    symbol: SymbolIdentity
    family_hash: str
    strategy_hash: str
    known_at: datetime
    source_grade: SourceGrade = SourceGrade.UNKNOWN
    gain: float | None = None
    drawdown: float | None = None
    trade_count: int = 0
    history_days: int = 0
    popularity: int | None = None
    verified: bool = False
    rules_understood: bool = False

    def __post_init__(self) -> None:
        if not all((self.source_id, self.external_id, self.family_hash, self.strategy_hash)):
            raise ValueError("candidate identity and strategy lineage are required")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("candidate known_at must be timezone-aware")
        if self.drawdown is not None and self.drawdown < 0:
            raise ValueError("drawdown cannot be negative")
        if self.trade_count < 0 or self.history_days < 0:
            raise ValueError("trade_count and history_days cannot be negative")
        if self.popularity is not None and self.popularity < 0:
            raise ValueError("popularity cannot be negative")


@dataclass(frozen=True, slots=True)
class CohortPolicy:
    top_n: int = 20
    control_n: int = 20

    def __post_init__(self) -> None:
        if self.top_n < 1 or self.control_n < 0:
            raise ValueError("cohort sizes are invalid")


@dataclass(frozen=True, slots=True)
class SourceCohort:
    platform: SourcePlatform
    target_symbol: str
    raw_top_gain: tuple[CurriculumCandidate, ...]
    research_top: tuple[CurriculumCandidate, ...]
    popularity_top: tuple[CurriculumCandidate, ...]
    controls: tuple[CurriculumCandidate, ...]


@dataclass(frozen=True, slots=True)
class CurriculumSnapshot:
    target_symbol: str
    as_of: datetime
    cohorts: tuple[SourceCohort, ...]


_GRADE_RANK = {
    SourceGrade.LIVE: 5,
    SourceGrade.FORWARD_TEST: 4,
    SourceGrade.DEMO: 3,
    SourceGrade.BACKTEST: 2,
    SourceGrade.UNKNOWN: 1,
}


def relevance(target_symbol: str, candidate_symbol: str, *, related_symbols: Iterable[str] = ()) -> RelevanceTier:
    target = resolve_symbol(target_symbol).canonical
    candidate = resolve_symbol(candidate_symbol).canonical
    if candidate == target:
        return RelevanceTier.EXACT
    related = {resolve_symbol(symbol).canonical for symbol in related_symbols}
    return RelevanceTier.RELATED if candidate in related else RelevanceTier.TRANSFER


def _quality_key(candidate: CurriculumCandidate) -> tuple[object, ...]:
    return (
        -int(candidate.verified),
        -_GRADE_RANK[candidate.source_grade],
        -candidate.history_days,
        -candidate.trade_count,
        candidate.drawdown if candidate.drawdown is not None else inf,
        -(candidate.gain if candidate.gain is not None else -inf),
        candidate.external_id,
    )


def build_symbol_curriculum(candidates: Iterable[CurriculumCandidate], target_symbol: str, *, as_of: datetime, policy: CohortPolicy = CohortPolicy()) -> CurriculumSnapshot:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    target = resolve_symbol(target_symbol).canonical
    eligible = [item for item in candidates if item.known_at <= as_of and item.symbol.canonical == target]
    cohorts: list[SourceCohort] = []
    for platform in SourcePlatform:
        group = [item for item in eligible if item.platform is platform]
        if not group:
            continue
        gains = [item for item in group if item.gain is not None]
        raw_top = tuple(sorted(gains, key=lambda item: (-(item.gain or 0.0), -item.trade_count, -item.history_days, item.external_id))[:policy.top_n])
        research_top = tuple(sorted(group, key=_quality_key)[:policy.top_n])
        popular = [item for item in group if item.popularity is not None]
        popularity_top = tuple(sorted(popular, key=lambda item: (-(item.popularity or 0), -(item.gain if item.gain is not None else -inf), item.external_id))[:policy.top_n])
        controls = tuple(sorted(gains, key=lambda item: (item.gain if item.gain is not None else inf, -item.trade_count, item.external_id))[:policy.control_n])
        cohorts.append(SourceCohort(platform, target, raw_top, research_top, popularity_top, controls))
    return CurriculumSnapshot(target, as_of, tuple(cohorts))


@dataclass(frozen=True, slots=True)
class CompressedCurriculum:
    representatives: tuple[CurriculumCandidate, ...]
    exact_duplicate_count: int
    family_variant_count: int


def novelty_state(candidate: CurriculumCandidate, *, known_strategy_hashes: set[str], known_family_hashes: set[str]) -> NoveltyState:
    if candidate.strategy_hash in known_strategy_hashes:
        return NoveltyState.EXACT_DUPLICATE
    if candidate.family_hash in known_family_hashes:
        return NoveltyState.MEANINGFUL_VARIANT
    return NoveltyState.NEW_FAMILY


def compress_curriculum(candidates: Iterable[CurriculumCandidate], *, max_families: int = 64) -> CompressedCurriculum:
    if max_families < 1:
        raise ValueError("max_families must be positive")
    by_strategy: dict[str, CurriculumCandidate] = {}
    exact_duplicates = 0
    for candidate in candidates:
        previous = by_strategy.get(candidate.strategy_hash)
        if previous is None:
            by_strategy[candidate.strategy_hash] = candidate
        else:
            exact_duplicates += 1
            if _quality_key(candidate) < _quality_key(previous):
                by_strategy[candidate.strategy_hash] = candidate
    by_family: dict[str, CurriculumCandidate] = {}
    family_variants = 0
    for candidate in by_strategy.values():
        previous = by_family.get(candidate.family_hash)
        if previous is None:
            by_family[candidate.family_hash] = candidate
        else:
            family_variants += 1
            if _quality_key(candidate) < _quality_key(previous):
                by_family[candidate.family_hash] = candidate
    representatives = tuple(sorted(by_family.values(), key=_quality_key)[:max_families])
    return CompressedCurriculum(representatives, exact_duplicates, family_variants)


@dataclass(frozen=True, slots=True)
class ToolRelation:
    tool: str
    concept: TradingConcept
    role: str
    source_id: str
    symbol: str
    known_at: datetime

    def __post_init__(self) -> None:
        if not all((self.tool.strip(), self.role.strip(), self.source_id.strip(), self.symbol.strip())):
            raise ValueError("tool relation requires tool, role, source, and symbol")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("tool relation known_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class MethodInsight:
    insight_id: str
    target_symbol: str
    statement: str
    concepts: tuple[TradingConcept, ...]
    features: tuple[str, ...]
    source_ids: tuple[str, ...]
    known_at: datetime
    counterexample: bool = False

    def __post_init__(self) -> None:
        if not self.insight_id or not self.statement.strip() or not self.source_ids:
            raise ValueError("insight requires identity, statement, and provenance")
        if not self.concepts:
            raise ValueError("insight requires at least one explicit concept")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("insight known_at must be timezone-aware")


def make_method_insight(*, insight_id: str, target_symbol: str, statement: str, concepts: Iterable[TradingConcept], features: Iterable[str], source_ids: Iterable[str], known_at: datetime, counterexample: bool = False) -> MethodInsight:
    return MethodInsight(
        insight_id=insight_id,
        target_symbol=resolve_symbol(target_symbol).canonical,
        statement=statement.strip(),
        concepts=tuple(sorted(set(concepts), key=lambda item: item.value)),
        features=tuple(sorted({item.strip().lower() for item in features if item.strip()})),
        source_ids=tuple(sorted({item.strip() for item in source_ids if item.strip()})),
        known_at=known_at,
        counterexample=counterexample,
    )
