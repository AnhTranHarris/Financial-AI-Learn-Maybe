from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping

from .core import EvidenceItem, EvidenceSnapshot
from .ports import EvidenceProvider


Collector = Callable[[str, datetime], Mapping[str, Any] | Iterable[EvidenceItem]]


@dataclass(frozen=True, slots=True)
class CallableProvider:
    """One adapter shape for Kronos, Chronos, Moirai, Vibe, and future providers."""

    name: str
    category: str
    collector: Collector

    def collect(self, symbol: str, at: datetime) -> Iterable[EvidenceItem]:
        result = self.collector(symbol, at)
        if isinstance(result, Mapping):
            return tuple(
                EvidenceItem(
                    key=key,
                    value=value,
                    source=self.name,
                    observed_at=at,
                    category=self.category,
                    provenance=f"adapter:{self.name}",
                )
                for key, value in result.items()
            )
        return tuple(result)


@dataclass(frozen=True, slots=True)
class CollectionResult:
    snapshot: EvidenceSnapshot
    errors: tuple[str, ...] = ()


def collect_snapshot(
    snapshot_id: str,
    providers: Iterable[EvidenceProvider],
    symbol: str,
    at: datetime,
) -> CollectionResult:
    items: list[EvidenceItem] = []
    errors: list[str] = []
    for provider in providers:
        try:
            items.extend(provider.collect(symbol, at))
        except Exception as exc:  # provider isolation is intentional at this boundary
            errors.append(f"{provider.name}:{type(exc).__name__}")
    return CollectionResult(EvidenceSnapshot.of(snapshot_id, items), tuple(errors))


def kronos_adapter(collector: Collector) -> CallableProvider:
    return CallableProvider("kronos", "forecast", collector)


def chronos_adapter(collector: Collector) -> CallableProvider:
    return CallableProvider("chronos", "forecast", collector)


def moirai_adapter(collector: Collector) -> CallableProvider:
    return CallableProvider("moirai", "forecast", collector)


def vibe_adapter(collector: Collector) -> CallableProvider:
    return CallableProvider("vibe", "research", collector)
