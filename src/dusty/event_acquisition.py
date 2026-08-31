from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .data_acquisition import HTTPSClient
from .events import ScheduledEvent
from .news import NewsItem


@dataclass(frozen=True, slots=True)
class RSSFeedConfig:
    source_id: str
    url: str
    currencies: tuple[str, ...] = ()
    underliers: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()
    event_key: str = ""

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.url.startswith("https://"):
            raise ValueError("RSS feed requires source identity and HTTPS URL")


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(element: ET.Element, names: set[str]) -> str:
    for child in element:
        if _local_name(child.tag) in names:
            if child.text and child.text.strip():
                return child.text.strip()
            href = child.attrib.get("href", "").strip()
            if href:
                return href
    return ""


def _parse_published(raw: str, fallback: datetime) -> datetime:
    value = raw.strip()
    if not value:
        return fallback
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return fallback


class OfficialRSSAdapter:
    """Normalize RSS/Atom from an explicitly configured official source into NewsItem."""

    def __init__(self, client: HTTPSClient) -> None:
        self.client = client

    def fetch(self, config: RSSFeedConfig, *, retrieved_at: datetime | None = None) -> tuple[NewsItem, ...]:
        when = retrieved_at or datetime.now(timezone.utc)
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        xml = self.client.get_bytes(config.url, headers={"Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml"})
        root = ET.fromstring(xml)
        entries = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
        result = []
        for entry in entries:
            title = _child_text(entry, {"title"})
            if not title:
                continue
            link = _child_text(entry, {"link"})
            identity = _child_text(entry, {"guid", "id"}) or link or hashlib.sha256(title.encode("utf-8")).hexdigest()
            published_raw = _child_text(entry, {"pubdate", "published", "updated"})
            published = _parse_published(published_raw, when)
            if published > when:
                published = when
            summary = _child_text(entry, {"description", "summary", "content"})
            result.append(
                NewsItem.of(
                    source_id=config.source_id,
                    external_id=identity,
                    published_at=published,
                    known_at=when,
                    headline=title,
                    summary=summary,
                    currencies=config.currencies,
                    underliers=config.underliers,
                    topics=config.topics,
                    event_key=config.event_key,
                )
            )
        return tuple(result)


@dataclass(frozen=True, slots=True)
class CalendarConfig:
    source_id: str = "bls_calendar"
    url: str = "https://www.bls.gov/schedule/news_release/bls.ics"
    currencies: tuple[str, ...] = ("USD",)


class BLSCalendarAdapter:
    """Fetch the official BLS online release calendar into ScheduledEvent records."""

    def __init__(self, client: HTTPSClient) -> None:
        self.client = client

    def fetch(self, config: CalendarConfig = CalendarConfig(), *, retrieved_at: datetime | None = None) -> tuple[ScheduledEvent, ...]:
        when = retrieved_at or datetime.now(timezone.utc)
        if when.tzinfo is None or when.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        text = self.client.get_text(config.url, headers={"Accept": "text/calendar"})
        lines = _unfold_ical(text)
        blocks: list[list[str]] = []
        current: list[str] | None = None
        for line in lines:
            if line == "BEGIN:VEVENT":
                current = []
            elif line == "END:VEVENT":
                if current is not None:
                    blocks.append(current)
                current = None
            elif current is not None:
                current.append(line)
        result = []
        for block in blocks:
            fields = _ical_fields(block)
            raw_dt = fields.get("DTSTART", "")
            summary = fields.get("SUMMARY", "").strip()
            if not raw_dt or not summary:
                continue
            scheduled = _parse_ical_datetime(raw_dt, fields.get("DTSTART_TZID", "America/New_York"))
            uid = fields.get("UID", "").strip()
            event_id = uid or "bls-" + hashlib.sha256(f"{summary}|{scheduled.isoformat()}".encode("utf-8")).hexdigest()[:20]
            result.append(ScheduledEvent(event_id, scheduled, when, tuple(sorted(set(config.currencies))), summary, source_id=config.source_id))
        return tuple(sorted(result, key=lambda item: (item.scheduled_at, item.event_id)))


def _unfold_ical(text: str) -> tuple[str, ...]:
    unfolded: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and unfolded:
            unfolded[-1] += raw[1:]
        elif raw:
            unfolded.append(raw)
    return tuple(unfolded)


def _ical_fields(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if ":" not in line:
            continue
        left, value = line.split(":", 1)
        pieces = left.split(";")
        key = pieces[0].upper()
        result[key] = value
        for parameter in pieces[1:]:
            if "=" in parameter:
                name, parameter_value = parameter.split("=", 1)
                result[f"{key}_{name.upper()}"] = parameter_value
    return result


def _nth_weekday_of_month(year: int, month: int, weekday: int, ordinal: int) -> int:
    first = date(year, month, 1)
    return 1 + (weekday - first.weekday()) % 7 + 7 * (ordinal - 1)


def _us_eastern_fallback(local: datetime) -> tzinfo:
    """Post-2007 U.S. Eastern rules for BLS release times when system IANA tzdata is absent.

    BLS releases are normally scheduled well away from the 02:00 DST transition boundary. This narrow
    fallback intentionally supports only the calendar timezone Dusty has verified, rather than embedding
    a general timezone database in the trading core.
    """
    second_sunday_march = _nth_weekday_of_month(local.year, 3, 6, 2)
    first_sunday_november = _nth_weekday_of_month(local.year, 11, 6, 1)
    dst_start = datetime(local.year, 3, second_sunday_march, 2, 0, 0)
    dst_end = datetime(local.year, 11, first_sunday_november, 2, 0, 0)
    in_dst = dst_start <= local < dst_end
    return timezone(timedelta(hours=-4 if in_dst else -5), name="America/New_York")


def _calendar_tz(tzid: str, local: datetime) -> tzinfo:
    normalized = tzid.strip() or "America/New_York"
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        if normalized in {"America/New_York", "US/Eastern"} and local.year >= 2007:
            return _us_eastern_fallback(local)
        raise ValueError(f"calendar timezone unavailable without verified rules: {normalized}") from exc


def _parse_ical_datetime(raw: str, tzid: str) -> datetime:
    value = raw.strip()
    if re.fullmatch(r"\d{8}T\d{6}Z", value):
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    if re.fullmatch(r"\d{8}T\d{6}", value):
        local = datetime.strptime(value, "%Y%m%dT%H%M%S")
        return local.replace(tzinfo=_calendar_tz(tzid, local))
    if re.fullmatch(r"\d{8}", value):
        local = datetime.strptime(value, "%Y%m%d")
        return local.replace(tzinfo=_calendar_tz(tzid, local))
    raise ValueError(f"unsupported iCalendar DTSTART: {raw}")
