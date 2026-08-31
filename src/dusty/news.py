from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Iterable

from .markets import AssetClass, SymbolResearchProfile


class NewsAccess(StrEnum):
    FREE_PRIMARY = "free_primary"
    FREE_PUBLIC_API = "free_public_api"
    FREE_PUBLIC_FEED = "free_public_feed"
    USER_PROVIDED = "user_provided"
    PAID = "paid"
    LICENSE_RESTRICTED = "license_restricted"
    UNKNOWN = "unknown"


class SourceRole(StrEnum):
    PRIMARY_FACT = "primary_fact"
    CALENDAR = "calendar"
    SECONDARY_CONTEXT = "secondary_context"


_AUTOMATIC_ACCESS = {
    NewsAccess.FREE_PRIMARY,
    NewsAccess.FREE_PUBLIC_API,
    NewsAccess.FREE_PUBLIC_FEED,
}


@dataclass(frozen=True, slots=True)
class NewsSource:
    source_id: str
    name: str
    access: NewsAccess
    role: SourceRole
    publisher_group: str
    currencies: tuple[str, ...] = ()
    underliers: tuple[str, ...] = ()
    asset_classes: tuple[AssetClass, ...] = ()

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.name.strip() or not self.publisher_group.strip():
            raise ValueError("news source identity and publisher group are required")

    @property
    def automatic_acquisition_allowed(self) -> bool:
        return self.access in _AUTOMATIC_ACCESS


@dataclass(frozen=True, slots=True)
class NewsItem:
    source_id: str
    external_id: str
    published_at: datetime
    known_at: datetime
    headline: str
    summary: str = ""
    currencies: tuple[str, ...] = ()
    underliers: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    event_key: str = ""

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.external_id.strip() or not self.headline.strip():
            raise ValueError("news item requires source, identity, and headline")
        for value in (self.published_at, self.known_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("news timestamps must be timezone-aware")
        if self.known_at < self.published_at:
            raise ValueError("known_at cannot precede published_at")

    @classmethod
    def of(
        cls,
        *,
        source_id: str,
        external_id: str,
        published_at: datetime,
        known_at: datetime,
        headline: str,
        summary: str = "",
        currencies: Iterable[str] = (),
        underliers: Iterable[str] = (),
        topics: Iterable[str] = (),
        event_key: str = "",
    ) -> "NewsItem":
        return cls(
            source_id=source_id,
            external_id=external_id,
            published_at=published_at,
            known_at=known_at,
            headline=headline.strip(),
            summary=summary.strip(),
            currencies=tuple(sorted({item.strip().upper() for item in currencies if item.strip()})),
            underliers=tuple(sorted({item.strip().upper() for item in underliers if item.strip()})),
            topics=tuple(sorted({item.strip().lower() for item in topics if item.strip()})),
            event_key=event_key.strip().lower(),
        )

    @property
    def story_hash(self) -> str:
        normalized = re.sub(r"[^a-z0-9]+", " ", self.headline.lower()).strip()
        return sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SymbolNewsRegistry:
    sources: tuple[NewsSource, ...]

    def source_map(self) -> dict[str, NewsSource]:
        return {source.source_id: source for source in self.sources}

    def eligible_sources(self, profile: SymbolResearchProfile) -> tuple[NewsSource, ...]:
        result = []
        currencies = set(profile.currencies)
        underliers = {profile.market.economic_underlier, *profile.related_underliers}
        allowed_assets = {profile.market.asset_class, *profile.allowed_asset_context}
        for source in self.sources:
            if not source.automatic_acquisition_allowed:
                continue
            if set(source.currencies) & currencies or set(source.underliers) & underliers or set(source.asset_classes) & allowed_assets:
                result.append(source)
        return tuple(sorted(result, key=lambda item: (item.role.value, item.source_id)))


def news_relevant(item: NewsItem, profile: SymbolResearchProfile) -> bool:
    currencies = set(profile.currencies)
    underliers = {profile.market.economic_underlier, *profile.related_underliers}
    return bool(set(item.currencies) & currencies or set(item.underliers) & underliers)


def eligible_news_items(
    items: Iterable[NewsItem],
    registry: SymbolNewsRegistry,
    profile: SymbolResearchProfile,
    *,
    as_of: datetime,
    limit: int = 64,
) -> tuple[NewsItem, ...]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must be timezone-aware")
    if limit < 1:
        raise ValueError("limit must be positive")
    sources = registry.source_map()
    accepted = []
    for item in items:
        source = sources.get(item.source_id)
        if source is None or not source.automatic_acquisition_allowed:
            continue
        if item.known_at > as_of or not news_relevant(item, profile):
            continue
        accepted.append(item)
    accepted.sort(key=lambda item: (-item.known_at.timestamp(), item.source_id, item.external_id))
    return tuple(accepted[:limit])
