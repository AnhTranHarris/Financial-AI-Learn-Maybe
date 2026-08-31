from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .core import EvidenceItem, EvidenceSnapshot
from .curriculum import MethodInsight, RelevanceTier, relevance, resolve_symbol


def insights_to_snapshot(
    insights: Iterable[MethodInsight],
    *,
    snapshot_id: str,
    target_symbol: str,
    at: datetime,
    related_symbols: tuple[str, ...] = (),
    include_transfer: bool = False,
    limit: int = 16,
) -> EvidenceSnapshot:
    """Translate only point-in-time-valid curriculum into bounded reasoning evidence."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("reasoning time must be timezone-aware")
    if limit < 1:
        raise ValueError("limit must be positive")
    target = resolve_symbol(target_symbol).canonical
    ranked: list[tuple[int, MethodInsight]] = []
    for insight in insights:
        if insight.known_at > at:
            continue
        tier = relevance(target, insight.target_symbol, related_symbols=related_symbols)
        if tier is RelevanceTier.TRANSFER and not include_transfer:
            continue
        rank = {
            RelevanceTier.EXACT: 0,
            RelevanceTier.RELATED: 1,
            RelevanceTier.TRANSFER: 2,
        }[tier]
        ranked.append((rank, insight))
    ranked.sort(key=lambda item: (item[0], -item[1].known_at.timestamp(), item[1].insight_id))
    items = []
    for rank, insight in ranked[:limit]:
        items.append(
            EvidenceItem(
                key=f"curriculum:{insight.insight_id}",
                value={
                    "statement": insight.statement,
                    "concepts": tuple(concept.value for concept in insight.concepts),
                    "features": insight.features,
                    "counterexample": insight.counterexample,
                    "relevance_rank": rank,
                },
                source="curriculum",
                observed_at=insight.known_at,
                category="curriculum",
                provenance=",".join(insight.source_ids),
            )
        )
    return EvidenceSnapshot.of(snapshot_id, items)
