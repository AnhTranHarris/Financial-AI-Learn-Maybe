from __future__ import annotations

"""M167 purged point-in-time temporal validation primitives."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Iterable


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class TemporalSample:
    sample_fingerprint: str
    feature_at: datetime
    label_start: datetime
    label_end: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_fingerprint", _sha(self.sample_fingerprint, "temporal sample"))
        for name in ("feature_at", "label_start", "label_end"):
            object.__setattr__(self, name, _aware(getattr(self, name), name))
        if not self.feature_at <= self.label_start <= self.label_end:
            raise ValueError("temporal sample label interval cannot precede feature observation")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "sample": self.sample_fingerprint,
                "feature_at": self.feature_at.isoformat(),
                "label_start": self.label_start.isoformat(),
                "label_end": self.label_end.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class PurgedTemporalSplit:
    test_start: datetime
    test_end: datetime
    embargo_seconds: int
    training: tuple[TemporalSample, ...]
    test: tuple[TemporalSample, ...]
    purged: tuple[TemporalSample, ...]
    embargoed: tuple[TemporalSample, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "test_start", _aware(self.test_start, "test_start"))
        object.__setattr__(self, "test_end", _aware(self.test_end, "test_end"))
        if self.test_end <= self.test_start:
            raise ValueError("purged temporal test_end must follow test_start")
        if isinstance(self.embargo_seconds, bool) or int(self.embargo_seconds) != self.embargo_seconds or int(self.embargo_seconds) < 0:
            raise ValueError("embargo_seconds must be nonnegative")
        object.__setattr__(self, "embargo_seconds", int(self.embargo_seconds))
        groups = (self.training, self.test, self.purged, self.embargoed)
        flattened = [row.sample_fingerprint for group in groups for row in group]
        if len(flattened) != len(set(flattened)):
            raise ValueError("temporal sample cannot belong to multiple split groups")
        if any(row.feature_at >= self.test_start or row.label_end >= self.test_start for row in self.training):
            raise ValueError("training sample leaks into test boundary")
        if any(not (self.test_start <= row.feature_at < self.test_end and row.label_end <= self.test_end) for row in self.test):
            raise ValueError("test sample is not fully realized inside test window")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m167-purged-temporal-v1",
                "test_start": self.test_start.isoformat(),
                "test_end": self.test_end.isoformat(),
                "embargo_seconds": self.embargo_seconds,
                "training": [row.fingerprint for row in self.training],
                "test": [row.fingerprint for row in self.test],
                "purged": [row.fingerprint for row in self.purged],
                "embargoed": [row.fingerprint for row in self.embargoed],
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


def build_purged_temporal_split(
    samples: Iterable[TemporalSample],
    *,
    test_start: datetime,
    test_end: datetime,
    embargo_seconds: int = 0,
) -> PurgedTemporalSplit:
    start = _aware(test_start, "test_start")
    end = _aware(test_end, "test_end")
    if end <= start:
        raise ValueError("test_end must follow test_start")
    if isinstance(embargo_seconds, bool) or int(embargo_seconds) != embargo_seconds or int(embargo_seconds) < 0:
        raise ValueError("embargo_seconds must be nonnegative")
    embargo_end = end + timedelta(seconds=int(embargo_seconds))
    rows = tuple(samples)
    identities = tuple(row.sample_fingerprint for row in rows)
    if len(identities) != len(set(identities)):
        raise ValueError("temporal split cannot contain duplicate sample identities")

    training: list[TemporalSample] = []
    test: list[TemporalSample] = []
    purged: list[TemporalSample] = []
    embargoed: list[TemporalSample] = []
    for row in sorted(rows, key=lambda value: (value.feature_at, value.sample_fingerprint)):
        if end <= row.feature_at < embargo_end:
            embargoed.append(row)
            continue
        if start <= row.feature_at < end:
            if row.label_end <= end:
                test.append(row)
            else:
                purged.append(row)
            continue
        if row.feature_at < start:
            if row.label_end < start:
                training.append(row)
            else:
                purged.append(row)
            continue
        # Future observations after the test/embargo window cannot inform this split.
        purged.append(row)

    return PurgedTemporalSplit(
        start,
        end,
        int(embargo_seconds),
        tuple(training),
        tuple(test),
        tuple(purged),
        tuple(embargoed),
    )
