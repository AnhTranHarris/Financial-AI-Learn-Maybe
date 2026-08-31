from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Iterable


class AnalystState(StrEnum):
    LONG = "long"
    SHORT = "short"
    NEUTRAL = "neutral"
    UNCLEAR = "unclear"


class SkepticState(StrEnum):
    CLEAR = "clear"
    CONCERN = "concern"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class PatienceState(StrEnum):
    WAIT = "wait"
    READY = "ready"
    COMPLETE = "complete"


class GuardianState(StrEnum):
    NORMAL = "normal"
    CAUTION = "caution"
    STOP = "stop"


class CoherenceState(StrEnum):
    COHERENT = "coherent"
    RESOLVABLE = "resolvable"
    OVERLOADED = "overloaded"
    INCOHERENT = "incoherent"
    INSUFFICIENT = "insufficient"


class ExceptionLevel(StrEnum):
    NONE = "none"
    E1_RECONSIDER = "e1_reconsider"
    E2_ABORT = "e2_abort"
    E3_STAND_DOWN = "e3_stand_down"


class Decision(StrEnum):
    OBSERVE = "observe"
    WAIT = "wait"
    READY = "ready"
    ENTRY_LONG = "entry_long"
    ENTRY_SHORT = "entry_short"
    HOLD = "hold"
    EXIT = "exit"
    ABORT = "abort"
    STAND_DOWN = "stand_down"


class PositionState(StrEnum):
    FLAT = "flat"
    OPEN_LONG = "open_long"
    OPEN_SHORT = "open_short"


class HealthState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


class ReasoningPhase(StrEnum):
    ORIENTING = "orienting"
    PERCEIVING = "perceiving"
    FILTERING = "filtering"
    COHERENCE = "coherence"
    HYPOTHESIS = "hypothesis"
    FALSIFYING = "falsifying"
    WAITING = "waiting"
    VALIDATING = "validating"
    ENTRY_QUALIFIED = "entry_qualified"
    SUPERVISING = "supervising"
    EXIT_QUALIFIED = "exit_qualified"
    REVIEWING = "reviewing"
    LEARNING = "learning"
    STAND_DOWN = "stand_down"


class ReasoningEvent(StrEnum):
    START = "start"
    PERCEIVED = "perceived"
    FILTERED = "filtered"
    COHERENT = "coherent"
    REASSESS = "reassess"
    HYPOTHESIS_READY = "hypothesis_ready"
    FALSIFICATION_PASSED = "falsification_passed"
    WAIT = "wait"
    VALIDATE = "validate"
    ENTRY = "entry"
    POSITION_OPEN = "position_open"
    HOLD = "hold"
    EXIT = "exit"
    REVIEW = "review"
    LEARN = "learn"
    STAND_DOWN = "stand_down"
    RECOVER = "recover"


