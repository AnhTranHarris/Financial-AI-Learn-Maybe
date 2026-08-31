from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable

from .markets import SymbolResearchProfile
from .news import NewsItem, SymbolNewsRegistry, eligible_news_items


class ScenarioState(StrEnum):
    CONTINUATION = "continuation"
    ESCALATION = "escalation"
    DEESCALATION = "deescalation"
    SUPPLY_DISRUPTION = "supply_disruption"
    NO_EFFECT = "no_effect"


class TransmissionChannel(StrEnum):
    PRODUCTION = "production"
    SHIPPING = "shipping"
    SANCTIONS = "sanctions"
    MONETARY_POLICY = "monetary_policy"
    INFLATION = "inflation"
    RISK_SENTIMENT = "risk_sentiment"
    DEMAND = "demand"
    INVENTORY = "inventory"
    LIQUIDITY = "liquidity"
    OTHER = "other"


class Corroboration(StrEnum):
    UNCORROBORATED = "uncorroborated"
    CORROBORATED = "corroborated"
    MULTISOURCE = "multisource"


@dataclass(frozen=True, slots=True)
class NewsCluster:
    cluster_id: str
    target_symbol: str
    event_key: str
    started_at: datetime
    last_known_at: datetime
    items: tuple[NewsItem, ...]
    source_ids: tuple[str, ...]
    publisher_groups: tuple[str, ...]
    duplicate_count: int = 0

    @property
    def corroboration(self) -> Corroboration:
        count = len(self.publisher_groups)
        if count < 2:
            return Corroboration.UNCORROBORATED
        if count == 2:
            return Corroboration.CORROBORATED
        return Corroboration.MULTISOURCE


@dataclass(frozen=True, slots=True)
class ScenarioHypothesis:
    scenario_id: str
    target_symbol: str
    event_key: str
    known_at: datetime
    state: ScenarioState
    premise: str
    transmission: tuple[TransmissionChannel, ...]
    confirmations: tuple[str, ...]
    invalidations: tuple[str, ...]
    source_ids: tuple[str, ...]
    publisher_groups: tuple[str, ...]

    def __post_init__(self) -> None:
        if not all((self.scenario_id.strip(), self.target_symbol.strip(), self.event_key.strip(), self.premise.strip())):
            raise ValueError("scenario requires identity, target, event, and premise")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("scenario known_at must be timezone-aware")
        if not self.transmission:
            raise ValueError("scenario requires at least one explicit transmission channel")
        if not self.confirmations or not self.invalidations:
            raise ValueError("scenario requires confirmation and invalidation criteria")

    @property
    def corroboration(self) -> Corroboration:
        count = len(self.publisher_groups)
        if count < 2:
            return Corroboration.UNCORROBORATED
        if count == 2:
            return Corroboration.CORROBORATED
        return Corroboration.MULTISOURCE

    @property
    def broker_write_authorized(self) -> bool:
        return False


def cluster_unscheduled_news(
    items: Iterable[NewsItem],
    registry: SymbolNewsRegistry,
    profile: SymbolResearchProfile,
    *,
    as_of: datetime,
    max_items_per_cluster: int = 64,
    max_clusters: int = 32,
) -> tuple[NewsCluster, ...]:
    """Cluster adapter-classified event keys, deduplicating headlines and publisher syndication."""
    if max_items_per_cluster < 1 or max_clusters < 1:
        raise ValueError("cluster budgets must be positive")
    sources = registry.source_map()
    eligible = eligible_news_items(items, registry, profile, as_of=as_of, limit=max_items_per_cluster * max_clusters * 4)
    grouped: dict[str, list[NewsItem]] = {}
    duplicate_counts: dict[str, int] = {}
    seen_hashes: dict[str, set[str]] = {}
    for item in sorted(eligible, key=lambda value: (value.known_at, value.source_id, value.external_id)):
        if not item.event_key:
            continue
        key = item.event_key
        if key not in grouped and len(grouped) >= max_clusters:
            continue
        hashes = seen_hashes.setdefault(key, set())
        if item.story_hash in hashes:
            duplicate_counts[key] = duplicate_counts.get(key, 0) + 1
            continue
        if len(grouped.setdefault(key, [])) >= max_items_per_cluster:
            continue
        hashes.add(item.story_hash)
        grouped[key].append(item)

    clusters = []
    for index, (event_key, members) in enumerate(sorted(grouped.items()), start=1):
        source_ids = tuple(sorted({item.source_id for item in members}))
        publisher_groups = tuple(sorted({sources[item.source_id].publisher_group for item in members if item.source_id in sources}))
        clusters.append(
            NewsCluster(
                cluster_id=f"news-cluster:{profile.market.canonical_symbol}:{index}:{event_key}",
                target_symbol=profile.market.canonical_symbol,
                event_key=event_key,
                started_at=min(item.known_at for item in members),
                last_known_at=max(item.known_at for item in members),
                items=tuple(members),
                source_ids=source_ids,
                publisher_groups=publisher_groups,
                duplicate_count=duplicate_counts.get(event_key, 0),
            )
        )
    return tuple(clusters)


def make_scenario(
    cluster: NewsCluster,
    *,
    scenario_id: str,
    state: ScenarioState,
    premise: str,
    transmission: Iterable[TransmissionChannel],
    confirmations: Iterable[str],
    invalidations: Iterable[str],
) -> ScenarioHypothesis:
    """Create a conditional scenario from explicit classifications; the core never invents causality from headlines."""
    return ScenarioHypothesis(
        scenario_id=scenario_id,
        target_symbol=cluster.target_symbol,
        event_key=cluster.event_key,
        known_at=cluster.last_known_at,
        state=state,
        premise=premise.strip(),
        transmission=tuple(sorted(set(transmission), key=lambda item: item.value)),
        confirmations=tuple(sorted({item.strip() for item in confirmations if item.strip()})),
        invalidations=tuple(sorted({item.strip() for item in invalidations if item.strip()})),
        source_ids=cluster.source_ids,
        publisher_groups=cluster.publisher_groups,
    )
