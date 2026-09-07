from __future__ import annotations

"""M196.12 chronological A1 evidence campaign.

This layer aggregates already-computed M196.11 minimum-lot A1 reliability
assessments across frozen, non-overlapping walk-forward test windows.  It does
not rerun or reinterpret the underlying strategy, does not grant later-stage
robustness/demo/Champion status, and carries no operational trading authority.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import median
from typing import Iterable

from .multitimeframe_a1_reliability import A1ReliabilityAssessment, A1ReliabilityStatus
from .walk_forward_lab import WalkForwardPlan


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _unit(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{label} must be finite in [0,1]")
    return rendered


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


class A1CampaignStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    REJECTED = "rejected"
    PROMISING = "promising"


@dataclass(frozen=True, slots=True)
class A1CampaignPolicy:
    minimum_windows: int
    minimum_total_trades: int
    minimum_promising_window_fraction: float
    minimum_positive_window_fraction: float
    minimum_total_net_pnl: float
    maximum_worst_drawdown_fraction: float

    def __post_init__(self) -> None:
        if isinstance(self.minimum_windows, bool) or int(self.minimum_windows) != self.minimum_windows or int(self.minimum_windows) < 2:
            raise ValueError("A1 campaign minimum_windows must be at least 2")
        if isinstance(self.minimum_total_trades, bool) or int(self.minimum_total_trades) != self.minimum_total_trades or int(self.minimum_total_trades) < 1:
            raise ValueError("A1 campaign minimum_total_trades must be positive")
        object.__setattr__(self, "minimum_windows", int(self.minimum_windows))
        object.__setattr__(self, "minimum_total_trades", int(self.minimum_total_trades))
        for name in ("minimum_promising_window_fraction", "minimum_positive_window_fraction", "maximum_worst_drawdown_fraction"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        object.__setattr__(self, "minimum_total_net_pnl", _finite(self.minimum_total_net_pnl, "minimum_total_net_pnl"))

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m19612-a1-campaign-policy-v1",
                "minimum_windows": self.minimum_windows,
                "minimum_total_trades": self.minimum_total_trades,
                "minimum_promising_window_fraction": self.minimum_promising_window_fraction,
                "minimum_positive_window_fraction": self.minimum_positive_window_fraction,
                "minimum_total_net_pnl": self.minimum_total_net_pnl,
                "maximum_worst_drawdown_fraction": self.maximum_worst_drawdown_fraction,
            }
        )


@dataclass(frozen=True, slots=True)
class A1WindowEvidence:
    fold: int
    plan_fingerprint: str
    window_fingerprint: str
    observed_start: datetime
    observed_end: datetime
    assessment: A1ReliabilityAssessment

    def __post_init__(self) -> None:
        if isinstance(self.fold, bool) or int(self.fold) != self.fold or int(self.fold) < 1:
            raise ValueError("A1 window fold must be a positive integer")
        object.__setattr__(self, "fold", int(self.fold))
        object.__setattr__(self, "plan_fingerprint", _sha(self.plan_fingerprint, "A1 window plan"))
        object.__setattr__(self, "window_fingerprint", _sha(self.window_fingerprint, "A1 window identity"))
        object.__setattr__(self, "observed_start", _aware(self.observed_start, "A1 observed_start"))
        object.__setattr__(self, "observed_end", _aware(self.observed_end, "A1 observed_end"))
        if self.observed_end <= self.observed_start:
            raise ValueError("A1 observed interval must be positive")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m19612-a1-window-evidence-v1",
                "fold": self.fold,
                "plan": self.plan_fingerprint,
                "window": self.window_fingerprint,
                "observed_start": self.observed_start.isoformat(),
                "observed_end": self.observed_end.isoformat(),
                "assessment": self.assessment.fingerprint,
            }
        )


@dataclass(frozen=True, slots=True)
class A1CampaignAssessment:
    status: A1CampaignStatus
    plan_fingerprint: str
    strategy_hash: str
    a1_policy_fingerprint: str | None
    campaign_policy_fingerprint: str
    window_count: int
    promising_window_count: int
    rejected_window_count: int
    insufficient_window_count: int
    promising_window_fraction: float | None
    positive_window_fraction: float | None
    total_trades: int
    total_net_pnl: float
    median_window_net_pnl: float | None
    worst_window_net_pnl: float | None
    worst_drawdown_fraction: float | None
    blockers: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m19612-a1-chronological-campaign-v1",
                "status": self.status.value,
                "plan": self.plan_fingerprint,
                "strategy_hash": self.strategy_hash,
                "a1_policy": self.a1_policy_fingerprint,
                "campaign_policy": self.campaign_policy_fingerprint,
                "window_count": self.window_count,
                "promising_window_count": self.promising_window_count,
                "rejected_window_count": self.rejected_window_count,
                "insufficient_window_count": self.insufficient_window_count,
                "promising_window_fraction": self.promising_window_fraction,
                "positive_window_fraction": self.positive_window_fraction,
                "total_trades": self.total_trades,
                "total_net_pnl": self.total_net_pnl,
                "median_window_net_pnl": self.median_window_net_pnl,
                "worst_window_net_pnl": self.worst_window_net_pnl,
                "worst_drawdown_fraction": self.worst_drawdown_fraction,
                "blockers": self.blockers,
                "evidence": self.evidence_fingerprints,
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def assess_a1_chronological_campaign(
    plan: WalkForwardPlan,
    evidence: Iterable[A1WindowEvidence],
    *,
    policy: A1CampaignPolicy,
) -> A1CampaignAssessment:
    rows = tuple(evidence)
    by_fold = {row.fold: row for row in rows}
    if len(by_fold) != len(rows):
        raise ValueError("A1 campaign evidence contains duplicate folds")
    if any(row.fold > len(plan.windows) for row in rows):
        raise ValueError("A1 campaign evidence references an unknown fold")

    ordered: list[A1WindowEvidence] = []
    missing: list[int] = []
    for window in plan.windows:
        row = by_fold.get(window.fold)
        if row is None:
            missing.append(window.fold)
            continue
        if row.plan_fingerprint != plan.fingerprint or row.window_fingerprint != window.fingerprint:
            raise ValueError("A1 campaign window identity drift")
        if not (window.test_start <= row.observed_start < row.observed_end <= window.test_end):
            raise ValueError("A1 observed evidence lies outside its frozen test window")
        ordered.append(row)

    strategy_hashes = {row.assessment.strategy_hash for row in ordered}
    if len(strategy_hashes) > 1:
        raise ValueError("A1 campaign cannot mix strategy identities")
    if strategy_hashes and next(iter(strategy_hashes)) != plan.strategy_execution_fingerprint:
        raise ValueError("A1 campaign strategy identity does not bind walk-forward plan")
    a1_policies = {row.assessment.policy_fingerprint for row in ordered}
    if len(a1_policies) > 1:
        raise ValueError("A1 campaign cannot mix M196.11 policy identities")

    total_trades = sum(row.assessment.trade_count for row in ordered)
    total_net = sum(row.assessment.net_pnl for row in ordered)
    promising = sum(row.assessment.status is A1ReliabilityStatus.PROMISING for row in ordered)
    rejected = sum(row.assessment.status is A1ReliabilityStatus.REJECTED for row in ordered)
    insufficient = sum(row.assessment.status is A1ReliabilityStatus.INSUFFICIENT for row in ordered)
    net_rows = tuple(row.assessment.net_pnl for row in ordered)
    drawdowns = tuple(row.assessment.max_drawdown_fraction for row in ordered)

    insufficiency: list[str] = []
    if missing:
        insufficiency.append("missing_planned_windows")
    if len(ordered) < policy.minimum_windows:
        insufficiency.append("insufficient_window_count")
    if total_trades < policy.minimum_total_trades:
        insufficiency.append("insufficient_total_trades")
    if insufficient:
        insufficiency.append("underlying_window_insufficient")

    promising_fraction = promising / len(ordered) if ordered else None
    positive_fraction = sum(value > 0.0 for value in net_rows) / len(net_rows) if net_rows else None
    a1_policy_fingerprint = next(iter(a1_policies)) if a1_policies else None
    evidence_fingerprints = tuple(row.fingerprint for row in ordered)

    if insufficiency:
        return A1CampaignAssessment(
            A1CampaignStatus.INSUFFICIENT,
            plan.fingerprint,
            plan.strategy_execution_fingerprint,
            a1_policy_fingerprint,
            policy.fingerprint,
            len(ordered),
            promising,
            rejected,
            insufficient,
            promising_fraction,
            positive_fraction,
            total_trades,
            total_net,
            median(net_rows) if net_rows else None,
            min(net_rows) if net_rows else None,
            max(drawdowns) if drawdowns else None,
            tuple(insufficiency),
            evidence_fingerprints,
        )

    assert promising_fraction is not None and positive_fraction is not None and drawdowns
    blockers: list[str] = []
    if promising_fraction < policy.minimum_promising_window_fraction:
        blockers.append("promising_window_fraction_failed")
    if positive_fraction < policy.minimum_positive_window_fraction:
        blockers.append("positive_window_fraction_failed")
    if total_net <= policy.minimum_total_net_pnl:
        blockers.append("aggregate_net_pnl_failed")
    worst_drawdown = max(drawdowns)
    if worst_drawdown > policy.maximum_worst_drawdown_fraction:
        blockers.append("worst_drawdown_failed")

    return A1CampaignAssessment(
        A1CampaignStatus.REJECTED if blockers else A1CampaignStatus.PROMISING,
        plan.fingerprint,
        plan.strategy_execution_fingerprint,
        a1_policy_fingerprint,
        policy.fingerprint,
        len(ordered),
        promising,
        rejected,
        insufficient,
        promising_fraction,
        positive_fraction,
        total_trades,
        total_net,
        median(net_rows),
        min(net_rows),
        worst_drawdown,
        tuple(blockers),
        evidence_fingerprints,
    )
