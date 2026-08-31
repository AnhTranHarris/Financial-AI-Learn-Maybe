from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty import (
    AnalystState as A,
    Cognition,
    CoherenceResult,
    CoherenceState,
    Decision,
    EvidenceItem,
    EvidenceSnapshot,
    GuardianState as G,
    HealthState,
    PatienceState as P,
    Person,
    PositionState,
    ReasoningEvent as Event,
    ReasoningPhase as Phase,
    SkepticState as S,
    advance,
    assess_exception,
    check_coherence,
    recovery_ready,
    synthesize,
)
from dusty.core import ExceptionLevel, TRANSITIONS


NOW = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)


def item(key: str, value: object, *, source: str = "test", age: int = 0, ttl: int = 60):
    observed = NOW - timedelta(seconds=age)
    return EvidenceItem(
        key=key,
        value=value,
        source=source,
        observed_at=observed,
        valid_until=observed + timedelta(seconds=ttl),
    )


class LifecycleTests(unittest.TestCase):
    def test_full_legal_cycle(self):
        sequence = [
            (Phase.ORIENTING, Event.START, Phase.PERCEIVING),
            (Phase.PERCEIVING, Event.PERCEIVED, Phase.FILTERING),
            (Phase.FILTERING, Event.FILTERED, Phase.COHERENCE),
            (Phase.COHERENCE, Event.COHERENT, Phase.HYPOTHESIS),
            (Phase.HYPOTHESIS, Event.HYPOTHESIS_READY, Phase.FALSIFYING),
            (Phase.FALSIFYING, Event.FALSIFICATION_PASSED, Phase.WAITING),
            (Phase.WAITING, Event.VALIDATE, Phase.VALIDATING),
            (Phase.VALIDATING, Event.ENTRY, Phase.ENTRY_QUALIFIED),
            (Phase.ENTRY_QUALIFIED, Event.POSITION_OPEN, Phase.SUPERVISING),
            (Phase.SUPERVISING, Event.HOLD, Phase.SUPERVISING),
            (Phase.SUPERVISING, Event.EXIT, Phase.EXIT_QUALIFIED),
            (Phase.EXIT_QUALIFIED, Event.REVIEW, Phase.REVIEWING),
            (Phase.REVIEWING, Event.LEARN, Phase.LEARNING),
            (Phase.LEARNING, Event.START, Phase.PERCEIVING),
        ]
        for current, event, expected in sequence:
            self.assertIs(advance(current, event), expected)

    def test_transition_table_has_total_contract_coverage(self):
        for (phase, event), expected in TRANSITIONS.items():
            with self.subTest(phase=phase, event=event):
                self.assertIs(advance(phase, event), expected)
        for phase in Phase:
            for event in Event:
                if event is Event.STAND_DOWN or (phase, event) in TRANSITIONS:
                    continue
                with self.subTest(illegal_phase=phase, illegal_event=event):
                    with self.assertRaises(ValueError):
                        advance(phase, event)

    def test_illegal_transition_fails_loudly(self):
        with self.assertRaises(ValueError):
            advance(Phase.ORIENTING, Event.ENTRY)

    def test_stand_down_interrupts_every_phase_and_recovers(self):
        for phase in Phase:
            if phase is Phase.STAND_DOWN:
                continue
            person = Person("p", "EURUSD", "s", phase=phase)
            previous, new = person.move(Event.STAND_DOWN)
            self.assertIs(previous, phase)
            self.assertIs(new, Phase.STAND_DOWN)
            person.move(Event.RECOVER)
            self.assertIs(person.phase, Phase.ORIENTING)

    def test_stand_down_and_recover(self):
        person = Person("p", "EURUSD", "s")
        previous, new = person.move(Event.STAND_DOWN)
        self.assertIs(previous, Phase.ORIENTING)
        self.assertIs(new, Phase.STAND_DOWN)
        person.move(Event.RECOVER)
        self.assertIs(person.phase, Phase.ORIENTING)


