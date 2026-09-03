"""A prespecified historical comparison, not an optimizer or deployment selector.

All five candidates and both cost scenarios are retained. Every case starts flat
at the same capital. Changing entry eligibility can change later occupancy and
cooldown, so filtered trades need not be a subset of the seed's executed trades.
"""
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime
from hashlib import sha256
import json
import math
from typing import Sequence

from .core import Decision
from .features import FeatureBar, compute_standard_features
from .investment_lab import LaboratoryConfig
from .markets import InstrumentEconomics
from .research_diagnostics import MatchedExposureAttribution, decompose_stressed_result
from .research_eligibility import EntryPolicy, entry_eligibility
from .research_evaluation import FixedEvaluationPlan, run_fixed_evaluation
from .reviewed_strategies import reviewed_research_packages


COMPARISON_PROTOCOL = "fixed-seeds-entry-veto-cost-comparison-v1"


def _fingerprint(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False,
                             default=_timestamp).encode()).hexdigest()


def _timestamp(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("unsupported_comparison_fingerprint_value")


def _candidates():
    packages = reviewed_research_packages()
    return tuple((package, policy) for package in packages for policy in (EntryPolicy.SEED, EntryPolicy.TREND)) + (
        (packages[0], EntryPolicy.NO_TRADE),)


def comparison_contract() -> dict:
    """Embedded in the frozen request BEFORE history acquisition or results."""
    return {
        "protocol": COMPARISON_PROTOCOL,
        "candidates": [{"id": "no-trade" if policy is EntryPolicy.NO_TRADE else f"{package.spec.direction.value}:{policy.value}",
                        "package_fingerprint": package.fingerprint, "strategy": asdict(package.spec),
                        "features": asdict(package.features), "cognition": asdict(package.cognition),
                        "entry_policy": policy.value} for package, policy in _candidates()],
        "trend_rule": {"long": "close > sma_20 AND ema_20 > sma_20",
                       "short": "close < sma_20 AND ema_20 < sma_20",
                       "missing_or_equal": "veto", "timing": "completed_bar_availability",
                       "authority": "additional_entry_veto_only"},
        "cost_scenarios": [{"id": "configured", "additional_round_trip_slippage_points": 0.0},
                           {"id": "stress-plus-10-points", "additional_round_trip_slippage_points": 10.0}],
        "screen": {"minimum_closed_trades": 20, "net_pnl_strictly_above": 0.0,
                   "maximum_marked_drawdown": 0.02},
        "expected_cases": 20, "automatic_selection": False, "promotion_eligible": False,
        "deployment_decision": "ABSTAIN_UNQUALIFIED",
    }


def _screen(metrics: dict, screen: dict, *, control: bool) -> dict:
    reasons = []
    if control:
        reasons.append("no_trade_control_not_a_qualifiable_strategy")
    if metrics["growth_trades"] < screen["minimum_closed_trades"]:
        reasons.append("fewer_than_20_closed_trades")
    if metrics["growth_net_pnl"] <= screen["net_pnl_strictly_above"]:
        reasons.append("nonpositive_net_pnl")
    if metrics["growth_drawdown"] > screen["maximum_marked_drawdown"]:
        reasons.append("marked_drawdown_exceeds_2_percent")
    return {"limited_screen_passed": not reasons, "reasons": reasons, "promotion_eligible": False}


def _matched_cost_attributions(cases: Sequence[dict], contract: dict,
                               economics: InstrumentEconomics) -> list[dict]:
    """Hold original approved volume fixed to isolate pure cost drag from re-sizing."""
    scenarios = contract["cost_scenarios"]
    configured = next(row for row in scenarios if row["additional_round_trip_slippage_points"] == 0)
    stressed = tuple(row for row in scenarios if row["additional_round_trip_slippage_points"] > 0)
    index = {(case["candidate_id"], case["cost_scenario"], case["segment"]): case for case in cases}
    results: list[dict] = []
    for candidate in contract["candidates"]:
        for segment in ("development", "holdout"):
            baseline = index[(candidate["id"], configured["id"], segment)]
            approved_volume = sum(
                trace["sizing"]["approved_volume"]
                for trace in baseline["growth_sizing"]
                if trace["approved"] and trace["sizing"] is not None
            )
            for stress in stressed:
                actual = index[(candidate["id"], stress["id"], segment)]
                extra_points = (
                    stress["additional_round_trip_slippage_points"]
                    - configured["additional_round_trip_slippage_points"]
                )
                extra_cost_per_lot = extra_points * economics.point_size / economics.tick_size * economics.tick_value
                original_net = baseline["metrics"]["growth_net_pnl"]
                same_exposure_net = original_net - extra_cost_per_lot * approved_volume
                attribution = MatchedExposureAttribution(
                    trade_count=baseline["metrics"]["growth_trades"],
                    original_net_pnl=original_net,
                    stressed_net_pnl_same_exposure=same_exposure_net,
                    additional_cost_effect=same_exposure_net - original_net,
                )
                decomposition = decompose_stressed_result(
                    attribution,
                    actual["metrics"]["growth_net_pnl"],
                )
                results.append({
                    "candidate_id": candidate["id"],
                    "segment": segment,
                    "configured_cost_scenario": configured["id"],
                    "stressed_cost_scenario": stress["id"],
                    "original_growth_trades": attribution.trade_count,
                    "original_approved_volume_lots": approved_volume,
                    "additional_cost_per_lot": extra_cost_per_lot,
                    "original_net_pnl": decomposition.original_net_pnl,
                    "stressed_net_pnl_same_exposure": attribution.stressed_net_pnl_same_exposure,
                    "additional_cost_effect_same_exposure": decomposition.additional_cost_effect_same_exposure,
                    "exposure_or_sequence_effect": decomposition.exposure_or_sequence_effect,
                    "actual_stressed_net_pnl": decomposition.actual_stressed_net_pnl,
                    "actual_total_change": decomposition.total_change,
                })
    return results


def run_research_comparison(bars: Sequence[FeatureBar], *, symbol: str, economics: InstrumentEconomics,
                            config: LaboratoryConfig, plan: FixedEvaluationPlan) -> dict:
    if (isinstance(economics.point_size, bool) or not math.isfinite(economics.point_size)
            or economics.point_size <= 0):
        raise ValueError("comparison_requires_known_positive_broker_point_size")
    rows = tuple(bars)
    contract = comparison_contract()
    data_fingerprint = _fingerprint([asdict(row) for row in rows])
    cases = []
    eligibility = {}
    for (package, policy), candidate in zip(_candidates(), contract["candidates"], strict=True):
        vectors = compute_standard_features(rows, package.features)
        permissions = {vector.at: entry_eligibility(vector, package.spec.direction, policy) for vector in vectors}
        eligibility[candidate["id"]] = [asdict(permissions[vector.at]) for vector in vectors
                                         if plan.start <= vector.at < plan.end]
        for cost in contract["cost_scenarios"]:
            scenario_config = replace(config, feature_config=package.features, cognition_policy=package.cognition,
                                      expected_slippage_price=config.expected_slippage_price
                                      + cost["additional_round_trip_slippage_points"] * economics.point_size)
            holdout, evaluation = run_fixed_evaluation(package.compiled, rows, symbol=symbol, economics=economics,
                                                      config=scenario_config, plan=plan, entry_policy=policy)
            holdout_details = {"potential_trades": [asdict(t) for t in holdout.potential_trades],
                                    "growth_sizing": [asdict(t) for t in holdout.growth_sizing],
                                    "minimum_lot_backtest": asdict(holdout.minimum_lot_backtest),
                                    "growth_backtest": asdict(holdout.growth_backtest)}
            for segment, metrics in evaluation["segments"].items():
                development = evaluation["development_laboratory"]
                details = development if segment == "development" else holdout_details
                cognition = development["cognition"] if segment == "development" else (
                    {"at": t.at, "decision": t.decision} for t in holdout.cognition)
                cutoff = datetime.fromisoformat(metrics["entry_cutoff_exclusive"])
                eligible_signals = tuple(t for t in cognition if t["at"] < cutoff and
                                         t["decision"] in (Decision.ENTRY_LONG, Decision.ENTRY_SHORT))
                blocked = Counter(permissions[t["at"]].reason for t in eligible_signals
                                  if not permissions[t["at"]].allowed)
                identity = {"contract_fingerprint": _fingerprint(contract), "data_fingerprint": data_fingerprint,
                            "candidate": candidate,
                            "cost_scenario": cost, "config": asdict(scenario_config), "plan": plan.payload(),
                            "symbol": symbol, "economics": asdict(economics), "segment": segment}
                case = {"case_fingerprint": _fingerprint(identity), "candidate_id": candidate["id"],
                        "cost_scenario": cost["id"], "segment": segment, "metrics": metrics,
                        "config": asdict(scenario_config),
                        "blocked_cognition_signals_before_tail": sum(blocked.values()),
                        "blocked_reasons": dict(sorted(blocked.items())),
                        "screen": _screen(metrics, contract["screen"], control=policy is EntryPolicy.NO_TRADE)}
                for key in ("potential_trades", "growth_sizing", "minimum_lot_backtest", "growth_backtest"):
                    case[key] = details[key]
                cases.append(case)
    if len(cases) != contract["expected_cases"] or len({c["case_fingerprint"] for c in cases}) != len(cases):
        raise ValueError("incomplete_or_duplicate_comparison_matrix")
    matched_cost = _matched_cost_attributions(cases, contract, economics)
    return {"contract": contract, "contract_fingerprint": _fingerprint(contract), "cases": cases,
            "data_fingerprint": data_fingerprint,
            "entry_eligibility": eligibility,
            "matched_exposure_cost_attribution": matched_cost,
            "deployment_decision": "ABSTAIN_UNQUALIFIED",
            "promotion_eligible": False, "selected_winner": None,
            "limitations": ["historical_prior_exposure_unknown", "repeated_trials_not_independent",
                            "no_multiple_testing_adjusted_confidence", "trend_veto_not_a_trained_forecast",
                            "cost_inputs_unverified_stress_is_hypothetical",
                            "actual_stress_result_can_change_sizing_matched_exposure_attribution_is_separate",
                            "short_margin_uses_current_long_margin_proxy", "bar_gaps_not_filled_or_calendar_verified",
                            "native_indicator_and_execution_parity_missing", "no_demo_or_live_authority"]}


def comparison_summary(report: dict, currency: str) -> str:
    lines = ["COMPARISON COMPLETED — ABSTAIN / NOT QUALIFIED",
             "Five fixed candidates; two cost assumptions; separate development and holdout simulations.",
             "No automatic winner. A limited screen pass is NOT trading approval.",
             f"All P&L below is simulated {currency}; costs remain unverified.", ""]
    for case in report["cases"]:
        m = case["metrics"]
        lines.append(f"{case['candidate_id']} | {case['cost_scenario']} | {case['segment']}\n"
                     f"  Min-lot P&L {m['minimum_lot_net_pnl']:+.2f}; growth {m['growth_net_pnl']:+.2f}; "
                     f"{m['growth_trades']} trades; marked DD {m['growth_drawdown']:.2%}; "
                     f"vetoed setup signals {case['blocked_cognition_signals_before_tail']}.\n"
                     f"  Limited screen: {', '.join(case['screen']['reasons']) or 'passed; still unqualified'}")
    lines.extend(["", "Stress adds 10 broker points of total round-trip slippage to YOUR assumptions; not a fee estimate.",
                  "Matched-exposure attribution now freezes the original approved lots before applying the stress;",
                  "the residual separately reports the effect of re-sizing/rejections rather than calling it cost drag.",
                  "Trend alignment is an untrained entry veto, not proof of forecasting skill.",
                  "No-trade control earns zero before interest/opportunity cost. No orders sent."])
    return "\n".join(lines)
