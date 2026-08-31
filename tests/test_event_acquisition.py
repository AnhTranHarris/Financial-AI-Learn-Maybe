from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from dusty.data_acquisition import HTTPSClient
from dusty.event_acquisition import (
    BLSCalendarAdapter,
    CalendarConfig,
    OfficialRSSAdapter,
    RSSFeedConfig,
    _parse_ical_datetime,
)


class StaticFetcher:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __call__(self, request, timeout: float) -> bytes:
        return self.payload


class EventAcquisitionTests(unittest.TestCase):
    def test_rss_publication_time_does_not_backdate_known_time(self) -> None:
        xml = b'''<rss><channel><item><guid>x1</guid><title>Policy decision</title><pubDate>Fri, 28 Aug 2026 14:00:00 GMT</pubDate><description>Context</description></item></channel></rss>'''
        known = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        adapter = OfficialRSSAdapter(HTTPSClient(fetch_bytes=StaticFetcher(xml), sleeper=lambda _: None))
        items = adapter.fetch(RSSFeedConfig("fed_press", "https://example.gov/feed.xml", currencies=("USD",), topics=("monetary_policy",)), retrieved_at=known)
        self.assertEqual(items[0].published_at.day, 28)
        self.assertEqual(items[0].known_at, known)
        self.assertEqual(items[0].currencies, ("USD",))

    def test_atom_feed_is_supported(self) -> None:
        xml = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>e1</id><title>ECB update</title><updated>2026-08-31T10:00:00Z</updated><link href="https://ecb.example/e1"/><summary>Details</summary></entry></feed>'''
        adapter = OfficialRSSAdapter(HTTPSClient(fetch_bytes=StaticFetcher(xml), sleeper=lambda _: None))
        items = adapter.fetch(RSSFeedConfig("ecb_press", "https://example.eu/feed.xml", currencies=("EUR",)), retrieved_at=datetime(2026, 8, 31, 10, 5, tzinfo=timezone.utc))
        self.assertEqual(items[0].headline, "ECB update")
        self.assertEqual(items[0].external_id, "e1")

    def test_bls_ics_becomes_scheduled_events(self) -> None:
        ics = b'''BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:cpi-2026-09\r\nDTSTART;TZID=America/New_York:20260911T083000\r\nSUMMARY:Consumer Price Index\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n'''
        known = datetime(2026, 8, 31, 12, tzinfo=timezone.utc)
        client = HTTPSClient(fetch_bytes=StaticFetcher(ics), sleeper=lambda _: None)
        events = BLSCalendarAdapter(client).fetch(CalendarConfig(), retrieved_at=known)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, "cpi-2026-09")
        self.assertEqual(events[0].known_at, known)
        self.assertEqual(events[0].currencies, ("USD",))
        self.assertEqual(events[0].scheduled_at.hour, 8)
        self.assertEqual(events[0].scheduled_at.utcoffset(), timedelta(hours=-4))

    def test_bls_eastern_fallback_handles_standard_and_daylight_time(self) -> None:
        with patch("dusty.event_acquisition.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing")):
            winter = _parse_ical_datetime("20260115T083000", "America/New_York")
            summer = _parse_ical_datetime("20260715T083000", "America/New_York")
            self.assertEqual(winter.utcoffset(), timedelta(hours=-5))
            self.assertEqual(summer.utcoffset(), timedelta(hours=-4))
            with self.assertRaisesRegex(ValueError, "timezone unavailable"):
                _parse_ical_datetime("20260715T083000", "Europe/London")


if __name__ == "__main__":
    unittest.main()
