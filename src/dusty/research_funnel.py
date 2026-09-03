"""Unified, checkpointed research funnel for bounded Dusty experiments.

The funnel joins existing M106 primitives without adding trading authority. It can cache
one frozen acquisition, derive completed features, generate deterministic V2 challengers,
run a cheap Python screen under configured and stressed costs, attribute cost-vs-exposure
changes, and emit only the first proposed MT5 fidelity step for candidates that pass every
prespecified gate. It never ranks survivors, never promotes a strategy, and never writes to
a broker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import math
from pathlib import Path
from typing import Any, Callable, Mapping

from .experience import TradeSide
from .features import FeatureBar, completed_feature_bars_from_mt5
from .investment_lab import LaboratoryConfig
from .markets import InstrumentEconomics
from .mt5lab import MT5TickMode
from .mt5worker import MT5Bar
from .research import RuleOp
from .research_challengers import (
    ChallengerPlan,
    MutationKind,
    ResearchMutation,
    generate_challengers,
)
from .research_cycle import ResearchCycle, ResearchCycleResult, ResearchStage, fingerprint
from .research_diagnostics import MatchedExposureAttribution, decompose_stressed_result
from .research_evaluation import FixedEvaluationPlan, run_fixed_evaluation
from .reviewed_strategies import ReviewedResearchPackage
from .risk import RiskConstitution


FUNNEL_PROTOCOL = "dusty-unified-research-funnel-v1"
AcquisitionRunner = Callable[[], tuple[tuple[MT5Bar, ...], InstrumentEconomics]]


@dataclass(frozen=True, slots=True)
class FunnelLaboratoryPolicy:
    growth_starting_equity: float
    strategy_test_equity: float = 100_000.0
    growth_risk_fraction: float = 0.0025
    commission_per_lot: float = 0.0
    spread_floor_points: float = 0.0
    slippage_points: float = 0.0
    risk_constitution: RiskConstitution = RiskConstitution()
    max_evidence_items: int = 64

    def __post_init__(self) -> None:
        values = (
            self.growth_starting_equity,
            self.strategy_test_equity,
            self.growth_risk_fraction,
            self.commission_per_lot,
            self.spread_floor_points,
            self.slippage_points,
        )
        if any(isinstance(value, bool) or not math.isfinite(value) for value in values):
            raise ValueError("funnel_laboratory_policy_requires_finite_numeric_values")
        if self.growth_starting_equity <= 0 or self.strategy_test_equity <= 0:
            raise ValueError("funnel_laboratory_equity_must_be_positive")
        if not 0 < self.growth_risk_fraction <= 1:
            raise ValueError("funnel_growth_risk_fraction_must_be_in_0_1")
        if min(self.commission_per_lot, self.spread_floor_points, self.slippage_points) < 0:
            raise ValueError("funnel_cost_assumptions_must_be_nonnegative")
        if type(self.max_evidence_items) is not int or self.max_evidence_items < 1:
            raise ValueError("funnel_max_evidence_items_must_be_positive_integer")


@dataclass(frozen=True, slots=True)
class FunnelScreenPolicy:
    minimum_closed_trades: int = 20
    maximum_marked_drawdown: float = 0.02
    require_positive_net_pnl: bool = True
    require_development_pass: bool = True
    require_stress_pass: bool = True
    max_native_candidates: int = 4

    def __post_init__(self) -> None:
        if type(self.minimum_closed_trades) is not int or self.minimum_closed_trades < 1:
            raise ValueError("funnel_minimum_closed_trades_must_be_positive_integer")
        if (isinstance(self.maximum_marked_drawdown, bool)
                or not math.isfinite(self.maximum_marked_drawdown)
                or self.maximum_marked_drawdown < 0):
            raise ValueError("funnel_maximum_drawdown_must_be_finite_and_nonnegative")
        if any(type(value) is not bool for value in (
            self.require_positive_net_pnl,
            self.require_development_pass,
            self.require_stress_pass,
        )):
            raise ValueError("funnel_screen_switches_must_be_boolean")
        if type(self.max_native_candidates) is not int or self.max_native_candidates < 1:
            raise ValueError("funnel_native_candidate_budget_must_be_positive_integer")


@dataclass(frozen=True, slots=True)
class FunnelPolicy:
    laboratory: FunnelLaboratoryPolicy
    screen: FunnelScreenPolicy = FunnelScreenPolicy()
    additional_round_trip_slippage_points: float = 10.0

    def __post_init__(self) -> None:
        value = self.additional_round_trip_slippage_points
        if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError("funnel_cost_stress_points_must_be_finite_and_positive")


def first_generation_challenger_plan(parent: ReviewedResearchPackage) -> ChallengerPlan:
    """Small code-reviewed search neighborhood; values are fixed before outcomes exist."""
    if parent.spec.direction is TradeSide.LONG:
        threshold = ResearchMutation(MutationKind.ENTRY_THRESHOLD, 57.5, "rsi", RuleOp.GE)
    elif parent.spec.direction is TradeSide.SHORT:
        threshold = ResearchMutation(MutationKind.ENTRY_THRESHOLD, 42.5, "rsi", RuleOp.LE)
    else:  # pragma: no cover - current research packages are directional
        raise ValueError("funnel_requires_directional_parent")
    return ChallengerPlan((
        threshold,
        ResearchMutation(MutationKind.EXIT_HORIZON_MINUTES, 180),
        ResearchMutation(MutationKind.EXIT_HORIZON_MINUTES, 300),
        ResearchMutation(MutationKind.COOLDOWN_STEPS, 0),
        ResearchMutation(MutationKind.COOLDOWN_STEPS, 8),
        ResearchMutation(MutationKind.RSI_PERIOD, 10),
        ResearchMutation(MutationKind.RSI_PERIOD, 21),
        ResearchMutation(MutationKind.FORECAST_NEUTRAL_RETURN, 0.0002),
    ), max_candidates=8)


def _serialize_source_bars(rows: tuple[MT5Bar, ...]) -> list[dict[str, Any]]:
    return [asdict(row) for row in rows]


def _as_datetime(value: Any) -> datetime:
    """Normalize fresh in-process datetimes and JSON-resumed ISO timestamps identically."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    raise ValueError("funnel_timestamp_must_be_datetime_or_iso_string")