TRANSITIONS: dict[tuple[ReasoningPhase, ReasoningEvent], ReasoningPhase] = {
    (ReasoningPhase.ORIENTING, ReasoningEvent.START): ReasoningPhase.PERCEIVING,
    (ReasoningPhase.PERCEIVING, ReasoningEvent.PERCEIVED): ReasoningPhase.FILTERING,
    (ReasoningPhase.FILTERING, ReasoningEvent.FILTERED): ReasoningPhase.COHERENCE,
    (ReasoningPhase.COHERENCE, ReasoningEvent.COHERENT): ReasoningPhase.HYPOTHESIS,
    (ReasoningPhase.COHERENCE, ReasoningEvent.REASSESS): ReasoningPhase.PERCEIVING,
    (ReasoningPhase.HYPOTHESIS, ReasoningEvent.HYPOTHESIS_READY): ReasoningPhase.FALSIFYING,
    (ReasoningPhase.FALSIFYING, ReasoningEvent.FALSIFICATION_PASSED): ReasoningPhase.WAITING,
    (ReasoningPhase.FALSIFYING, ReasoningEvent.REASSESS): ReasoningPhase.PERCEIVING,
    (ReasoningPhase.WAITING, ReasoningEvent.WAIT): ReasoningPhase.WAITING,
    (ReasoningPhase.WAITING, ReasoningEvent.VALIDATE): ReasoningPhase.VALIDATING,
    (ReasoningPhase.VALIDATING, ReasoningEvent.WAIT): ReasoningPhase.WAITING,
    (ReasoningPhase.VALIDATING, ReasoningEvent.REASSESS): ReasoningPhase.PERCEIVING,
    (ReasoningPhase.VALIDATING, ReasoningEvent.ENTRY): ReasoningPhase.ENTRY_QUALIFIED,
    (ReasoningPhase.ENTRY_QUALIFIED, ReasoningEvent.POSITION_OPEN): ReasoningPhase.SUPERVISING,
    (ReasoningPhase.SUPERVISING, ReasoningEvent.HOLD): ReasoningPhase.SUPERVISING,
    (ReasoningPhase.SUPERVISING, ReasoningEvent.EXIT): ReasoningPhase.EXIT_QUALIFIED,
    (ReasoningPhase.EXIT_QUALIFIED, ReasoningEvent.REVIEW): ReasoningPhase.REVIEWING,
    (ReasoningPhase.REVIEWING, ReasoningEvent.LEARN): ReasoningPhase.LEARNING,
    (ReasoningPhase.LEARNING, ReasoningEvent.START): ReasoningPhase.PERCEIVING,
    (ReasoningPhase.STAND_DOWN, ReasoningEvent.RECOVER): ReasoningPhase.ORIENTING,
}


def advance(phase: ReasoningPhase, event: ReasoningEvent) -> ReasoningPhase:
    """Return the only legal next phase for ``phase + event``."""
    try:
        return TRANSITIONS[(phase, event)]
    except KeyError as exc:
        raise ValueError(f"illegal reasoning transition: {phase} + {event}") from exc


@dataclass(frozen=True, slots=True)
class Cognition:
    analyst: AnalystState
    skeptic: SkepticState
    patience: PatienceState
    guardian: GuardianState


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    key: str
    value: Any
    source: str
    observed_at: datetime
    valid_until: datetime | None = None
    category: str = "general"
    provenance: str = ""
    confidence: float | None = None
    relevant: bool = True

    def is_fresh(self, at: datetime) -> bool:
        return self.valid_until is None or at <= self.valid_until


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    snapshot_id: str
    items: tuple[EvidenceItem, ...]

    @classmethod
    def of(cls, snapshot_id: str, items: Iterable[EvidenceItem]) -> "EvidenceSnapshot":
        return cls(snapshot_id, tuple(items))


@dataclass(frozen=True, slots=True)
class CoherenceResult:
    state: CoherenceState
    reasons: tuple[str, ...] = ()


@dataclass(slots=True)
class Person:
    person_id: str
    symbol: str
    strategy_id: str
    phase: ReasoningPhase = ReasoningPhase.ORIENTING
    position: PositionState = PositionState.FLAT
    hypothesis_id: str = ""
    health: HealthState = HealthState.HEALTHY

    def move(self, event: ReasoningEvent) -> tuple[ReasoningPhase, ReasoningPhase]:
        previous = self.phase
        if event is ReasoningEvent.STAND_DOWN:
            self.phase = ReasoningPhase.STAND_DOWN
        else:
            self.phase = advance(self.phase, event)
        return previous, self.phase

    def reason(self, cognition: Cognition, coherence: CoherenceResult) -> Decision:
        """Return a semantic decision without mutating broker/execution truth."""
        exception = assess_exception(coherence, self.health)
        return exception_decision(exception, self.position) or synthesize(
            cognition, self.position
        )


