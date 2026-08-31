from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dusty import (
    AnalystState as A,
    Cognition,
    CoherenceState,
    Decision,
    GuardianState as G,
    PatienceState as P,
    ReasoningEvent as Event,
    ReasoningPhase as Phase,
    SkepticState as S,
)
from dusty.core import ExceptionLevel
from dusty.journal import JournalRecord, SQLiteJournal, replay
from dusty.learning import Attribution, ReviewObservation, attribute, summarize


def record(event: Event, previous: Phase, new: Phase, decision: Decision) -> JournalRecord:
    return JournalRecord(
        timestamp="2026-08-30T20:00:00+00:00",
        person_id="person-1",
        symbol="EURUSD",
        strategy_id="strategy-1",
        snapshot_id="snap-1",
        analyst=A.LONG,
        skeptic=S.CLEAR,
        patience=P.READY,
        guardian=G.NORMAL,
        coherence=CoherenceState.COHERENT,
        exception=ExceptionLevel.NONE,
        hypothesis_id="h-1",
        decision=decision,
        event=event,
        previous_phase=previous,
        new_phase=new,
        reason_codes=("synthetic",),
    )


class JournalTests(unittest.TestCase):
    def test_sqlite_roundtrip_and_semantic_replay(self):
        records = [
            record(Event.START, Phase.ORIENTING, Phase.PERCEIVING, Decision.OBSERVE),
            record(Event.PERCEIVED, Phase.PERCEIVING, Phase.FILTERING, Decision.OBSERVE),
            record(Event.FILTERED, Phase.FILTERING, Phase.COHERENCE, Decision.OBSERVE),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            journal = SQLiteJournal(Path(tmp) / "journal.db")
            for entry in records:
                journal.append(entry)
            loaded = journal.records("person-1")
            self.assertEqual(records, loaded)
            self.assertEqual(replay(records), replay(loaded))
            self.assertTrue(journal.integrity_ok())
            journal.close()

    def test_replay_rejects_discontinuity(self):
        records = [
            record(Event.START, Phase.ORIENTING, Phase.PERCEIVING, Decision.OBSERVE),
            record(Event.FILTERED, Phase.FILTERING, Phase.COHERENCE, Decision.OBSERVE),
        ]
        with self.assertRaises(ValueError):
            replay(records)


class LearningTests(unittest.TestCase):
    def test_attribution_is_small_and_explicit(self):
        good = Cognition(A.LONG, S.CLEAR, P.READY, G.NORMAL)
        self.assertIs(attribute(ReviewObservation(good, thesis_correct=False)), Attribution.ANALYST)
        self.assertIs(
            attribute(ReviewObservation(good, contradiction_present=True)), Attribution.SKEPTIC
        )
        self.assertIs(attribute(ReviewObservation(good, timing_correct=False)), Attribution.PATIENCE)
        self.assertIs(attribute(ReviewObservation(good, process_ok=False)), Attribution.GUARDIAN)
        counts = summarize([Attribution.ANALYST, Attribution.ANALYST, Attribution.UNKNOWN])
        self.assertEqual(counts[Attribution.ANALYST], 2)
        self.assertEqual(counts[Attribution.UNKNOWN], 1)


if __name__ == "__main__":
    unittest.main()
