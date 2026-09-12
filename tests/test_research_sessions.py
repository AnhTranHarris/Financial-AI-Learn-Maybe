from __future__ import annotations

from datetime import datetime, timezone
import unittest

from dusty.research_sessions import (
    SESSION_EVIDENCE_PROTOCOL,
    active_research_sessions,
    matching_research_session,
)

UTC = timezone.utc


class ResearchSessionEvidenceTests(unittest.TestCase):
    def test_protocol_is_versioned(self) -> None:
        self.assertEqual(SESSION_EVIDENCE_PROTOCOL, "dusty-pit-research-sessions-v1")

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

    def test_london_new_york_overlap_is_retained(self) -> None:
        sessions = active_research_sessions(datetime(2026, 7, 15, 14, 0, tzinfo=UTC))
        self.assertIn("LONDON", sessions)
        self.assertIn("NEW_YORK", sessions)
        self.assertEqual(matching_research_session(datetime(2026, 7, 15, 14, 0, tzinfo=UTC), ("NEW_YORK",)), "NEW_YORK")
        self.assertEqual(matching_research_session(datetime(2026, 7, 15, 14, 0, tzinfo=UTC), ("LONDON",)), "LONDON")

    def test_naive_time_and_unknown_session_fail_closed(self) -> None:
        with self.assertRaises(ValueError):
            active_research_sessions(datetime(2026, 7, 15, 14, 0))
        with self.assertRaisesRegex(ValueError, "unsupported research session filters"):
            matching_research_session(datetime(2026, 7, 15, 14, 0, tzinfo=UTC), ("MARS",))


if __name__ == "__main__":
    unittest.main()
