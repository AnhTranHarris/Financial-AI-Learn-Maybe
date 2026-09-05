from __future__ import annotations

"""M166 deterministic walk-forward laboratory with frozen test identities."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import median
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


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


class WalkForwardMode(StrEnum):
    ANCHORED = "anchored"
    ROLLING = "rolling"


@dataclass(frozen=True, slots=True)
class WalkForwardWindow:
    fold: int
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime

    def __post_init__(self) -> None:
        if isinstance(self.fold, bool) or int(self.fold) != self.fold or int(self.fold) < 1:
            raise ValueError("walk-forward fold must be a positive integer")
        object.__setattr__(self, "fold", int(self.fold))
        for name in ("train_start", "train_end", "test_start", "test_end"):
            object.__setattr__(self, name, _aware(getattr(self, name), name))
        if not self.train_start < self.train_end <= self.test_start < self.test_end:
            raise ValueError("walk-forward window must keep training strictly before test")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "fold": self.fold,
                "train_start": self.train_start.isoformat(),
                "train_end": self.train_end.isoformat(),
                "test_start": self.test_start.isoformat(),
                "test_end": self.test_end.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class WalkForwardPlan:
    strategy_execution_fingerprint: str
    parameter_fingerprint: str
    dataset_fingerprint: str
    mode: WalkForwardMode
    windows: tuple[WalkForwardWindow, ...]

    def __post_init__(self) -> None:
        for name in ("strategy_execution_fingerprint", "parameter_fingerprint", "dataset_fingerprint"):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        if not self.windows:
            raise ValueError("walk-forward plan requires at least one fold")
        expected = tuple(range(1, len(self.windows) + 1))
        if tuple(row.fold for row in self.windows) != expected:
            raise ValueError("walk-forward folds must be sequential")
        for previous, current in zip(self.windows, self.windows[1:]):
            if current.test_start < previous.test_end:
                raise ValueError("walk-forward test windows cannot overlap")
            if current.train_end > current.test_start:
                raise ValueError("walk-forward training cannot cross test start")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m166-walk-forward-v1",
                "strategy_execution_fingerprint": self.strategy_execution_fingerprint,
                "parameter_fingerprint": self.parameter_fingerprint,
                "dataset_fingerprint": self.dataset_fingerprint,
                "mode": self.mode.value,
                "windows": [row.fingerprint for row in self.windows],
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


def build_walk_forward_plan(
    *,
    strategy_execution_fingerprint: str,
    parameter_fingerprint: str,
    dataset_fingerprint: str,
    start: datetime,
    end: datetime,
    train_days: int,
    test_days: int,
    mode: WalkForwardMode = WalkForwardMode.ANCHORED,
) -> WalkForwardPlan:
    start = _aware(start, "walk-forward start")
    end = _aware(end, "walk-forward end")
    if end <= start:
        raise ValueError("walk-forward end must follow start")
    if not 1 <= int(train_days) <= 36500 or not 1 <= int(test_days) <= 36500:
        raise ValueError("walk-forward train/test days out of range")
    train_span = timedelta(days=int(train_days))
    test_span = timedelta(days=int(test_days))
    first_test = start + train_span
    if first_test + test_span > end:
        raise ValueError("walk-forward range is too short for one full fold")

    windows: list[WalkForwardWindow] = []
    test_start = first_test
    fold = 1
    while test_start + test_span <= end:
        train_start = start if mode is WalkForwardMode.ANCHORED else test_start - train_span
        windows.append(
            WalkForwardWindow(
                fold,
                train_start,
                test_start,
                test_start,
                test_start + test_span,
            )
        )
        test_start += test_span
        fold += 1
    return WalkForwardPlan(
        strategy_execution_fingerprint,
        parameter_fingerprint,
        dataset_fingerprint,
        mode,
        tuple(windows),
    )


@dataclass(frozen=True, slots=True)
class WalkForwardFoldResult:
    plan_fingerprint: str
    window_fingerprint: str
    fold: int
    net_return: float
    max_drawdown: float
    trade_count: int
    passed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "plan_fingerprint", _sha(self.plan_fingerprint, "walk-forward plan result"))
        object.__setattr__(self, "window_fingerprint", _sha(self.window_fingerprint, "walk-forward window result"))
        object.__setattr__(self, "net_return", _finite(self.net_return, "walk-forward net return"))
        drawdown = _finite(self.max_drawdown, "walk-forward max drawdown")
        if drawdown < 0:
            raise ValueError("walk-forward drawdown cannot be negative")
        object.__setattr__(self, "max_drawdown", drawdown)
        if isinstance(self.trade_count, bool) or int(self.trade_count) != self.trade_count or int(self.trade_count) < 0:
            raise ValueError("walk-forward trade_count must be nonnegative")
        object.__setattr__(self, "trade_count", int(self.trade_count))


@dataclass(frozen=True, slots=True)
class WalkForwardSummary:
    plan_fingerprint: str
    fold_count: int
    pass_count: int
    pass_fraction: float
    median_net_return: float
    worst_net_return: float
    worst_max_drawdown: float
    total_trades: int

    @property
    def broker_write_authority(self) -> bool:
        return False


def summarize_walk_forward(
    plan: WalkForwardPlan,
    results: Iterable[WalkForwardFoldResult],
) -> WalkForwardSummary:
    rows = tuple(results)
    if len(rows) != len(plan.windows):
        raise ValueError("walk-forward summary requires exactly one result per planned fold")
    by_fold = {row.fold: row for row in rows}
    if len(by_fold) != len(rows):
        raise ValueError("walk-forward results contain duplicate folds")
    ordered: list[WalkForwardFoldResult] = []
    for window in plan.windows:
        row = by_fold.get(window.fold)
        if row is None:
            raise ValueError("walk-forward result missing planned fold")
        if row.plan_fingerprint != plan.fingerprint or row.window_fingerprint != window.fingerprint:
            raise ValueError("walk-forward result identity drift")
        ordered.append(row)
    returns = [row.net_return for row in ordered]
    return WalkForwardSummary(
        plan.fingerprint,
        len(ordered),
        sum(1 for row in ordered if row.passed),
        sum(1 for row in ordered if row.passed) / len(ordered),
        median(returns),
        min(returns),
        max(row.max_drawdown for row in ordered),
        sum(row.trade_count for row in ordered),
    )
