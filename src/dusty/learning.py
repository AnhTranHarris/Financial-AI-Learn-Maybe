from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import StrEnum

from .core import Cognition, GuardianState, PatienceState, SkepticState


class Attribution(StrEnum):
    ANALYST = "analyst"
    SKEPTIC = "skeptic"
    PATIENCE = "patience"
    GUARDIAN = "guardian"
    PROCESS = "process"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ReviewObservation:
    cognition: Cognition
    thesis_correct: bool = True
    contradiction_present: bool = False
    timing_correct: bool = True
    process_ok: bool = True


def attribute(observation: ReviewObservation) -> Attribution:
    """Attribute a synthetic failure without pretending to infer more than observed."""
    c = observation.cognition
    if not observation.process_ok:
        return (
            Attribution.GUARDIAN
            if c.guardian is GuardianState.NORMAL
            else Attribution.PROCESS
        )
    if not observation.thesis_correct:
        return Attribution.ANALYST
    if observation.contradiction_present and c.skeptic is SkepticState.CLEAR:
        return Attribution.SKEPTIC
    if not observation.timing_correct and c.patience is PatienceState.READY:
        return Attribution.PATIENCE
    if c.guardian is GuardianState.STOP:
        return Attribution.GUARDIAN
    return Attribution.UNKNOWN


def summarize(attributions: list[Attribution]) -> dict[Attribution, int]:
    counts = Counter(attributions)
    return {kind: counts[kind] for kind in Attribution}
