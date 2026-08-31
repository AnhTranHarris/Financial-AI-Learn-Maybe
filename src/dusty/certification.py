from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from itertools import product

from .core import (
    TRANSITIONS,
    AnalystState,
    Cognition,
    CoherenceResult,
    CoherenceState,
    GuardianState,
    HealthState,
    PatienceState,
    Person,
    PositionState,
    ReasoningEvent,
    ReasoningPhase,
    SkepticState,
    advance,
)


@dataclass(frozen=True, slots=True)
class ReasoningCertification:
    cases: int
    legal_transitions: int
    illegal_transitions: int
    fingerprint: str
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass(frozen=True, slots=True)
class ResearchGateInput:
    reasoning_certified: bool
    provenance_complete: bool
    point_in_time_clean: bool
    experiments_reproducible: bool
    memory_integrity: bool
    forecast_comparison_complete: bool


@dataclass(frozen=True, slots=True)
class ResearchQualification:
    ready_for_shadow_research: bool
    broker_write_authorized: bool
    reasons: tuple[str, ...] = ()


def certify_reasoning_core() -> ReasoningCertification:
    """Exhaust the finite v1 semantic space and fingerprint its behavior."""
    failures: list[str] = []
    digest = sha256()
    cases = 0

    for analyst, skeptic, patience, guardian, coherence, position, health in product(
        AnalystState,
        SkepticState,
        PatienceState,
        GuardianState,
        CoherenceState,
        PositionState,
        HealthState,
    ):
        cognition = Cognition(analyst, skeptic, patience, guardian)
        coherence_result = CoherenceResult(coherence)
        left = Person("cert-left", "EURUSD", "cert", position=position, health=health)
        right = Person("cert-right", "EURUSD", "cert", position=position, health=health)
        left_decision = left.reason(cognition, coherence_result)
        right_decision = right.reason(cognition, coherence_result)
        cases += 1

        if left_decision is not right_decision:
            failures.append(f"nondeterministic:{cases}")
        if left.position is not position or right.position is not position:
            failures.append(f"execution_truth_mutated:{cases}")

        digest.update(
            "|".join(
                (
                    analyst.value,
                    skeptic.value,
                    patience.value,
                    guardian.value,
                    coherence.value,
                    position.value,
                    health.value,
                    left_decision.value,
                )
            ).encode("utf-8")
        )

    legal = 0
    illegal = 0
    for phase, event in product(ReasoningPhase, ReasoningEvent):
        if event is ReasoningEvent.STAND_DOWN:
            continue
        expected = TRANSITIONS.get((phase, event))
        if expected is None:
            illegal += 1
            try:
                advance(phase, event)
            except ValueError:
                pass
            else:
                failures.append(f"illegal_transition_accepted:{phase.value}:{event.value}")
        else:
            legal += 1
            actual = advance(phase, event)
            if actual is not expected:
                failures.append(f"transition_mismatch:{phase.value}:{event.value}")
            digest.update(f"{phase.value}|{event.value}|{actual.value}".encode("utf-8"))

    return ReasoningCertification(
        cases=cases,
        legal_transitions=legal,
        illegal_transitions=illegal,
        fingerprint=digest.hexdigest(),
        failures=tuple(failures),
    )


def qualify_research(inputs: ResearchGateInput) -> ResearchQualification:
    """M23 pre-execution gate. Passing never grants broker-write authority."""
    checks = {
        "reasoning_not_certified": inputs.reasoning_certified,
        "provenance_incomplete": inputs.provenance_complete,
        "point_in_time_violation": inputs.point_in_time_clean,
        "experiments_not_reproducible": inputs.experiments_reproducible,
        "memory_integrity_failed": inputs.memory_integrity,
        "forecast_comparison_incomplete": inputs.forecast_comparison_complete,
    }
    reasons = tuple(reason for reason, passed in checks.items() if not passed)
    return ResearchQualification(
        ready_for_shadow_research=not reasons,
        broker_write_authorized=False,
        reasons=reasons,
    )
