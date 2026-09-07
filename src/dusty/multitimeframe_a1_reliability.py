from __future__ import annotations

"""M196.11 fail-closed A1 reliability assessment for minimum-lot research.

A1 answers only whether a completed minimum-lot research replay shows enough
repeatable evidence to remain worth studying.  It does not certify robustness,
profitability at growth sizing, demo readiness, Champion status, or trading
authority.  All economic/statistical thresholds are explicit policy inputs.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import fmean

from .backtest import trade_net_pnl
from .markets import InstrumentEconomics
from .multitimeframe_financial_backtest import (
    MinimumLotFinancialReplay,
    summarize_minimum_lot_financial_replay,
)
from .statistical import (
    ConfidenceInterval,
    ProfitConcentration,
    SelectionBiasAssessment,
    assess_selection_bias,
    bootstrap_mean_interval,
    profit_concentration,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


class A1ReliabilityStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    REJECTED = "rejected"
    PROMISING = "promising"


@dataclass(frozen=True, slots=True)
class A1ReliabilityPolicy:
    minimum_trades: int
    minimum_distinct_weeks: int
    minimum_net_pnl: float
    minimum_mean_trade_pnl: float
    minimum_bootstrap_lower_mean: float
    minimum_deflated_signal_score: float
    maximum_largest_winner_fraction: float
    maximum_drawdown_fraction: float
    minimum_positive_week_fraction: float
    bootstrap_confidence: float = 0.95
    bootstrap_resamples: int = 2000
    bootstrap_seed: int = 19611

    def __post_init__(self) -> None:
        if isinstance(self.minimum_trades, bool) or self.minimum_trades < 2:
            raise ValueError("A1 minimum_trades must be at least 2")
        if isinstance(self.minimum_distinct_weeks, bool) or self.minimum_distinct_weeks < 1:
            raise ValueError("A1 minimum_distinct_weeks must be positive")
        for name in (
            "minimum_net_pnl",
            "minimum_mean_trade_pnl",
            "minimum_bootstrap_lower_mean",
            "minimum_deflated_signal_score",
        ):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        for name in (
            "maximum_largest_winner_fraction",
            "maximum_drawdown_fraction",
            "minimum_positive_week_fraction",
            "bootstrap_confidence",
        ):
            value = _finite(getattr(self, name), name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0,1]")
            object.__setattr__(self, name, value)
        if not 0.0 < self.bootstrap_confidence < 1.0:
            raise ValueError("bootstrap_confidence must be in (0,1)")
        if isinstance(self.bootstrap_resamples, bool) or self.bootstrap_resamples < 100:
            raise ValueError("bootstrap_resamples must be at least 100")
        if isinstance(self.bootstrap_seed, bool) or self.bootstrap_seed < 0:
            raise ValueError("bootstrap_seed must be nonnegative")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m19611-a1-policy-v1",
                "minimum_trades": self.minimum_trades,
                "minimum_distinct_weeks": self.minimum_distinct_weeks,
                "minimum_net_pnl": self.minimum_net_pnl,
                "minimum_mean_trade_pnl": self.minimum_mean_trade_pnl,
                "minimum_bootstrap_lower_mean": self.minimum_bootstrap_lower_mean,
                "minimum_deflated_signal_score": self.minimum_deflated_signal_score,
                "maximum_largest_winner_fraction": self.maximum_largest_winner_fraction,
                "maximum_drawdown_fraction": self.maximum_drawdown_fraction,
                "minimum_positive_week_fraction": self.minimum_positive_week_fraction,
                "bootstrap_confidence": self.bootstrap_confidence,
                "bootstrap_resamples": self.bootstrap_resamples,
                "bootstrap_seed": self.bootstrap_seed,
            }
        )


@dataclass(frozen=True, slots=True)
class A1ReliabilityAssessment:
    status: A1ReliabilityStatus
    strategy_hash: str
    policy_fingerprint: str
    trial_count: int
    trade_count: int
    distinct_trade_weeks: int
    net_pnl: float
    mean_trade_pnl: float | None
    positive_week_fraction: float | None
    bootstrap: ConfidenceInterval | None
    selection_bias: SelectionBiasAssessment | None
    concentration: ProfitConcentration | None
    max_drawdown_fraction: float
    blockers: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m19611-a1-reliability-v1",
                "status": self.status.value,
                "strategy_hash": self.strategy_hash,
                "policy": self.policy_fingerprint,
                "trial_count": self.trial_count,
                "trade_count": self.trade_count,
                "distinct_trade_weeks": self.distinct_trade_weeks,
                "net_pnl": self.net_pnl,
                "mean_trade_pnl": self.mean_trade_pnl,
                "positive_week_fraction": self.positive_week_fraction,
                "bootstrap": None
                if self.bootstrap is None
                else {
                    "mean": self.bootstrap.mean,
                    "lower": self.bootstrap.lower,
                    "upper": self.bootstrap.upper,
                    "confidence": self.bootstrap.confidence,
                    "sample_count": self.bootstrap.sample_count,
                },
                "selection_bias": None
                if self.selection_bias is None
                else {
                    "passed": self.selection_bias.passed,
                    "sample_count": self.selection_bias.sample_count,
                    "trial_count": self.selection_bias.trial_count,
                    "mean_return": self.selection_bias.mean_return,
                    "standard_error": self.selection_bias.standard_error,
                    "raw_signal_score": self.selection_bias.raw_signal_score,
                    "search_penalty": self.selection_bias.search_penalty,
                    "deflated_signal_score": self.selection_bias.deflated_signal_score,
                    "reasons": self.selection_bias.reasons,
                },
                "concentration": None
                if self.concentration is None
                else {
                    "positive_total": self.concentration.positive_total,
                    "largest_winner": self.concentration.largest_winner,
                    "largest_winner_fraction": self.concentration.largest_winner_fraction,
                },
                "max_drawdown_fraction": self.max_drawdown_fraction,
                "blockers": self.blockers,
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


def assess_a1_reliability(
    replay: MinimumLotFinancialReplay,
    economics: InstrumentEconomics,
    *,
    policy: A1ReliabilityPolicy,
    trial_count: int,
) -> A1ReliabilityAssessment:
    """Assess repeatable A1 evidence without granting later-stage authority."""
    if isinstance(trial_count, bool) or trial_count < 1:
        raise ValueError("A1 trial_count must be positive")

    summary = summarize_minimum_lot_financial_replay(replay, economics)
    trades = replay.simulated_trades
    week_keys = tuple(sorted({(row.entry_at.isocalendar().year, row.entry_at.isocalendar().week) for row in trades}))

    insufficiency: list[str] = []
    if len(trades) < policy.minimum_trades:
        insufficiency.append("insufficient_trade_count")
    if len(week_keys) < policy.minimum_distinct_weeks:
        insufficiency.append("insufficient_time_coverage")

    if insufficiency:
        return A1ReliabilityAssessment(
            status=A1ReliabilityStatus.INSUFFICIENT,
            strategy_hash=replay.strategy_hash,
            policy_fingerprint=policy.fingerprint,
            trial_count=trial_count,
            trade_count=len(trades),
            distinct_trade_weeks=len(week_keys),
            net_pnl=summary.net_pnl,
            mean_trade_pnl=None,
            positive_week_fraction=None,
            bootstrap=None,
            selection_bias=None,
            concentration=None,
            max_drawdown_fraction=summary.max_drawdown_fraction,
            blockers=tuple(insufficiency),
        )

    trade_pnls = tuple(trade_net_pnl(row, economics) for row in trades)
    mean_trade = fmean(trade_pnls)
    bootstrap = bootstrap_mean_interval(
        trade_pnls,
        confidence=policy.bootstrap_confidence,
        resamples=policy.bootstrap_resamples,
        seed=policy.bootstrap_seed,
    )
    selection = assess_selection_bias(
        trade_pnls,
        trial_count=trial_count,
        min_deflated_score=policy.minimum_deflated_signal_score,
    )
    concentration = profit_concentration(trade_pnls)

    week_pnls: dict[tuple[int, int], float] = {}
    for trade, pnl in zip(trades, trade_pnls, strict=True):
        calendar = trade.entry_at.isocalendar()
        key = (calendar.year, calendar.week)
        week_pnls[key] = week_pnls.get(key, 0.0) + pnl
    positive_week_fraction = sum(value > 0.0 for value in week_pnls.values()) / len(week_pnls)

    blockers: list[str] = []
    if summary.net_pnl <= policy.minimum_net_pnl:
        blockers.append("net_pnl_failed")
    if mean_trade <= policy.minimum_mean_trade_pnl:
        blockers.append("mean_trade_pnl_failed")
    if bootstrap.lower <= policy.minimum_bootstrap_lower_mean:
        blockers.append("bootstrap_lower_mean_failed")
    if not selection.passed:
        blockers.append("search_adjusted_signal_failed")
    if concentration.largest_winner_fraction > policy.maximum_largest_winner_fraction:
        blockers.append("profit_concentration_failed")
    if summary.max_drawdown_fraction > policy.maximum_drawdown_fraction:
        blockers.append("drawdown_failed")
    if positive_week_fraction < policy.minimum_positive_week_fraction:
        blockers.append("time_consistency_failed")

    return A1ReliabilityAssessment(
        status=A1ReliabilityStatus.REJECTED if blockers else A1ReliabilityStatus.PROMISING,
        strategy_hash=replay.strategy_hash,
        policy_fingerprint=policy.fingerprint,
        trial_count=trial_count,
        trade_count=len(trades),
        distinct_trade_weeks=len(week_keys),
        net_pnl=summary.net_pnl,
        mean_trade_pnl=mean_trade,
        positive_week_fraction=positive_week_fraction,
        bootstrap=bootstrap,
        selection_bias=selection,
        concentration=concentration,
        max_drawdown_fraction=summary.max_drawdown_fraction,
        blockers=tuple(blockers),
    )
