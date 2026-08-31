from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Iterable

from .markets import SymbolResearchProfile
from .news import NewsItem, SymbolNewsRegistry, eligible_news_items


class EventKind(StrEnum):
    SCHEDULED = "scheduled"
    UNSCHEDULED = "unscheduled"


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    event_id: str
    scheduled_at: datetime
    known_at: datetime
    currencies: tuple[str, ...]
    category: str
    forecast: str = ""
    previous: str = ""
    actual: str = ""
    revised_previous: str = ""
    source_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id.strip() or not self.category.strip() or not self.source_id.strip():
            raise ValueError("scheduled event requires identity, category, and source")
        for value in (self.scheduled_at, self.known_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("scheduled event timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class EventCapsule:
    capsule_id: str
    target_symbol: str
    event_kind: EventKind
    as_of: datetime
    event_key: str
    currencies: tuple[str, ...]
    scheduled_event: ScheduledEvent | None
    context_items: tuple[NewsItem, ...]
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.capsule_id.strip() or not self.target_symbol.strip() or not self.event_key.strip():
            raise ValueError("event capsule requires identity, target, and event key")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("event capsule as_of must be timezone-aware")
        if self.event_kind is EventKind.SCHEDULED and self.scheduled_event is None:
            raise ValueError("scheduled capsule requires scheduled event")


def reconstruct_scheduled_event(
    event: ScheduledEvent,
    items: Iterable[NewsItem],
    registry: SymbolNewsRegistry,
    profile: SymbolResearchProfile,
    *,
    as_of: datetime,
    capsule_id: str,
    context_limit: int = 32,
) -> EventCapsule:
    """Build a point-in-time event episode without importing future headlines."""
    if event.known_at > as_of:
        raise ValueError("event was not known at requested reconstruction time")
    if not set(event.currencies) & set(profile.currencies):
        raise ValueError("scheduled event is not relevant to target symbol currencies")
    eligible = eligible_news_items(items, registry, profile, as_of=as_of, limit=max(context_limit * 4, context_limit))
    context = []
    for item in eligible:
        if item.event_key and item.event_key != event.event_id.lower():
            continue
        context.append(item)
        if len(context) >= context_limit:
            break
    source_ids = {event.source_id}
    source_ids.update(item.source_id for item in context)
    return EventCapsule(
        capsule_id=capsule_id,
        target_symbol=profile.market.canonical_symbol,
        event_kind=EventKind.SCHEDULED,
        as_of=as_of,
        event_key=event.event_id.lower(),
        currencies=tuple(sorted(set(event.currencies))),
        scheduled_event=event,
        context_items=tuple(context),
        source_ids=tuple(sorted(source_ids)),
    )
