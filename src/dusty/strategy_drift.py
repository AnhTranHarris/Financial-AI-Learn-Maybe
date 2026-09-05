from __future__ import annotations

"""M192 deterministic Strategy Drift Watch.

The monitor compares a Frozen Champion's certified reference behavior with
chronologically later point-in-time replay and actual Demo outcomes.  It keeps
strategy/edge drift separate from broker-execution drift and data-integrity
failures.  M192 is observational only: suspension belongs to M193.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from statistics import median
from typing import Iterable

from .champion_registry import FrozenChampionRecord


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError("source commit requires a 40- or 64-character hexadecimal identity")
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


def _unit(value: float, label: str) -> float:
    rendered = _finite(value, label)
    if not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{label} must be in [0,1]")
    return rendered


def _positive(value: float, label: str) -> float:
    rendered = _finite(value, label)
    if rendered <= 0:
        raise ValueError(f"{label} must be positive")
    return rendered


def _positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 1:
        raise ValueError(f"{label} must be a positive integer")
    return int(value)


def _nonnegative_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return int(value)


class ExecutionQuality(StrEnum):
    WITHIN_CALIBRATED = "within_calibrated"
    DEVIATED = "deviated"
    INCOMPLETE = "incomplete"


class ReplayQuality(StrEnum):
    MATCHED = "matched"
    DIVERGED = "diverged"
    INCOMPLETE = "incomplete"


class StrategyDriftStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    STABLE = "stable"
    WATCH = "watch"
    EXECUTION_DRIFT_ONLY = "execution_drift_only"
    DATA_OR_REPLAY_DRIFT = "data_or_replay_drift"
    STRUCTURAL_DRIFT = "structural_drift"
    GOVERNANCE_FAILURE = "governance_failure"


@dataclass(frozen=True, slots=True)
class StrategyDriftPolicy:
    minimum_observations: int
    warning_signal_count: int
    structural_signal_count: int
    maximum_expectancy_drop_fraction: float
    maximum_hit_rate_drop: float
    maximum_profit_factor_drop_fraction: float
    maximum_average_win_drop_fraction: float
    maximum_average_loss_increase_fraction: float
    maximum_holding_time_ratio_deviation: float
    maximum_replay_actual_expectancy_gap_r: float
    maximum_execution_deviation_fraction: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum_observations", _positive_int(self.minimum_observations, "minimum_observations"))
        object.__setattr__(self, "warning_signal_count", _positive_int(self.warning_signal_count, "warning_signal_count"))
        object.__setattr__(self, "structural_signal_count", _positive_int(self.structural_signal_count, "structural_signal_count"))
        if self.structural_signal_count < self.warning_signal_count:
            raise ValueError("structural_signal_count cannot be below warning_signal_count")
        for name in (
            "maximum_expectancy_drop_fraction",
            "maximum_hit_rate_drop",
            "maximum_profit_factor_drop_fraction",
            "maximum_average_win_drop_fraction",
            "maximum_average_loss_increase_fraction",
            "maximum_execution_deviation_fraction",
        ):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        holding = _finite(self.maximum_holding_time_ratio_deviation, "maximum_holding_time_ratio_deviation")
        if holding < 0:
            raise ValueError("maximum_holding_time_ratio_deviation must be nonnegative")
        object.__setattr__(self, "maximum_holding_time_ratio_deviation", holding)
        gap = _finite(self.maximum_replay_actual_expectancy_gap_r, "maximum_replay_actual_expectancy_gap_r")
        if gap < 0:
            raise ValueError("maximum_replay_actual_expectancy_gap_r must be nonnegative")
        object.__setattr__(self, "maximum_replay_actual_expectancy_gap_r", gap)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m192-strategy-drift-policy-v1",
            self.minimum_observations,
            self.warning_signal_count,
            self.structural_signal_count,
            self.maximum_expectancy_drop_fraction,
            self.maximum_hit_rate_drop,
            self.maximum_profit_factor_drop_fraction,
            self.maximum_average_win_drop_fraction,
            self.maximum_average_loss_increase_fraction,
            self.maximum_holding_time_ratio_deviation,
            self.maximum_replay_actual_expectancy_gap_r,
            self.maximum_execution_deviation_fraction,
        ))


@dataclass(frozen=True, slots=True)
class StrategyDriftBaseline:
    champion_fingerprint: str
    deployment_fingerprint: str
    strategy_fingerprint: str
    robustness_fingerprint: str
    reference_data_fingerprint: str
    source_commit: str
    period_end: datetime
    observation_count: int
    expectancy_r: float
    hit_rate: float
    average_win_r: float
    average_loss_r: float
    profit_factor: float
    median_holding_seconds: float
    evidence_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        for field, label in (
            ("champion_fingerprint", "baseline Champion"),
            ("deployment_fingerprint", "baseline deployment"),
            ("strategy_fingerprint", "baseline strategy"),
            ("robustness_fingerprint", "baseline robustness"),
            ("reference_data_fingerprint", "baseline reference data"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit))
        object.__setattr__(self, "period_end", _aware(self.period_end, "baseline period_end"))
        object.__setattr__(self, "observation_count", _positive_int(self.observation_count, "baseline observation_count"))
        object.__setattr__(self, "expectancy_r", _finite(self.expectancy_r, "baseline expectancy_r"))
        object.__setattr__(self, "hit_rate", _unit(self.hit_rate, "baseline hit_rate"))
        object.__setattr__(self, "average_win_r", _positive(self.average_win_r, "baseline average_win_r"))
        object.__setattr__(self, "average_loss_r", _positive(self.average_loss_r, "baseline average_loss_r"))
        object.__setattr__(self, "profit_factor", _positive(self.profit_factor, "baseline profit_factor"))
        object.__setattr__(self, "median_holding_seconds", _positive(self.median_holding_seconds, "baseline median_holding_seconds"))
        evidence = tuple(sorted(_sha(row, "baseline evidence") for row in self.evidence_fingerprints))
        if not evidence or len(evidence) != len(set(evidence)):
            raise ValueError("baseline evidence must be unique and nonempty")
        object.__setattr__(self, "evidence_fingerprints", evidence)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m192-strategy-drift-baseline-v1",
            self.champion_fingerprint,
            self.deployment_fingerprint,
            self.strategy_fingerprint,
            self.robustness_fingerprint,
            self.reference_data_fingerprint,
            self.source_commit,
            self.period_end.isoformat(),
            self.observation_count,
            self.expectancy_r,
            self.hit_rate,
            self.average_win_r,
            self.average_loss_r,
            self.profit_factor,
            self.median_holding_seconds,
            self.evidence_fingerprints,
        ))


def build_strategy_drift_baseline(
    champion: FrozenChampionRecord,
    *,
    reference_data_fingerprint: str,
    period_end: datetime,
    observation_count: int,
    expectancy_r: float,
    hit_rate: float,
    average_win_r: float,
    average_loss_r: float,
    profit_factor: float,
    median_holding_seconds: float,
    evidence_fingerprints: Iterable[str],
) -> StrategyDriftBaseline:
    """Bind supplied certified reference metrics to one immutable Champion."""

    return StrategyDriftBaseline(
        champion.fingerprint,
        champion.deployment_fingerprint,
        champion.strategy_fingerprint,
        champion.robustness_fingerprint,
        reference_data_fingerprint,
        champion.source_commit,
        period_end,
        observation_count,
        expectancy_r,
        hit_rate,
        average_win_r,
        average_loss_r,
        profit_factor,
        median_holding_seconds,
        tuple(evidence_fingerprints),
    )


@dataclass(frozen=True, slots=True)
class ForwardTradeDriftObservation:
    champion_fingerprint: str
    trade_fingerprint: str
    observed_at: datetime
    replay_fingerprint: str
    replay_quality: ReplayQuality
    replay_net_r: float | None
    replay_holding_seconds: float | None
    actual_net_r: float | None
    actual_holding_seconds: float | None
    execution_quality: ExecutionQuality
    execution_evidence_fingerprint: str | None
    data_integrity_ok: bool
    rule_violations: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "champion_fingerprint", _sha(self.champion_fingerprint, "forward Champion"))
        object.__setattr__(self, "trade_fingerprint", _sha(self.trade_fingerprint, "forward trade"))
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "forward observed_at"))
        object.__setattr__(self, "replay_fingerprint", _sha(self.replay_fingerprint, "forward PIT replay"))
        object.__setattr__(self, "rule_violations", _nonnegative_int(self.rule_violations, "forward rule_violations"))
        if self.replay_quality is ReplayQuality.INCOMPLETE:
            if self.replay_net_r is not None or self.replay_holding_seconds is not None:
                raise ValueError("incomplete PIT replay cannot expose replay metrics")
        else:
            if self.replay_net_r is None or self.replay_holding_seconds is None:
                raise ValueError("complete PIT replay requires replay metrics")
            object.__setattr__(self, "replay_net_r", _finite(self.replay_net_r, "forward replay_net_r"))
            object.__setattr__(self, "replay_holding_seconds", _positive(self.replay_holding_seconds, "forward replay_holding_seconds"))
        if self.actual_net_r is None or self.actual_holding_seconds is None:
            if self.execution_quality is not ExecutionQuality.INCOMPLETE:
                raise ValueError("missing actual outcome requires incomplete execution evidence")
            if self.actual_net_r is not None or self.actual_holding_seconds is not None:
                raise ValueError("actual return and holding duration must appear together")
        else:
            object.__setattr__(self, "actual_net_r", _finite(self.actual_net_r, "forward actual_net_r"))
            object.__setattr__(self, "actual_holding_seconds", _positive(self.actual_holding_seconds, "forward actual_holding_seconds"))
        if self.execution_quality is ExecutionQuality.INCOMPLETE:
            if self.execution_evidence_fingerprint is not None:
                object.__setattr__(self, "execution_evidence_fingerprint", _sha(self.execution_evidence_fingerprint, "execution evidence"))
        else:
            if self.execution_evidence_fingerprint is None:
                raise ValueError("complete execution classification requires evidence fingerprint")
            object.__setattr__(self, "execution_evidence_fingerprint", _sha(self.execution_evidence_fingerprint, "execution evidence"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m192-forward-trade-drift-v1",
            self.champion_fingerprint,
            self.trade_fingerprint,
            self.observed_at.isoformat(),
            self.replay_fingerprint,
            self.replay_quality.value,
            self.replay_net_r,
            self.replay_holding_seconds,
            self.actual_net_r,
            self.actual_holding_seconds,
            self.execution_quality.value,
            self.execution_evidence_fingerprint,
            self.data_integrity_ok,
            self.rule_violations,
        ))


@dataclass(frozen=True, slots=True)
class DriftMetricSnapshot:
    observation_count: int
    expectancy_r: float
    hit_rate: float
    average_win_r: float | None
    average_loss_r: float | None
    profit_factor: float | None
    median_holding_seconds: float

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m192-drift-metric-snapshot-v1",
            self.observation_count,
            self.expectancy_r,
            self.hit_rate,
            self.average_win_r,
            self.average_loss_r,
            self.profit_factor,
            self.median_holding_seconds,
        ))


def _snapshot(returns: tuple[float, ...], holding: tuple[float, ...]) -> DriftMetricSnapshot:
    if not returns or len(returns) != len(holding):
        raise ValueError("drift metric snapshot requires aligned observations")
    wins = tuple(value for value in returns if value > 0)
    losses = tuple(-value for value in returns if value < 0)
    expectancy = sum(returns) / len(returns)
    hit_rate = len(wins) / len(returns)
    average_win = sum(wins) / len(wins) if wins else None
    average_loss = sum(losses) / len(losses) if losses else None
    profit_factor = (sum(wins) / sum(losses)) if losses and wins else None
    return DriftMetricSnapshot(
        len(returns), expectancy, hit_rate, average_win, average_loss, profit_factor, float(median(holding))
    )


def _relative_drop(reference: float, observed: float) -> float:
    if reference <= 0:
        return 0.0 if observed >= reference else 1.0
    return max(0.0, (reference - observed) / reference)


@dataclass(frozen=True, slots=True)
class StrategyDriftAssessment:
    status: StrategyDriftStatus
    champion_fingerprint: str
    baseline_fingerprint: str
    replay_metrics: DriftMetricSnapshot | None
    actual_metrics: DriftMetricSnapshot | None
    strategy_signals: tuple[str, ...]
    execution_signals: tuple[str, ...]
    data_signals: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    policy_fingerprint: str
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "champion_fingerprint", _sha(self.champion_fingerprint, "drift assessment Champion"))
        object.__setattr__(self, "baseline_fingerprint", _sha(self.baseline_fingerprint, "drift assessment baseline"))
        evidence = tuple(sorted(_sha(row, "drift assessment evidence") for row in self.evidence_fingerprints))
        if len(evidence) != len(set(evidence)):
            raise ValueError("drift assessment evidence must be unique")
        object.__setattr__(self, "evidence_fingerprints", evidence)
        object.__setattr__(self, "policy_fingerprint", _sha(self.policy_fingerprint, "drift assessment policy"))
        if not str(self.reason).strip():
            raise ValueError("drift assessment requires reason")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m192-strategy-drift-assessment-v1",
            self.status.value,
            self.champion_fingerprint,
            self.baseline_fingerprint,
            self.replay_metrics.fingerprint if self.replay_metrics else None,
            self.actual_metrics.fingerprint if self.actual_metrics else None,
            self.strategy_signals,
            self.execution_signals,
            self.data_signals,
            self.evidence_fingerprints,
            self.policy_fingerprint,
            self.reason,
        ))

    @property
    def broker_write_authority(self) -> bool: return False
    @property
    def position_mutation_authority(self) -> bool: return False
    @property
    def champion_suspension_authority(self) -> bool: return False
    @property
    def promotion_authority(self) -> bool: return False
    @property
    def risk_override_authority(self) -> bool: return False
    @property
    def guardian_override_authority(self) -> bool: return False
    @property
    def provider_selection_authority(self) -> bool: return False
    @property
    def provider_weight_authority(self) -> bool: return False


def assess_strategy_drift(
    baseline: StrategyDriftBaseline,
    observations: Iterable[ForwardTradeDriftObservation],
    *,
    policy: StrategyDriftPolicy,
) -> StrategyDriftAssessment:
    rows = tuple(sorted(observations, key=lambda row: (row.observed_at, row.trade_fingerprint)))
    fingerprints = tuple(row.fingerprint for row in rows)
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("duplicate forward drift observation")
    if len({row.trade_fingerprint for row in rows}) != len(rows):
        raise ValueError("duplicate forward trade identity")
    timestamps = tuple(row.observed_at for row in rows)
    if len(timestamps) != len(set(timestamps)):
        raise ValueError("forward drift observations require unique timestamps")
    for row in rows:
        if row.champion_fingerprint != baseline.champion_fingerprint:
            raise ValueError("forward observation Champion identity drift")
        if row.observed_at <= baseline.period_end:
            raise ValueError("forward drift observation must be strictly after certified baseline")

    evidence = {baseline.fingerprint, *fingerprints}
    evidence.update(row.replay_fingerprint for row in rows)
    evidence.update(
        row.execution_evidence_fingerprint
        for row in rows
        if row.execution_evidence_fingerprint is not None
    )

    if any(row.rule_violations for row in rows):
        return StrategyDriftAssessment(
            StrategyDriftStatus.GOVERNANCE_FAILURE, baseline.champion_fingerprint, baseline.fingerprint,
            None, None, (), (), ("forward_rule_violation",), tuple(evidence), policy.fingerprint,
            "a profitable or losing rule violation is a governance failure, not statistical drift",
        )
    if any(not row.data_integrity_ok for row in rows):
        return StrategyDriftAssessment(
            StrategyDriftStatus.DATA_OR_REPLAY_DRIFT, baseline.champion_fingerprint, baseline.fingerprint,
            None, None, (), (), ("forward_data_integrity_failure",), tuple(evidence), policy.fingerprint,
            "forward data integrity failed; strategy performance cannot be trusted",
        )
    if any(row.replay_quality is ReplayQuality.DIVERGED for row in rows):
        return StrategyDriftAssessment(
            StrategyDriftStatus.DATA_OR_REPLAY_DRIFT, baseline.champion_fingerprint, baseline.fingerprint,
            None, None, (), (), ("frozen_strategy_PIT_replay_diverged",), tuple(evidence), policy.fingerprint,
            "fresh point-in-time reconstruction diverged from frozen strategy behavior",
        )

    complete_replay = tuple(row for row in rows if row.replay_quality is ReplayQuality.MATCHED)
    if len(complete_replay) < policy.minimum_observations:
        return StrategyDriftAssessment(
            StrategyDriftStatus.INSUFFICIENT, baseline.champion_fingerprint, baseline.fingerprint,
            None, None, (), (), ("insufficient_forward_PIT_replay",), tuple(evidence), policy.fingerprint,
            "insufficient chronologically later point-in-time observations",
        )

    replay_returns = tuple(float(row.replay_net_r) for row in complete_replay if row.replay_net_r is not None)
    replay_holding = tuple(float(row.replay_holding_seconds) for row in complete_replay if row.replay_holding_seconds is not None)
    replay_metrics = _snapshot(replay_returns, replay_holding)

    strategy_signals: list[str] = []
    if _relative_drop(baseline.expectancy_r, replay_metrics.expectancy_r) > policy.maximum_expectancy_drop_fraction:
        strategy_signals.append("expectancy_decay")
    if baseline.hit_rate - replay_metrics.hit_rate > policy.maximum_hit_rate_drop:
        strategy_signals.append("hit_rate_decay")
    if replay_metrics.profit_factor is not None and _relative_drop(baseline.profit_factor, replay_metrics.profit_factor) > policy.maximum_profit_factor_drop_fraction:
        strategy_signals.append("profit_factor_decay")
    if replay_metrics.average_win_r is not None and _relative_drop(baseline.average_win_r, replay_metrics.average_win_r) > policy.maximum_average_win_drop_fraction:
        strategy_signals.append("average_win_decay")
    if replay_metrics.average_loss_r is not None and replay_metrics.average_loss_r > baseline.average_loss_r * (1.0 + policy.maximum_average_loss_increase_fraction):
        strategy_signals.append("average_loss_increase")
    holding_ratio = replay_metrics.median_holding_seconds / baseline.median_holding_seconds
    if abs(holding_ratio - 1.0) > policy.maximum_holding_time_ratio_deviation:
        strategy_signals.append("holding_time_distribution_shift")

    complete_actual = tuple(
        row for row in complete_replay
        if row.actual_net_r is not None and row.actual_holding_seconds is not None
    )
    actual_metrics: DriftMetricSnapshot | None = None
    execution_signals: list[str] = []
    if len(complete_actual) >= policy.minimum_observations:
        actual_metrics = _snapshot(
            tuple(float(row.actual_net_r) for row in complete_actual),
            tuple(float(row.actual_holding_seconds) for row in complete_actual),
        )
        execution_deviation_fraction = sum(
            row.execution_quality is ExecutionQuality.DEVIATED for row in complete_actual
        ) / len(complete_actual)
        if execution_deviation_fraction > policy.maximum_execution_deviation_fraction:
            execution_signals.append("execution_deviation_rate_above_policy")
        if replay_metrics.expectancy_r - actual_metrics.expectancy_r > policy.maximum_replay_actual_expectancy_gap_r:
            execution_signals.append("actual_expectancy_below_PIT_replay")
    elif any(row.execution_quality is ExecutionQuality.DEVIATED for row in rows):
        execution_signals.append("execution_deviation_observed_but_sample_incomplete")

    strategy_count = len(set(strategy_signals))
    execution_count = len(set(execution_signals))
    strategy_tuple = tuple(sorted(set(strategy_signals)))
    execution_tuple = tuple(sorted(set(execution_signals)))

    if strategy_count >= policy.structural_signal_count:
        status = StrategyDriftStatus.STRUCTURAL_DRIFT
        reason = "multiple independent forward strategy metrics breached the frozen drift policy"
    elif strategy_count >= policy.warning_signal_count:
        status = StrategyDriftStatus.WATCH
        reason = "forward strategy metrics warrant monitoring but do not meet structural-drift confirmation"
    elif execution_count:
        status = StrategyDriftStatus.EXECUTION_DRIFT_ONLY
        reason = "frozen strategy replay remains inside policy while actual execution evidence deteriorated"
    else:
        status = StrategyDriftStatus.STABLE
        reason = "forward PIT replay remains inside the explicit frozen drift policy"

    return StrategyDriftAssessment(
        status,
        baseline.champion_fingerprint,
        baseline.fingerprint,
        replay_metrics,
        actual_metrics,
        strategy_tuple,
        execution_tuple,
        (),
        tuple(evidence),
        policy.fingerprint,
        reason,
    )
