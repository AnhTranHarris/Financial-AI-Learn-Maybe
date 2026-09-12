from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.research_events import (
    EVENT_EVIDENCE_PROTOCOL,
    ResearchEventEvidence,
    ResearchScheduledEvent,
    bind_event_exclusions,
    event_evidence_from_json,
)
from dusty.runtime import RuntimeBar

UTC = timezone.utc
SHA = "a" * 64


def bar(at: datetime) -> RuntimeBar:
    return RuntimeBar.of(at, open=1.0, high=1.1, low=0.9, close=1.0, features={})


class ResearchEventEvidenceTests(unittest.TestCase):
    def event(self, at: datetime, *, currencies=("USD",), known_delta=timedelta(days=7), event_id="e1") -> ResearchScheduledEvent:
        return ResearchScheduledEvent(event_id, at, at - known_delta, currencies, "official_archive", SHA)

    def evidence(self, events=()) -> ResearchEventEvidence:
        return ResearchEventEvidence(
            "EURUSD",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2027, 1, 1, tzinfo=UTC),
            tuple(events),
        )

    def test_protocol_is_content_addressed_and_authority_free(self):
        evidence = self.evidence((self.event(datetime(2026, 6, 1, 12, 30, tzinfo=UTC)),))
        self.assertEqual(evidence.payload["protocol"], EVENT_EVIDENCE_PROTOCOL)
        self.assertFalse(evidence.payload["authority"]["broker_write"])
        self.assertFalse(evidence.payload["authority"]["custody_write"])
        self.assertEqual(evidence.fingerprint, self.evidence(evidence.events).fingerprint)

    def test_unknown_at_event_time_fails_closed(self):
        at = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "not known"):
            ResearchScheduledEvent("e1", at, at + timedelta(seconds=1), ("USD",), "source", SHA)

    def test_duplicate_identity_and_incomplete_coverage_fail_closed(self):
        at = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        row = self.event(at)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.evidence((row, row))
        narrow = ResearchEventEvidence("EURUSD", at - timedelta(hours=1), at + timedelta(hours=1), (row,))
        rows = (bar(at - timedelta(hours=2)), bar(at))
        with self.assertRaisesRegex(ValueError, "does not fully cover"):
            bind_event_exclusions(rows, narrow, exclusion_minutes=30)

    def test_exclusion_adjusted_edge_coverage_is_required(self):
        first = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        last = first + timedelta(minutes=15)
        rows = (bar(first), bar(last))
        evidence = ResearchEventEvidence(
            "EURUSD",
            first,
            last + timedelta(minutes=31),
            (),
        )
        with self.assertRaisesRegex(ValueError, "exclusion-adjusted"):
            bind_event_exclusions(rows, evidence, exclusion_minutes=30)

    def test_relevant_currency_is_blocked_symmetrically(self):
        event_at = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        evidence = self.evidence((self.event(event_at),))
        rows = tuple(bar(event_at + timedelta(minutes=offset)) for offset in (-31, -30, 0, 30, 31))
        bound = bind_event_exclusions(rows, evidence, exclusion_minutes=30, expected_symbol="EURUSD")
        self.assertEqual([row.event_blocked for row in bound], [False, True, True, True, False])

    def test_pre_event_bar_is_not_blocked_before_schedule_was_known(self):
        event_at = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        evidence = self.evidence((self.event(event_at, known_delta=timedelta(minutes=10)),))
        rows = (
            bar(event_at - timedelta(minutes=30)),
            bar(event_at - timedelta(minutes=10)),
            bar(event_at),
        )
        bound = bind_event_exclusions(rows, evidence, exclusion_minutes=30)
        self.assertEqual([row.event_blocked for row in bound], [False, True, True])

    def test_expected_symbol_mismatch_fails_closed(self):
        event_at = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        with self.assertRaisesRegex(ValueError, "symbol"):
            bind_event_exclusions(
                (bar(event_at),),
                self.evidence((self.event(event_at),)),
                exclusion_minutes=30,
                expected_symbol="GBPUSD",
            )

    def test_non_chronological_runtime_fails_closed(self):
        event_at = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        rows = (bar(event_at), bar(event_at - timedelta(minutes=15)))
        with self.assertRaisesRegex(ValueError, "chronological"):
            bind_event_exclusions(rows, self.evidence(), exclusion_minutes=30)

    def test_irrelevant_currency_does_not_block(self):
        event_at = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        evidence = self.evidence((self.event(event_at, currencies=("JPY",)),))
        bound = bind_event_exclusions((bar(event_at),), evidence, exclusion_minutes=60)
        self.assertFalse(bound[0].event_blocked)

    def test_json_loader_rejects_unproven_schedule(self):
        at = datetime(2026, 6, 1, 12, 30, tzinfo=UTC)
        payload = self.evidence((self.event(at),)).payload
        loaded = event_evidence_from_json(payload)
        self.assertEqual(loaded.fingerprint, self.evidence((self.event(at),)).fingerprint)
        payload["events"][0]["known_at"] = (at + timedelta(minutes=1)).isoformat()
        with self.assertRaisesRegex(ValueError, "not known"):
            event_evidence_from_json(payload)


if __name__ == "__main__":
    unittest.main()
