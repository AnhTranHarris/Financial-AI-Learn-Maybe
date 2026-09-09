from __future__ import annotations

"""Pure policy for the multi-day M165 native broker calibration campaign.

The campaign is intentionally fixed: ten observations per broker-evidence date,
with Day 2 starting from 10/1 and Day 3 starting from 20/2.  A campaign day may
only start on an evidence date not already represented in custody.  The caller is
responsible for deriving that date from the same MetaTrader tick/deal clock used by
native evidence.  This module has no MetaTrader5 dependency and grants no broker,
retry, promotion, or live authority.
"""

from dataclasses import dataclass
from datetime import date, timezone
from typing import Iterable

from .broker_calibration import BrokerExecutionObservation, TradeSide


@dataclass(frozen=True, slots=True)
class M165CalibrationDayPolicy:
    day_number: int
    starting_observations: int
    target_observations: int
    starting_distinct_days: int
    target_distinct_days: int
    maximum_roundtrips: int = 5

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    promotion_authority = False

    def __post_init__(self) -> None:
        if self.day_number not in (2, 3):
            raise ValueError("M165 production calibration day must be 2 or 3")
        if self.starting_observations != (self.day_number - 1) * 10:
            raise ValueError("M165 day starting observation count mismatch")
        if self.target_observations != self.day_number * 10:
            raise ValueError("M165 day target observation count mismatch")
        if self.starting_distinct_days != self.day_number - 1:
            raise ValueError("M165 day starting distinct-day count mismatch")
        if self.target_distinct_days != self.day_number:
            raise ValueError("M165 day target distinct-day count mismatch")
        if self.maximum_roundtrips != 5:
            raise ValueError("M165 calibration day is fixed at five round trips")


def calibration_day_policy(day_number: int) -> M165CalibrationDayPolicy:
    day = int(day_number)
    return M165CalibrationDayPolicy(
        day_number=day,
        starting_observations=(day - 1) * 10,
        target_observations=day * 10,
        starting_distinct_days=day - 1,
        target_distinct_days=day,
    )


def observation_dates(rows: Iterable[BrokerExecutionObservation]) -> tuple[date, ...]:
    return tuple(sorted({row.observed_at.astimezone(timezone.utc).date() for row in rows}))


def validate_campaign_start(
    rows: Iterable[BrokerExecutionObservation],
    *,
    policy: M165CalibrationDayPolicy,
    campaign_date: date,
) -> None:
    observations = tuple(rows)
    if len(observations) != policy.starting_observations:
        raise RuntimeError(
            f"M165 day {policy.day_number} requires exactly "
            f"{policy.starting_observations} starting observations; found {len(observations)}"
        )
    dates = observation_dates(observations)
    if len(dates) != policy.starting_distinct_days:
        raise RuntimeError(
            f"M165 day {policy.day_number} requires exactly "
            f"{policy.starting_distinct_days} existing broker-evidence dates; found {len(dates)}"
        )
    if {row.side for row in observations} != {TradeSide.BUY, TradeSide.SELL}:
        raise RuntimeError("M165 campaign requires BUY and SELL evidence before advancing days")
    if dates and campaign_date <= max(dates):
        raise RuntimeError(
            f"M165 day {policy.day_number} cannot start until a new broker-evidence date; "
            f"latest custody date is {max(dates).isoformat()}, current broker-evidence date is {campaign_date.isoformat()}"
        )


def validate_campaign_progress(
    rows: Iterable[BrokerExecutionObservation],
    *,
    policy: M165CalibrationDayPolicy,
    campaign_date: date,
    expected_observations: int,
) -> None:
    observations = tuple(rows)
    if len(observations) != expected_observations:
        raise RuntimeError(
            f"M165 custody progression mismatch: expected {expected_observations}, found {len(observations)}"
        )
    dates = observation_dates(observations)
    if len(dates) != policy.target_distinct_days:
        raise RuntimeError(
            f"M165 day {policy.day_number} requires exactly {policy.target_distinct_days} broker-evidence dates while running"
        )
    if max(dates) != campaign_date:
        raise RuntimeError("new M165 observations escaped the authorized broker-evidence date")
    if {row.side for row in observations} != {TradeSide.BUY, TradeSide.SELL}:
        raise RuntimeError("M165 campaign lost BUY/SELL representation")