class DecisionTests(unittest.TestCase):
    def test_entry_requires_four_way_permission(self):
        ready = Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL)
        self.assertIs(synthesize(ready, PositionState.FLAT), Decision.ENTRY_LONG)
        self.assertIs(
            synthesize(Cognition(A.LONG, S.CONCERN, P.READY, G.NORMAL), PositionState.FLAT),
            Decision.WAIT,
        )
        self.assertIs(
            synthesize(Cognition(A.LONG, S.CLEAR, P.WAIT, G.NORMAL), PositionState.FLAT),
            Decision.WAIT,
        )
        self.assertIs(
            synthesize(Cognition(A.LONG, S.CLEAR, P.READY, G.STOP), PositionState.FLAT),
            Decision.STAND_DOWN,
        )

    def test_hold_and_exit_are_asymmetric(self):
        hold = Cognition(A.LONG, S.CONCERN, P.WAIT, G.CAUTION)
        self.assertIs(synthesize(hold, PositionState.OPEN_LONG), Decision.HOLD)
        self.assertIs(
            synthesize(Cognition(A.LONG, S.INVALID, P.WAIT, G.NORMAL), PositionState.OPEN_LONG),
            Decision.EXIT,
        )
        self.assertIs(
            synthesize(Cognition(A.LONG, S.CLEAR, P.COMPLETE, G.NORMAL), PositionState.OPEN_LONG),
            Decision.EXIT,
        )
        self.assertIs(
            synthesize(Cognition(A.SHORT, S.CLEAR, P.WAIT, G.NORMAL), PositionState.OPEN_LONG),
            Decision.EXIT,
        )

    def test_semantic_decisions_do_not_mutate_position_truth(self):
        coherent = CoherenceResult(CoherenceState.COHERENT)
        person = Person("p", "EURUSD", "s")
        self.assertIs(
            person.reason(Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL), coherent),
            Decision.ENTRY_LONG,
        )
        self.assertIs(person.position, PositionState.FLAT)

        person.position = PositionState.OPEN_LONG
        self.assertIs(
            person.reason(Cognition(A.SHORT, S.CLEAR, P.WAIT, G.NORMAL), coherent),
            Decision.EXIT,
        )
        self.assertIs(person.position, PositionState.OPEN_LONG)


class CoherenceAndExceptionTests(unittest.TestCase):
    def test_coherence_states(self):
        coherent = EvidenceSnapshot.of("ok", [item("trend", "up"), item("vol", "normal")])
        self.assertIs(check_coherence(coherent, at=NOW).state, CoherenceState.COHERENT)

        duplicate = EvidenceSnapshot.of(
            "dup", [item("trend", "up", source="a"), item("trend", "up", source="b")]
        )
        self.assertIs(check_coherence(duplicate, at=NOW).state, CoherenceState.RESOLVABLE)

        conflict = EvidenceSnapshot.of(
            "bad", [item("trend", "up", source="a"), item("trend", "down", source="b")]
        )
        self.assertIs(check_coherence(conflict, at=NOW).state, CoherenceState.INCOHERENT)

        stale = EvidenceSnapshot.of("stale", [item("trend", "up", age=120, ttl=30)])
        self.assertIs(check_coherence(stale, at=NOW).state, CoherenceState.INSUFFICIENT)

        overloaded = EvidenceSnapshot.of("many", [item(str(i), i) for i in range(3)])
        self.assertIs(
            check_coherence(overloaded, at=NOW, max_items=2).state,
            CoherenceState.OVERLOADED,
        )

        missing = check_coherence(coherent, at=NOW, required_keys=("trend", "spread"))
        self.assertIs(missing.state, CoherenceState.INSUFFICIENT)
        self.assertIn("missing:spread", missing.reasons)

    def test_exception_severity_and_recovery(self):
        self.assertIs(
            assess_exception(CoherenceResult(CoherenceState.INCOHERENT)),
            ExceptionLevel.E2_ABORT,
        )
        self.assertIs(
            assess_exception(CoherenceResult(CoherenceState.COHERENT), HealthState.FAILED),
            ExceptionLevel.E3_STAND_DOWN,
        )
        self.assertTrue(recovery_ready(HealthState.HEALTHY, CoherenceResult(CoherenceState.COHERENT)))
        self.assertFalse(recovery_ready(HealthState.DEGRADED, CoherenceResult(CoherenceState.COHERENT)))

    def test_person_exception_override(self):
        person = Person("p", "EURUSD", "s")
        c = Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL)
        bad = CoherenceResult(CoherenceState.INCOHERENT)
        self.assertIs(person.reason(c, bad), Decision.ABORT)
        person.position = PositionState.OPEN_LONG
        self.assertIs(person.reason(c, bad), Decision.EXIT)
        self.assertIs(person.position, PositionState.OPEN_LONG)

    def test_e1_reconsider_preserves_open_position_truth(self):
        person = Person("p", "EURUSD", "s", position=PositionState.OPEN_LONG)
        decision = person.reason(
            Cognition(A.LONG, S.CLEAR, P.WAIT, G.NORMAL),
            CoherenceResult(CoherenceState.INSUFFICIENT),
        )
        self.assertIs(decision, Decision.OBSERVE)
        self.assertIs(person.position, PositionState.OPEN_LONG)


if __name__ == "__main__":
    unittest.main()
