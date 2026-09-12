from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import unittest

from dusty.research_sessions import (
    SESSION_EVIDENCE_DEFINITION,
    SESSION_EVIDENCE_FINGERPRINT,
    SESSION_EVIDENCE_PROTOCOL,
    active_research_session_labels,
    active_research_sessions,
    matching_research_session,
)

UTC = timezone.utc


class ResearchSessionEvidenceTests(unittest.TestCase):
    def test_protocol_is_versioned_and_content_addressed(self) -> None:
        expected = sha256(
            json.dumps(SESSION_EVIDENCE_DEFINITION, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        self.assertEqual(SESSION_EVIDENCE_FINGERPRINT, expected)
        self.assertEqual(SESSION_EVIDENCE_PROTOCOL, f"dusty-pit-research-sessions-v2:{expected}")
        composite = SESSION_EVIDENCE_DEFINITION["composites"]["LONDON_NY_OVERLAP"]
        self.assertEqual(composite["operator"], "all_active")
        self.assertEqual(composite["members"], ["LONDON", "NEW_YORK"])

    def test_asia_is_fixed_tokyo_civil_window(self) -> None:
        self.assertIn("ASIA", active_research_sessions(datetime(2026, 1, 15, 0, 0, tzinfo=UTC)))
        self.assertIn("ASIA", active_research_sessions(datetime(2026, 7, 15, 8, 59, tzinfo=UTC)))
        self.assertNotIn("ASIA", active_research_sessions(datetime(2026, 7, 15, 9, 0, tzinfo=UTC)))

    def test_london_civil_window_moves_with_uk_dst(self) -> None:
        self.assertNotIn("LONDON", active_research_sessions(datetime(2026, 1, 15, 7, 30, tzinfo=UTC)))
        self.assertIn("LONDON", active_research_sessions(datetime(2026, 1, 15, 8, 0, tzinfo=UTC)))
        self.assertIn("LONDON", active_research_sessions(datetime(2026, 7, 15, 7, 0, tzinfo=UTC)))
        self.assertNotIn("LONDON", active_research_sessions(datetime(2026, 7, 15, 16, 0, tzinfo=UTC)))

    def test_new_york_civil_window_moves_with_us_dst(self) -> None:
        self.assertNotIn("NEW_YORK", active_research_sessions(datetime(2026, 1, 15, 12, 30, tzinfo=UTC)))
        self.assertIn("NEW_YORK", active_research_sessions(datetime(2026, 1, 15, 13, 0, tzinfo=UTC)))
        self.assertIn("NEW_YORK", active_research_sessions(datetime(2026, 7, 15, 12, 0, tzinfo=UTC)))
        self.assertNotIn("NEW_YORK", active_research_sessions(datetime(2026, 7, 15, 21, 0, tzinfo=UTC)))

    def test_london_new_york_overlap_is_retained_and_composite_is_derived(self) -> None:
        at = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
        sessions = active_research_sessions(at)
        labels = active_research_session_labels(at)
        self.assertIn("LONDON", sessions)
        self.assertIn("NEW_YORK", sessions)
        self.assertIn("LONDON_NY_OVERLAP", labels)
        self.assertEqual(matching_research_session(at, ("NEW_YORK",)), "NEW_YORK")
        self.assertEqual(matching_research_session(at, ("LONDON",)), "LONDON")
        self.assertEqual(matching_research_session(at, ("LONDON_NY_OVERLAP",)), "LONDON_NY_OVERLAP")

    def test_composite_is_not_active_when_only_one_member_is_active(self) -> None:
        london_only = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
        new_york_only = datetime(2026, 7, 15, 20, 0, tzinfo=UTC)
        self.assertNotIn("LONDON_NY_OVERLAP", active_research_session_labels(london_only))
        self.assertNotIn("LONDON_NY_OVERLAP", active_research_session_labels(new_york_only))
        self.assertEqual(matching_research_session(london_only, ("LONDON_NY_OVERLAP",)), "")
        self.assertEqual(matching_research_session(new_york_only, ("LONDON_NY_OVERLAP",)), "")

    def test_frozen_strategy_session_set_is_supported(self) -> None:
        asia = datetime(2026, 7, 15, 1, 0, tzinfo=UTC)
        overlap = datetime(2026, 7, 15, 14, 0, tzinfo=UTC)
        requested = ("ASIA", "LONDON_NY_OVERLAP")
        self.assertEqual(matching_research_session(asia, requested), "ASIA")
        self.assertEqual(matching_research_session(overlap, requested), "LONDON_NY_OVERLAP")

    def test_naive_time_and_unknown_session_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            active_research_sessions(datetime(2026, 7, 15, 14, 0))
        with self.assertRaisesRegex(ValueError, "unsupported research session filters"):
            matching_research_session(datetime(2026, 7, 15, 14, 0, tzinfo=UTC), ("MARS",))


if __name__ == "__main__":
    unittest.main()