def check_coherence(
    snapshot: EvidenceSnapshot,
    *,
    at: datetime | None = None,
    required_keys: Iterable[str] = (),
    max_items: int = 32,
) -> CoherenceResult:
    """Classify evidence usability with deterministic, strategy-neutral rules."""
    at = at or datetime.now(timezone.utc)
    relevant = tuple(item for item in snapshot.items if item.relevant)
    if not relevant:
        return CoherenceResult(CoherenceState.INSUFFICIENT, ("no_relevant_evidence",))
    if len(relevant) > max_items:
        return CoherenceResult(CoherenceState.OVERLOADED, ("item_budget_exceeded",))

    fresh = tuple(item for item in relevant if item.is_fresh(at))
    if not fresh:
        return CoherenceResult(CoherenceState.INSUFFICIENT, ("all_evidence_stale",))

    required = set(required_keys)
    available = {item.key for item in fresh}
    missing = sorted(required - available)
    if missing:
        return CoherenceResult(
            CoherenceState.INSUFFICIENT,
            tuple(f"missing:{key}" for key in missing),
        )

    values: dict[str, set[str]] = {}
    duplicate = False
    for item in fresh:
        marker = repr(item.value)
        bucket = values.setdefault(item.key, set())
        duplicate = duplicate or marker in bucket
        bucket.add(marker)

    conflicts = sorted(key for key, seen in values.items() if len(seen) > 1)
    if conflicts:
        return CoherenceResult(
            CoherenceState.INCOHERENT,
            tuple(f"conflict:{key}" for key in conflicts),
        )

    stale_count = len(relevant) - len(fresh)
    if duplicate or stale_count:
        reasons = []
        if duplicate:
            reasons.append("redundant_evidence")
        if stale_count:
            reasons.append(f"stale:{stale_count}")
        return CoherenceResult(CoherenceState.RESOLVABLE, tuple(reasons))
    return CoherenceResult(CoherenceState.COHERENT)


def assess_exception(
    coherence: CoherenceResult,
    health: HealthState = HealthState.HEALTHY,
) -> ExceptionLevel:
    if health is HealthState.FAILED:
        return ExceptionLevel.E3_STAND_DOWN
    if coherence.state is CoherenceState.INCOHERENT:
        return ExceptionLevel.E2_ABORT
    if coherence.state in {CoherenceState.OVERLOADED, CoherenceState.INSUFFICIENT}:
        return ExceptionLevel.E1_RECONSIDER
    return ExceptionLevel.NONE


def exception_decision(
    level: ExceptionLevel,
    position: PositionState,
) -> Decision | None:
    if level is ExceptionLevel.NONE:
        return None
    if level is ExceptionLevel.E1_RECONSIDER:
        return Decision.OBSERVE
    if level is ExceptionLevel.E2_ABORT:
        return Decision.EXIT if position is not PositionState.FLAT else Decision.ABORT
    return Decision.EXIT if position is not PositionState.FLAT else Decision.STAND_DOWN


def recovery_ready(
    health: HealthState,
    coherence: CoherenceResult,
) -> bool:
    return health is HealthState.HEALTHY and coherence.state in {
        CoherenceState.COHERENT,
        CoherenceState.RESOLVABLE,
    }


def synthesize(c: Cognition, position: PositionState) -> Decision:
    """Collapse four cognitive functions into one semantic decision."""
    if position is PositionState.FLAT:
        if c.guardian is GuardianState.STOP:
            return Decision.STAND_DOWN
        if c.skeptic is SkepticState.INVALID:
            return Decision.ABORT
        if c.analyst in {AnalystState.NEUTRAL, AnalystState.UNCLEAR}:
            return Decision.OBSERVE
        if c.guardian is GuardianState.CAUTION or c.skeptic in {
            SkepticState.CONCERN,
            SkepticState.UNKNOWN,
        }:
            return Decision.WAIT
        if c.patience is PatienceState.WAIT:
            return Decision.WAIT
        if c.patience is PatienceState.COMPLETE:
            return Decision.OBSERVE
        return (
            Decision.ENTRY_LONG
            if c.analyst is AnalystState.LONG
            else Decision.ENTRY_SHORT
        )

    if c.guardian is GuardianState.STOP:
        return Decision.EXIT
    if c.skeptic is SkepticState.INVALID or c.patience is PatienceState.COMPLETE:
        return Decision.EXIT
    if position is PositionState.OPEN_LONG and c.analyst is AnalystState.SHORT:
        return Decision.EXIT
    if position is PositionState.OPEN_SHORT and c.analyst is AnalystState.LONG:
        return Decision.EXIT
    return Decision.HOLD
