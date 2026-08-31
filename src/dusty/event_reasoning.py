from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .core import EvidenceItem, EvidenceSnapshot
from .scenario import ScenarioHypothesis


def scenarios_to_snapshot(
    scenarios: Iterable[ScenarioHypothesis],
    *,
    snapshot_id: str,
    target_symbol: str,
    at: datetime,
    limit: int = 8,
) -> EvidenceSnapshot:
    """Bound conditional macro scenarios into evidence; scenarios never directly authorize trading."""
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("reasoning time must be timezone-aware")
    if limit < 1:
        raise ValueError("limit must be positive")
    target = target_symbol.strip().upper()
    eligible = [item for item in scenarios if item.target_symbol == target and item.known_at <= at]
    eligible.sort(key=lambda item: (-item.known_at.timestamp(), item.scenario_id))
    evidence = []
    for scenario in eligible[:limit]:
        evidence.append(
            EvidenceItem(
                key=f"scenario:{scenario.scenario_id}",
                value={
                    "event_key": scenario.event_key,
                    "state": scenario.state.value,
                    "premise": scenario.premise,
                    "transmission": tuple(item.value for item in scenario.transmission),
                    "confirmations": scenario.confirmations,
                    "invalidations": scenario.invalidations,
                    "corroboration": scenario.corroboration.value,
                    "broker_write_authorized": False,
                },
                source="event_intelligence",
                observed_at=scenario.known_at,
                category="scenario",
                provenance=",".join(scenario.source_ids),
            )
        )
    return EvidenceSnapshot.of(snapshot_id, evidence)
