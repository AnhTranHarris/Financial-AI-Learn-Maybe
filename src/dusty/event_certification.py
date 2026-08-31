from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventIntelligenceGateInput:
    m45_ready: bool
    market_identity_certified: bool
    free_source_policy_certified: bool
    scheduled_event_pit_clean: bool
    unscheduled_clustering_certified: bool
    source_independence_certified: bool
    scenario_falsification_ready: bool
    reaction_research_certified: bool
    strategy_event_research_certified: bool
    event_reasoning_bridge_certified: bool
    source_value_gate_certified: bool


@dataclass(frozen=True, slots=True)
class EventIntelligenceQualification:
    ready_for_demo_execution_development: bool
    broker_write_authorized: bool
    reasons: tuple[str, ...] = ()


def qualify_event_intelligence(inputs: EventIntelligenceGateInput) -> EventIntelligenceQualification:
    """M55 gate: macro/event intelligence may support later demo engineering, never broker writes."""
    checks = {
        "m45_not_ready": inputs.m45_ready,
        "market_identity_not_certified": inputs.market_identity_certified,
        "free_source_policy_not_certified": inputs.free_source_policy_certified,
        "scheduled_event_point_in_time_failed": inputs.scheduled_event_pit_clean,
        "unscheduled_clustering_not_certified": inputs.unscheduled_clustering_certified,
        "source_independence_not_certified": inputs.source_independence_certified,
        "scenario_falsification_not_ready": inputs.scenario_falsification_ready,
        "reaction_research_not_certified": inputs.reaction_research_certified,
        "strategy_event_research_not_certified": inputs.strategy_event_research_certified,
        "event_reasoning_bridge_not_certified": inputs.event_reasoning_bridge_certified,
        "source_value_gate_not_certified": inputs.source_value_gate_certified,
    }
    reasons = tuple(reason for reason, passed in checks.items() if not passed)
    return EventIntelligenceQualification(
        ready_for_demo_execution_development=not reasons,
        broker_write_authorized=False,
        reasons=reasons,
    )
