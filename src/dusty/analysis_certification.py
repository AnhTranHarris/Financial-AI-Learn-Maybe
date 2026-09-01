from __future__ import annotations

import json
import math
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Iterable

from .analytical_tools import TemporalBehavior, ToolLifecycle
from .strategy_v3 import FrozenStrategyDeployment
from .tool_evaluation import PerformanceWindow


REQUIRED_ANALYSIS_MILESTONES = tuple(f"M{number}" for number in range(76, 85))


@dataclass(frozen=True, slots=True)
class FirmMandate:
    mandate_id: str
    minimum_independent_windows: int = 3
    minimum_trades_per_window: int = 100
    minimum_expectancy: float = 0.0
    minimum_profit_factor: float = 1.05
    maximum_drawdown_fraction: float = 0.20
    maximum_profit_concentration: float = 0.35
    required_desk_passes: int = 6

    def __post_init__(self) -> None:
        if not self.mandate_id.strip():
            raise ValueError("firm mandate requires identity")
        if min(self.minimum_independent_windows, self.minimum_trades_per_window, self.required_desk_passes) < 1:
            raise ValueError("firm mandate count thresholds must be positive")
        values = (
            self.minimum_expectancy,
            self.minimum_profit_factor,
            self.maximum_drawdown_fraction,
            self.maximum_profit_concentration,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("firm mandate thresholds must be finite")
        if self.minimum_profit_factor < 1 or not 0 < self.maximum_drawdown_fraction <= 1:
            raise ValueError("firm mandate profit/drawdown thresholds are invalid")
        if not 0 <= self.maximum_profit_concentration <= 1:
            raise ValueError("firm mandate concentration must be in [0,1]")

    @property
    def fingerprint(self) -> str:
        return sha256(
            _canonical(
                {
                    "mandate_id": self.mandate_id,
                    "minimum_independent_windows": self.minimum_independent_windows,
                    "minimum_trades_per_window": self.minimum_trades_per_window,
                    "minimum_expectancy": self.minimum_expectancy,
                    "minimum_profit_factor": self.minimum_profit_factor,
                    "maximum_drawdown_fraction": self.maximum_drawdown_fraction,
                    "maximum_profit_concentration": self.maximum_profit_concentration,
                    "required_desk_passes": self.required_desk_passes,
                }
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class DeskStrategyEvidence:
    desk_id: str
    generation_id: str
    session_fingerprint: str
    performance: PerformanceWindow
    native_execution_parity: bool
    analytical_tool_drift: bool = False

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.desk_id, self.generation_id, self.session_fingerprint)):
            raise ValueError("desk strategy evidence requires desk/generation/session identity")


@dataclass(frozen=True, slots=True)
class MandateAssessment:
    passed: bool
    passing_desks: int
    reasons: tuple[str, ...]
    mandate_fingerprint: str


def assess_firm_mandate(
    mandate: FirmMandate,
    independent_windows: Iterable[PerformanceWindow],
    desk_runs: Iterable[DeskStrategyEvidence],
) -> MandateAssessment:
    reasons: list[str] = []
    windows = tuple(independent_windows)
    if len({row.window_id for row in windows}) != len(windows):
        reasons.append("duplicate_independent_window")
    if len(windows) < mandate.minimum_independent_windows:
        reasons.append("insufficient_independent_windows")
    for row in windows:
        reasons.extend(_performance_failures(row, mandate, f"window:{row.window_id}"))

    desks = tuple(desk_runs)
    if len({row.desk_id for row in desks}) != len(desks):
        reasons.append("duplicate_desk_id")
    passing = 0
    for row in desks:
        failures = _performance_failures(row.performance, mandate, f"desk:{row.desk_id}")
        if not row.native_execution_parity:
            failures.append(f"desk:{row.desk_id}:native_execution_parity_failed")
        if row.analytical_tool_drift:
            failures.append(f"desk:{row.desk_id}:analytical_tool_drift")
        if failures:
            reasons.extend(failures)
        else:
            passing += 1
    if passing < mandate.required_desk_passes:
        reasons.append("insufficient_passing_desks")
    if any(row.performance.rule_violations for row in desks):
        reasons.append("desk_generation_contains_rule_violation")
    return MandateAssessment(not reasons, passing, tuple(reasons), mandate.fingerprint)


def _performance_failures(row: PerformanceWindow, mandate: FirmMandate, prefix: str) -> list[str]:
    reasons: list[str] = []
    if row.trade_count < mandate.minimum_trades_per_window:
        reasons.append(f"{prefix}:insufficient_trades")
    if row.expectancy <= mandate.minimum_expectancy:
        reasons.append(f"{prefix}:expectancy_failed")
    if row.profit_factor < mandate.minimum_profit_factor:
        reasons.append(f"{prefix}:profit_factor_failed")
    if row.maximum_drawdown_fraction > mandate.maximum_drawdown_fraction:
        reasons.append(f"{prefix}:drawdown_failed")
    if row.profit_concentration > mandate.maximum_profit_concentration:
        reasons.append(f"{prefix}:profit_concentration_failed")
    if row.rule_violations:
        reasons.append(f"{prefix}:rule_violation")
    return reasons


@dataclass(frozen=True, slots=True)
class RuntimeToolObservation:
    fingerprint: str
    state: ToolLifecycle
    artifact_hash_matches: bool
    native_value_available: bool
    stale_seconds: float

    def __post_init__(self) -> None:
        if len(self.fingerprint) != 64 or not math.isfinite(self.stale_seconds) or self.stale_seconds < 0:
            raise ValueError("runtime tool observation is invalid")


@dataclass(frozen=True, slots=True)
class RuntimeAnalysisAssessment:
    new_entries_authorized: bool
    position_supervision_required: bool
    reasons: tuple[str, ...]


def assess_runtime_analysis(
    deployment: FrozenStrategyDeployment,
    *,
    strategy_hash: str,
    graph_hash: str,
    tools: Iterable[RuntimeToolObservation],
    maximum_stale_seconds: float,
) -> RuntimeAnalysisAssessment:
    if not math.isfinite(maximum_stale_seconds) or maximum_stale_seconds <= 0:
        raise ValueError("runtime staleness threshold must be positive")
    rows = tuple(tools)
    tool_ids = tuple(row.fingerprint for row in rows)
    bound, reasons_tuple = deployment.verify(
        strategy_hash=strategy_hash,
        graph_hash=graph_hash,
        tool_fingerprints=tool_ids,
    )
    reasons = list(reasons_tuple)
    if len(set(tool_ids)) != len(tool_ids):
        reasons.append("duplicate_runtime_tool")
    allowed = {ToolLifecycle.CERTIFIED_DEPENDENCY, ToolLifecycle.REGIME_RESTRICTED}
    for row in rows:
        prefix = row.fingerprint[:12]
        if row.state not in allowed:
            reasons.append(f"tool_not_certified:{prefix}:{row.state.value}")
        if not row.artifact_hash_matches:
            reasons.append(f"tool_hash_drift:{prefix}")
        if not row.native_value_available:
            reasons.append(f"tool_value_unavailable:{prefix}")
        if row.stale_seconds > maximum_stale_seconds:
            reasons.append(f"tool_value_stale:{prefix}")
    return RuntimeAnalysisAssessment(bound and not reasons, True, tuple(reasons))


@dataclass(frozen=True, slots=True)
class AnalysisMilestoneEvidence:
    milestone: str
    passed: bool
    artifact_hash: str
    data_fingerprint: str
    config_fingerprint: str
    test_fingerprint: str
    commit_sha: str

    def __post_init__(self) -> None:
        if self.milestone not in REQUIRED_ANALYSIS_MILESTONES:
            raise ValueError(f"unsupported analysis milestone: {self.milestone}")
        if any(not value.strip() for value in (self.artifact_hash, self.data_fingerprint, self.config_fingerprint, self.test_fingerprint, self.commit_sha)):
            raise ValueError("analysis milestone evidence is incomplete")

    @property
    def evidence_hash(self) -> str:
        return sha256(
            _canonical(
                {
                    "milestone": self.milestone,
                    "passed": self.passed,
                    "artifact_hash": self.artifact_hash,
                    "data_fingerprint": self.data_fingerprint,
                    "config_fingerprint": self.config_fingerprint,
                    "test_fingerprint": self.test_fingerprint,
                    "commit_sha": self.commit_sha,
                }
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class AnalyticalDependencyEvidence:
    fingerprint: str
    state: ToolLifecycle
    temporal_behavior: TemporalBehavior
    native_parity_passed: bool
    backtest_demo_parity_passed: bool
    artifact_hash_matches: bool

    def __post_init__(self) -> None:
        if len(self.fingerprint) != 64:
            raise ValueError("analytical dependency identity must be SHA-256")


@dataclass(frozen=True, slots=True)
class AnalysisPhaseCertification:
    indicator_chart_certified: bool
    demo_strategy_certified: bool
    live_compatible_research_package: bool
    live_write_authorized: bool
    certification_hash: str
    reasons: tuple[str, ...]


def certify_analysis_phase(
    evidence: Iterable[AnalysisMilestoneEvidence],
    dependencies: Iterable[AnalyticalDependencyEvidence],
    mandate: MandateAssessment,
    *,
    current_commit_sha: str,
    m75_operational_proof_hash: str,
) -> AnalysisPhaseCertification:
    if not current_commit_sha.strip() or len(m75_operational_proof_hash) != 64:
        raise ValueError("analysis certification requires commit and M75 operational proof")
    reasons: list[str] = []
    rows = tuple(evidence)
    by_milestone: dict[str, AnalysisMilestoneEvidence] = {}
    for row in rows:
        if row.milestone in by_milestone:
            reasons.append(f"duplicate_evidence:{row.milestone}")
        else:
            by_milestone[row.milestone] = row
    for milestone in REQUIRED_ANALYSIS_MILESTONES:
        row = by_milestone.get(milestone)
        if row is None:
            reasons.append(f"missing_evidence:{milestone}")
            continue
        if not row.passed:
            reasons.append(f"milestone_failed:{milestone}")
        if row.commit_sha != current_commit_sha:
            reasons.append(f"commit_mismatch:{milestone}")

    deps = tuple(dependencies)
    if not deps:
        reasons.append("no_analytical_dependencies")
    if len({row.fingerprint for row in deps}) != len(deps):
        reasons.append("duplicate_analytical_dependency")
    allowed = {ToolLifecycle.CERTIFIED_DEPENDENCY, ToolLifecycle.REGIME_RESTRICTED}
    for row in deps:
        prefix = row.fingerprint[:12]
        if row.state not in allowed:
            reasons.append(f"dependency_not_certified:{prefix}")
        if row.temporal_behavior is not TemporalBehavior.CAUSAL_COMPLETED_BAR:
            reasons.append(f"dependency_temporal_failure:{prefix}")
        if not row.native_parity_passed:
            reasons.append(f"dependency_native_parity_failed:{prefix}")
        if not row.backtest_demo_parity_passed:
            reasons.append(f"dependency_demo_parity_failed:{prefix}")
        if not row.artifact_hash_matches:
            reasons.append(f"dependency_hash_drift:{prefix}")
    if not mandate.passed:
        reasons.extend(f"mandate:{reason}" for reason in mandate.reasons)

    payload = {
        "schema": "dusty-analysis-phase-certification-v1",
        "commit": current_commit_sha,
        "m75_operational_proof_hash": m75_operational_proof_hash,
        "milestones": [(name, by_milestone[name].evidence_hash) for name in REQUIRED_ANALYSIS_MILESTONES if name in by_milestone],
        "dependencies": [(row.fingerprint, row.state.value) for row in sorted(deps, key=lambda item: item.fingerprint)],
        "mandate": mandate.mandate_fingerprint,
        "reasons": reasons,
        "live_write_authorized": False,
    }
    digest = sha256(_canonical(payload).encode("utf-8")).hexdigest()
    passed = not reasons
    return AnalysisPhaseCertification(passed, passed, passed, False, digest, tuple(reasons))


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)

