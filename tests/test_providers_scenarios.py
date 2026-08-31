from __future__ import annotations

import unittest
from dataclasses import dataclass
from datetime import datetime, timezone

from dusty import (
    AnalystState as A,
    Cognition,
    CoherenceResult,
    CoherenceState,
    Decision,
    GuardianState as G,
    PatienceState as P,
    Person,
    PositionState,
    SkepticState as S,
)
from dusty.providers import (
    chronos_adapter,
    collect_snapshot,
    kronos_adapter,
    moirai_adapter,
    vibe_adapter,
)


NOW = datetime(2026, 8, 30, 20, 0, tzinfo=timezone.utc)


class ProviderTests(unittest.TestCase):
    def test_model_adapters_share_one_shape(self):
        collector = lambda symbol, at: {"direction": "up", "symbol": symbol}
        providers = [
            kronos_adapter(collector),
            chronos_adapter(collector),
            moirai_adapter(collector),
            vibe_adapter(collector),
        ]
        result = collect_snapshot("s1", providers, "EURUSD", NOW)
        self.assertEqual(len(result.snapshot.items), 8)
        self.assertFalse(result.errors)

    def test_provider_failure_is_isolated(self):
        def broken(symbol, at):
            raise RuntimeError("offline")

        good = kronos_adapter(lambda symbol, at: {"direction": "up"})
        bad = chronos_adapter(broken)
        result = collect_snapshot("s2", [good, bad], "EURUSD", NOW)
        self.assertEqual(len(result.snapshot.items), 1)
        self.assertEqual(result.errors, ("chronos:RuntimeError",))


@dataclass(frozen=True)
class Scenario:
    name: str
    cognition: Cognition
    coherence: CoherenceState
    position: PositionState
    expected: Decision


class SyntheticMarketLabTests(unittest.TestCase):
    def test_m11_scenario_matrix(self):
        cases = [
            Scenario("clear_long", Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL), CoherenceState.COHERENT, PositionState.FLAT, Decision.ENTRY_LONG),
            Scenario("clear_short", Cognition(A.SHORT, S.CLEAR, P.READY, G.NORMAL), CoherenceState.COHERENT, PositionState.FLAT, Decision.ENTRY_SHORT),
            Scenario("timing_early", Cognition(A.LONG, S.CLEAR, P.WAIT, G.NORMAL), CoherenceState.COHERENT, PositionState.FLAT, Decision.WAIT),
            Scenario("skeptic_concern", Cognition(A.LONG, S.CONCERN, P.READY, G.NORMAL), CoherenceState.COHERENT, PositionState.FLAT, Decision.WAIT),
            Scenario("neutral", Cognition(A.NEUTRAL, S.CLEAR, P.READY, G.NORMAL), CoherenceState.COHERENT, PositionState.FLAT, Decision.OBSERVE),
            Scenario("contradictory", Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL), CoherenceState.INCOHERENT, PositionState.FLAT, Decision.ABORT),
            Scenario("insufficient", Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL), CoherenceState.INSUFFICIENT, PositionState.FLAT, Decision.OBSERVE),
            Scenario("overloaded", Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL), CoherenceState.OVERLOADED, PositionState.FLAT, Decision.OBSERVE),
            Scenario("hold_winner", Cognition(A.LONG, S.CLEAR, P.WAIT, G.NORMAL), CoherenceState.COHERENT, PositionState.OPEN_LONG, Decision.HOLD),
            Scenario("thesis_reversal", Cognition(A.SHORT, S.CLEAR, P.WAIT, G.NORMAL), CoherenceState.COHERENT, PositionState.OPEN_LONG, Decision.EXIT),
            Scenario("lifecycle_complete", Cognition(A.LONG, S.CLEAR, P.COMPLETE, G.NORMAL), CoherenceState.COHERENT, PositionState.OPEN_LONG, Decision.EXIT),
            Scenario("guardian_stop_flat", Cognition(A.LONG, S.CLEAR, P.READY, G.STOP), CoherenceState.COHERENT, PositionState.FLAT, Decision.STAND_DOWN),
            Scenario("guardian_stop_open", Cognition(A.LONG, S.CLEAR, P.WAIT, G.STOP), CoherenceState.COHERENT, PositionState.OPEN_LONG, Decision.EXIT),
        ]
        for case in cases:
            with self.subTest(case=case.name):
                person = Person("p", "EURUSD", "strategy", position=case.position)
                decision = person.reason(case.cognition, CoherenceResult(case.coherence))
                self.assertIs(decision, case.expected)

    def test_identical_input_is_reproducible(self):
        cognition = Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL)
        coherence = CoherenceResult(CoherenceState.COHERENT)
        outcomes = [Person("p", "EURUSD", "s").reason(cognition, coherence) for _ in range(100)]
        self.assertEqual(set(outcomes), {Decision.ENTRY_LONG})


if __name__ == "__main__":
    unittest.main()