def _source_bars(payload: Mapping[str, Any]) -> tuple[MT5Bar, ...]:
    rows = []
    for raw in payload["source_bars"]:
        values = dict(raw)
        values["at"] = _as_datetime(values["at"])
        rows.append(MT5Bar(**values))
    return tuple(rows)


def _feature_bars(payload: Mapping[str, Any]) -> tuple[FeatureBar, ...]:
    rows = []
    for raw in payload["feature_bars"]:
        values = dict(raw)
        values["at"] = _as_datetime(values["at"])
        if values.get("source_open_at") is not None:
            values["source_open_at"] = _as_datetime(values["source_open_at"])
        rows.append(FeatureBar(**values))
    return tuple(rows)


def _economics(payload: Mapping[str, Any]) -> InstrumentEconomics:
    return InstrumentEconomics(**dict(payload["economics"]))


def decode_acquisition(result: ResearchCycleResult) -> tuple[tuple[MT5Bar, ...], InstrumentEconomics]:
    """Recover verified acquisition output for the legacy report path without another MT5 read."""
    acquisition = result.output_map()["acquisition"]
    return _source_bars(acquisition), _economics(acquisition)


def _candidate_packages(
    parent: ReviewedResearchPackage,
    challenger_plan: ChallengerPlan,
) -> tuple[tuple[str, ReviewedResearchPackage, dict[str, Any] | None], ...]:
    rows: list[tuple[str, ReviewedResearchPackage, dict[str, Any] | None]] = [
        ("parent", parent, None)
    ]
    for draft in generate_challengers(parent, challenger_plan):
        rows.append((draft.candidate_fingerprint, draft.package, draft.mutation.payload))
    return tuple(rows)


def _laboratory_config(
    package: ReviewedResearchPackage,
    policy: FunnelLaboratoryPolicy,
    economics: InstrumentEconomics,
    *,
    additional_slippage_points: float,
) -> LaboratoryConfig:
    if economics.point_size <= 0:
        raise ValueError("funnel_requires_positive_broker_point_size")
    return LaboratoryConfig(
        feature_config=package.features,
        cognition_policy=package.cognition,
        risk_constitution=policy.risk_constitution,
        strategy_test_equity=policy.strategy_test_equity,
        growth_starting_equity=policy.growth_starting_equity,
        growth_risk_fraction=policy.growth_risk_fraction,
        spread_price=policy.spread_floor_points * economics.point_size,
        expected_slippage_price=(policy.slippage_points + additional_slippage_points) * economics.point_size,
        commission_per_lot=policy.commission_per_lot,
        max_evidence_items=policy.max_evidence_items,
    )


