from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from typing import Iterable, Mapping

from .experience import TradeSide
from .research import Clause, Scalar, StrategySpec


class GroupMode(StrEnum):
    ALL = "all"
    ANY = "any"


class ExecutionSensitivity(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    LATENCY_CRITICAL = "latency_critical"


class EligibilityStatus(StrEnum):
    ALLOWED = "allowed"
    RESEARCH_ONLY = "research_only"
    PROHIBITED = "prohibited"


@dataclass(frozen=True, slots=True)
class RuleGroup:
    clauses: tuple[Clause, ...]
    mode: GroupMode = GroupMode.ALL

    def __post_init__(self) -> None:
        if not self.clauses:
            raise ValueError("rule group requires at least one clause")

    def evaluate(self, features: Mapping[str, Scalar]) -> bool:
        values = (clause.evaluate(features) for clause in self.clauses)
        return all(values) if self.mode is GroupMode.ALL else any(values)


@dataclass(frozen=True, slots=True)
class ExitPlan:
    stop_rule: str
    target_rule: str = ""
    trailing_rule: str = "off"
    breakeven_rule: str = "off"
    max_hold_steps: int = 1
    max_elapsed_minutes: int | None = None

    def __post_init__(self) -> None:
        if not self.stop_rule.strip():
            raise ValueError("every strategy requires an initial stop rule")
        if self.max_hold_steps < 1:
            raise ValueError("max_hold_steps must be positive")
        if self.max_elapsed_minutes is not None and (
            type(self.max_elapsed_minutes) is not int or self.max_elapsed_minutes < 1
        ):
            raise ValueError("max_elapsed_minutes must be a positive integer or None")


@dataclass(frozen=True, slots=True)
class StrategySpecV2:
    strategy_id: str
    direction: TradeSide
    entry_groups: tuple[RuleGroup, ...]
    exit_plan: ExitPlan
    decision_timeframe_minutes: int
    intended_horizon_minutes: int
    session_filters: tuple[str, ...] = ()
    event_exclusion_minutes: int = 0
    cooldown_steps: int = 0
    scale_in_limit: int = 0
    scale_out_fractions: tuple[float, ...] = ()
    cost_bps: float = 0.0
    execution_sensitivity: ExecutionSensitivity = ExecutionSensitivity.NORMAL
    is_scalping: bool = False
    is_hft: bool = False
    martingale: bool = False
    loss_recovery_sizing: bool = False
    unbounded_averaging: bool = False
    schema_version: int = 2

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.entry_groups:
            raise ValueError("strategy id and entry rules are required")
        if self.schema_version != 2:
            raise ValueError("StrategySpecV2 schema_version must be 2")
        if self.decision_timeframe_minutes < 1 or self.intended_horizon_minutes < 1:
            raise ValueError("strategy timeframes must be positive")
        if self.event_exclusion_minutes < 0 or self.cooldown_steps < 0 or self.scale_in_limit < 0:
            raise ValueError("strategy timing/scaling controls cannot be negative")
        if self.cost_bps < 0:
            raise ValueError("cost_bps cannot be negative")
        if any(not 0.0 < value <= 1.0 for value in self.scale_out_fractions):
            raise ValueError("scale-out fractions must be in (0,1]")
        if sum(self.scale_out_fractions) > 1.0 + 1e-12:
            raise ValueError("scale-out fractions cannot exceed full position")
        if len(set(self.session_filters)) != len(self.session_filters):
            raise ValueError("session filters must be unique")

    def entry_matches(self, features: Mapping[str, Scalar]) -> bool:
        """Groups are OR'ed; each group controls its own ALL/ANY clause semantics."""
        return any(group.evaluate(features) for group in self.entry_groups)

    @property
    def strategy_hash(self) -> str:
        def clause_payload(clause: Clause) -> dict[str, object]:
            return {"feature": clause.feature, "op": clause.op.value, "value": clause.value}

        groups = []
        for group in self.entry_groups:
            groups.append(
                {
                    "mode": group.mode.value,
                    "clauses": sorted(
                        (clause_payload(clause) for clause in group.clauses),
                        key=lambda item: (str(item["feature"]), str(item["op"]), repr(item["value"])),
                    ),
                }
            )
        encoded_groups = {
            json.dumps(group, sort_keys=True, separators=(",", ":")): group for group in groups
        }
        canonical_groups = [encoded_groups[key] for key in sorted(encoded_groups)]
        exit_payload: dict[str, object] = {
            "stop_rule": self.exit_plan.stop_rule,
            "target_rule": self.exit_plan.target_rule,
            "trailing_rule": self.exit_plan.trailing_rule,
            "breakeven_rule": self.exit_plan.breakeven_rule,
            "max_hold_steps": self.exit_plan.max_hold_steps,
        }
        # Keep legacy hashes stable unless elapsed-time semantics are explicitly enabled.
        if self.exit_plan.max_elapsed_minutes is not None:
            exit_payload["max_elapsed_minutes"] = self.exit_plan.max_elapsed_minutes
        payload = {
            "schema_version": self.schema_version,
            "direction": self.direction.value,
            "entry_groups": canonical_groups,
            "exit_plan": exit_payload,
            "decision_timeframe_minutes": self.decision_timeframe_minutes,
            "intended_horizon_minutes": self.intended_horizon_minutes,
            "session_filters": sorted(self.session_filters),
            "event_exclusion_minutes": self.event_exclusion_minutes,
            "cooldown_steps": self.cooldown_steps,
            "scale_in_limit": self.scale_in_limit,
            "scale_out_fractions": list(self.scale_out_fractions),
            "cost_bps": self.cost_bps,
            "execution_sensitivity": self.execution_sensitivity.value,
            "is_scalping": self.is_scalping,
            "is_hft": self.is_hft,
            "martingale": self.martingale,
            "loss_recovery_sizing": self.loss_recovery_sizing,
            "unbounded_averaging": self.unbounded_averaging,
        }
        return sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class StrategyEligibilityPolicy:
    min_decision_timeframe_minutes: int = 5
    min_intended_horizon_minutes: int = 15
    max_new_entries_per_hour: int = 3
    high_execution_sensitivity_research_only: bool = True

    def __post_init__(self) -> None:
        if self.min_decision_timeframe_minutes < 1 or self.min_intended_horizon_minutes < 1:
            raise ValueError("eligibility time limits must be positive")
        if self.max_new_entries_per_hour < 1:
            raise ValueError("entry frequency limit must be positive")


@dataclass(frozen=True, slots=True)
class EligibilityAssessment:
    status: EligibilityStatus
    reasons: tuple[str, ...]

    @property
    def promotable(self) -> bool:
        return self.status is EligibilityStatus.ALLOWED


def assess_strategy_eligibility(
    spec: StrategySpecV2,
    policy: StrategyEligibilityPolicy = StrategyEligibilityPolicy(),
) -> EligibilityAssessment:
    """Constitutional gate. Backtest performance cannot override prohibited behavior."""
    prohibited: list[str] = []
    if spec.decision_timeframe_minutes < policy.min_decision_timeframe_minutes:
        prohibited.append("decision_timeframe_below_m5")
    if spec.intended_horizon_minutes < policy.min_intended_horizon_minutes:
        prohibited.append("intended_horizon_below_15m")
    if spec.is_scalping:
        prohibited.append("scalping_prohibited")
    if spec.is_hft:
        prohibited.append("hft_prohibited")
    if spec.execution_sensitivity is ExecutionSensitivity.LATENCY_CRITICAL:
        prohibited.append("latency_critical_prohibited")
    if spec.martingale:
        prohibited.append("martingale_prohibited")
    if spec.loss_recovery_sizing:
        prohibited.append("loss_recovery_sizing_prohibited")
    if spec.unbounded_averaging:
        prohibited.append("unbounded_averaging_prohibited")
    if prohibited:
        return EligibilityAssessment(EligibilityStatus.PROHIBITED, tuple(prohibited))
    if (
        spec.execution_sensitivity is ExecutionSensitivity.HIGH
        and policy.high_execution_sensitivity_research_only
    ):
        return EligibilityAssessment(EligibilityStatus.RESEARCH_ONLY, ("high_execution_sensitivity",))
    return EligibilityAssessment(EligibilityStatus.ALLOWED, ())


def assess_observed_entry_frequency(
    entry_times: Iterable[datetime],
    policy: StrategyEligibilityPolicy = StrategyEligibilityPolicy(),
) -> EligibilityAssessment:
    """Catch accidental machine-scalping even when declarative metadata claims otherwise."""
    times = tuple(sorted(entry_times))
    if any(value.tzinfo is None or value.utcoffset() is None for value in times):
        raise ValueError("entry timestamps must be timezone-aware")
    left = 0
    one_hour = timedelta(hours=1)
    for right, value in enumerate(times):
        while left <= right and value - times[left] >= one_hour:
            left += 1
        if right - left + 1 > policy.max_new_entries_per_hour:
            return EligibilityAssessment(EligibilityStatus.PROHIBITED, ("entry_frequency_prohibited",))
    return EligibilityAssessment(EligibilityStatus.ALLOWED, ())


def migrate_v1(
    spec: StrategySpec,
    *,
    decision_timeframe_minutes: int,
    intended_horizon_minutes: int,
    stop_rule: str,
) -> StrategySpecV2:
    """Backward-compatible v1 migration without changing the original entry semantics."""
    return StrategySpecV2(
        strategy_id=spec.strategy_id,
        direction=spec.direction,
        entry_groups=(RuleGroup(spec.clauses),),
        exit_plan=ExitPlan(stop_rule=stop_rule, max_hold_steps=spec.horizon_steps),
        decision_timeframe_minutes=decision_timeframe_minutes,
        intended_horizon_minutes=intended_horizon_minutes,
        cost_bps=spec.cost_bps,
    )