def _approved_volume_from_dict(traces: list[dict[str, Any]]) -> float:
    total = 0.0
    for trace in traces:
        if not trace["approved"]:
            continue
        sizing = trace["sizing"]
        if sizing is None:
            raise ValueError("approved_funnel_growth_trace_lacks_sizing")
        total += float(sizing["approved_volume"])
    return total


def _approved_volume_from_run(run: Any) -> float:
    total = 0.0
    for trace in run.growth_sizing:
        if not trace.approved:
            continue
        if trace.sizing is None:
            raise ValueError("approved_funnel_growth_trace_lacks_sizing")
        total += trace.sizing.approved_volume
    return total


def _segment_screen(metrics: Mapping[str, Any], policy: FunnelScreenPolicy) -> dict[str, Any]:
    reasons: list[str] = []
    if metrics["growth_trades"] < policy.minimum_closed_trades:
        reasons.append("insufficient_closed_trades")
    if policy.require_positive_net_pnl and metrics["growth_net_pnl"] <= 0:
        reasons.append("nonpositive_net_pnl")
    if metrics["growth_drawdown"] > policy.maximum_marked_drawdown:
        reasons.append("marked_drawdown_above_limit")
    return {"passed": not reasons, "reasons": reasons}


def _combined_pass(scenarios: Mapping[str, Any], policy: FunnelScreenPolicy) -> tuple[bool, list[str]]:
    required = [("configured", "holdout")]
    if policy.require_development_pass:
        required.append(("configured", "development"))
    if policy.require_stress_pass:
        required.append(("stress", "holdout"))
        if policy.require_development_pass:
            required.append(("stress", "development"))
    failures = [f"{scenario}:{segment}" for scenario, segment in required
                if not scenarios[scenario]["segments"][segment]["screen"]["passed"]]
    return not failures, failures


def _candidate_manifest(parent: ReviewedResearchPackage, challenger_plan: ChallengerPlan) -> list[dict[str, Any]]:
    return [
        {
            "candidate_id": candidate_id,
            "package_fingerprint": package.fingerprint,
            "strategy_hash": package.spec.strategy_hash,
            "mutation": mutation,
            "promotion_eligible": False,
        }
        for candidate_id, package, mutation in _candidate_packages(parent, challenger_plan)
    ]


class UnifiedResearchFunnel:
    """One immutable cheap-to-expensive research plan backed by ResearchCycle checkpoints."""

    def __init__(self, root: Path) -> None:
        self._cycle = ResearchCycle(root)

    def run(
        self,
        request: Mapping[str, Any],
        *,
        parent: ReviewedResearchPackage,
        challenger_plan: ChallengerPlan,
        evaluation_plan: FixedEvaluationPlan,
        policy: FunnelPolicy,
        acquire: AcquisitionRunner,
    ) -> ResearchCycleResult:
        if not callable(acquire):
            raise ValueError("funnel_requires_acquisition_runner")
        identity = dict(request)
        if not isinstance(identity.get("code_commit"), str) or not identity["code_commit"].strip():
            raise ValueError("funnel_requires_code_commit")
        identity.update({
            "funnel_protocol": FUNNEL_PROTOCOL,
            "parent_package_fingerprint": parent.fingerprint,
            "challenger_plan_fingerprint": challenger_plan.fingerprint,
            "evaluation_plan": evaluation_plan.payload(),
            "evaluation_plan_fingerprint": evaluation_plan.fingerprint,
            "funnel_policy": asdict(policy),
        })

        def acquisition_stage(_: Mapping[str, Any]) -> dict[str, Any]:
            source, economics = acquire()
            if not source:
                raise ValueError("funnel_acquisition_returned_no_bars")
            if tuple(sorted(source, key=lambda row: row.at)) != source or len({row.at for row in source}) != len(source):
                raise ValueError("funnel_acquisition_requires_unique_chronological_bars")
            if any(row.at.minute % 15 or row.at.second or row.at.microsecond for row in source):
                raise ValueError("funnel_acquisition_requires_M15_source_bars")
            if any(not evaluation_plan.start <= row.at <= evaluation_plan.end for row in source):
                raise ValueError("funnel_acquisition_outside_frozen_evaluation_window")
            serialized = _serialize_source_bars(source)
            return {
                "source_bars": serialized,
                "source_bar_count": len(source),
                "source_fingerprint": fingerprint(serialized),
                "economics": asdict(economics),
                "economics_fingerprint": fingerprint(asdict(economics)),
            }

        def feature_stage(outputs: Mapping[str, Any]) -> dict[str, Any]:
            source = _source_bars(outputs["acquisition"])
            completed = completed_feature_bars_from_mt5(source)
            if len(completed) < 64:
                raise ValueError("funnel_requires_at_least_64_completed_feature_bars")
            serialized = [asdict(row) for row in completed]
            return {
                "feature_bars": serialized,
                "feature_bar_count": len(completed),
                "feature_fingerprint": fingerprint(serialized),
            }

        def challenger_stage(_: Mapping[str, Any]) -> dict[str, Any]:
            manifest = _candidate_manifest(parent, challenger_plan)
            if len(manifest) > challenger_plan.max_candidates + 1:
                raise ValueError("funnel_candidate_manifest_exceeded_budget")
            return {
                "candidate_count": len(manifest),
                "parent_package_fingerprint": parent.fingerprint,
                "challenger_plan_fingerprint": challenger_plan.fingerprint,
                "candidates": manifest,
                "ranking_performed": False,
                "promotion_eligible": False,
            }

        def cheap_screen_stage(outputs: Mapping[str, Any]) -> dict[str, Any]:
            bars = _feature_bars(outputs["features"])
            economics = _economics(outputs["acquisition"])
            manifest = outputs["challengers"]["candidates"]
            packages = _candidate_packages(parent, challenger_plan)
            expected = {(row[0], row[1].fingerprint, row[1].spec.strategy_hash) for row in packages}
            observed = {(row["candidate_id"], row["package_fingerprint"], row["strategy_hash"]) for row in manifest}
            if observed != expected:
                raise ValueError("funnel_candidate_manifest_drift")
            cases = []
            for candidate_id, package, mutation in packages:
                scenarios: dict[str, Any] = {}
                for scenario, extra_points in (
                    ("configured", 0.0),
                    ("stress", policy.additional_round_trip_slippage_points),
                ):
                    config = _laboratory_config(
                        package,
                        policy.laboratory,
                        economics,
                        additional_slippage_points=extra_points,
                    )
                    holdout, evaluation = run_fixed_evaluation(
                        package.compiled,
                        bars,
                        symbol=str(identity["symbol"]),
                        economics=economics,
                        config=config,
                        plan=evaluation_plan,
                    )
                    segments: dict[str, Any] = {}
                    for segment, metrics in evaluation["segments"].items():
                        if segment == "development":
                            approved_volume = _approved_volume_from_dict(
                                evaluation["development_laboratory"]["growth_sizing"]
                            )
                        else:
                            approved_volume = _approved_volume_from_run(holdout)
                        segments[segment] = {
                            **metrics,
                            "approved_volume_lots": approved_volume,
                            "screen": _segment_screen(metrics, policy.screen),
                        }
                    scenarios[scenario] = {
                        "additional_round_trip_slippage_points": extra_points,
                        "segments": segments,
                    }
                passed, failures = _combined_pass(scenarios, policy.screen)
                cases.append({
                    "candidate_id": candidate_id,
                    "package_fingerprint": package.fingerprint,
                    "strategy_hash": package.spec.strategy_hash,
                    "mutation": mutation,
                    "scenarios": scenarios,
                    "combined_screen_passed": passed,
                    "combined_screen_failures": failures,
                    "promotion_eligible": False,
                })
            return {
                "screen_policy": asdict(policy.screen),
                "cases": cases,
                "survivor_ids": sorted(row["candidate_id"] for row in cases if row["combined_screen_passed"]),
                "ranking_performed": False,
                "promotion_eligible": False,
            }

        def diagnostic_stage(outputs: Mapping[str, Any]) -> dict[str, Any]:
            economics = _economics(outputs["acquisition"])
            extra_cost_per_lot = (
                policy.additional_round_trip_slippage_points
                * economics.point_size / economics.tick_size * economics.tick_value
            )
            rows = []
            for case in outputs["cheap_screen"]["cases"]:
                configured = case["scenarios"]["configured"]["segments"]
                stress = case["scenarios"]["stress"]["segments"]
                for segment in ("development", "holdout"):
                    original = configured[segment]
                    actual = stress[segment]
                    same_exposure = (
                        original["growth_net_pnl"]
                        - extra_cost_per_lot * original["approved_volume_lots"]
                    )
                    attribution = MatchedExposureAttribution(
                        trade_count=original["growth_trades"],
                        original_net_pnl=original["growth_net_pnl"],
                        stressed_net_pnl_same_exposure=same_exposure,
                        additional_cost_effect=same_exposure - original["growth_net_pnl"],
                    )
                    decomposition = decompose_stressed_result(
                        attribution,
                        actual["growth_net_pnl"],
                    )
                    rows.append({
                        "candidate_id": case["candidate_id"],
                        "segment": segment,
                        "approved_volume_lots_frozen": original["approved_volume_lots"],
                        "additional_cost_per_lot": extra_cost_per_lot,
                        "original_net_pnl": decomposition.original_net_pnl,
                        "stressed_net_pnl_same_exposure": attribution.stressed_net_pnl_same_exposure,
                        "additional_cost_effect_same_exposure": decomposition.additional_cost_effect_same_exposure,
                        "exposure_or_sequence_effect": decomposition.exposure_or_sequence_effect,
                        "actual_stressed_net_pnl": decomposition.actual_stressed_net_pnl,
                        "actual_total_change": decomposition.total_change,
                    })
            return {
                "matched_exposure_cost_attribution": rows,
                "forecast_veto_attribution": {
                    "status": "NOT_RUN_NO_FORECAST_PROVIDER_IN_FUNNEL_V1",
                    "required_before_forecast_veto_claim": True,
                },
                "promotion_eligible": False,
            }

        def fidelity_stage(outputs: Mapping[str, Any]) -> dict[str, Any]:
            survivors = outputs["cheap_screen"]["survivor_ids"]
            by_id = {row["candidate_id"]: row for row in outputs["challengers"]["candidates"]}
            if len(survivors) > policy.screen.max_native_candidates:
                return {
                    "status": "BUDGET_BLOCKED_TOO_MANY_SURVIVORS",
                    "survivor_count": len(survivors),
                    "native_candidate_budget": policy.screen.max_native_candidates,
                    "proposals": [],
                    "ranking_performed": False,
                    "promotion_eligible": False,
                }
            proposals = [
                {
                    "candidate_id": candidate_id,
                    "package_fingerprint": by_id[candidate_id]["package_fingerprint"],
                    "strategy_hash": by_id[candidate_id]["strategy_hash"],
                    "symbol": str(identity["symbol"]),
                    "timeframe": str(identity.get("timeframe", "M15")),
                    "start": evaluation_plan.start,
                    "end": evaluation_plan.end,
                    "tick_mode": MT5TickMode.OPEN_PRICES,
                    "broker_write_authorized": False,
                    "requires_reconciliation_before_advance": True,
                    "promotion_eligible": False,
                }
                for candidate_id in survivors
            ]
            return {
                "status": "READY_FOR_OPEN_PRICES_VALIDATION" if proposals else "NO_CANDIDATE_PASSED_PYTHON_SCREEN",
                "survivor_count": len(survivors),
                "native_candidate_budget": policy.screen.max_native_candidates,
                "proposals": proposals,
                "ranking_performed": False,
                "promotion_eligible": False,
            }

        stages = (
            ResearchStage("acquisition", "1", acquisition_stage),
            ResearchStage("features", "1", feature_stage),
            ResearchStage("challengers", "1", challenger_stage),
            ResearchStage("cheap_screen", "1", cheap_screen_stage),
            ResearchStage("diagnostics", "1", diagnostic_stage),
            ResearchStage("fidelity_queue", "1", fidelity_stage),
        )
        return self._cycle.run(identity, stages)


def compact_funnel_report(result: ResearchCycleResult) -> dict[str, Any]:
    """Small report payload; raw acquisition/features remain only in verified checkpoints."""
    outputs = result.output_map()
    return {
        "protocol": FUNNEL_PROTOCOL,
        "cycle_fingerprint": result.cycle_fingerprint,
        "cache_hit": result.cache_hit,
        "reused_stages": list(result.reused_stages),
        "challengers": outputs["challengers"],
        "cheap_screen": outputs["cheap_screen"],
        "diagnostics": outputs["diagnostics"],
        "fidelity_queue": outputs["fidelity_queue"],
        "promotion_eligible": False,
        "selected_winner": None,
    }
