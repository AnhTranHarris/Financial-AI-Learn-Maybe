"""Post-run arithmetic and recorded entry context, never a trading decision.

No model is fitted and no thresholds or exit rules are changed here. Loss labels
describe realized simulation arithmetic; they do not identify a causal market
regime or prove that an indicator should be removed.
"""
from collections import Counter
from datetime import datetime
import math
from typing import Mapping, Sequence

from .features import FeatureBar, FeatureVector
from .markets import InstrumentEconomics
from .research_eligibility import EntryPolicy, entry_eligibility
from .runtime import CompiledStrategy


DIAGNOSIS_PROTOCOL = "recorded-entry-exit-cash-attribution-v1"
TRADE_DETAILS_SEPARATOR = "\n\n=== TRADE DIAGNOSIS DETAILS ===\n"


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("diagnosis_requires_finite_numbers")
    return float(value)


def _reconcile(actual: float, expected: float) -> None:
    if not math.isclose(_number(actual), _number(expected), rel_tol=1e-12, abs_tol=1e-7):
        raise ValueError("diagnosis_cash_reconciliation_failed")


def _cash(gross_per_lot: float, costs: dict, volume: float) -> dict:
    volume = _number(volume)
    if volume < 0:
        raise ValueError("diagnosis_negative_volume")
    parts = {name: value * volume for name, value in costs.items()}
    gross = gross_per_lot * volume
    total = math.fsum(parts.values())
    net = gross - total
    for value in (gross, total, net):
        _number(value)
    return {"volume": volume, "gross_pnl": gross, **parts, "total_cost": total, "net_pnl": net}


def _totals(rows: list[dict], field: str) -> dict:
    cash = [r[field] for r in rows]
    names = ("gross_pnl", "spread_cost", "slippage_cost", "commission_cost", "total_cost", "net_pnl")
    return {**{name: math.fsum(r[name] for r in cash) for name in names},
            "trade_count": sum(r["volume"] > 0 for r in cash),
            "wins": sum(r["net_pnl"] > 0 for r in cash),
            "losses": sum(r["net_pnl"] < 0 for r in cash),
            "flat": sum(r["volume"] > 0 and r["net_pnl"] == 0 for r in cash)}


def diagnose_case(case: dict, *, strategy: CompiledStrategy, policy: EntryPolicy,
                  vectors: Mapping[datetime, FeatureVector], bars: Sequence[FeatureBar],
                  economics: InstrumentEconomics) -> dict:
    """Explain every potential trade, retaining rejected growth entries as zero cash.

The caller supplies the same completed-bar features used in the simulation.
Only the entry timestamp's vector is read. Exits are recorded simulator exits;
we do not infer intrabar excursions, alternative optimal exits, or native fills.
"""
    indices = {bar.at: i for i, bar in enumerate(bars)}
    if len(indices) != len(bars) or list(indices) != sorted(indices):
        raise ValueError("diagnosis_requires_unique_ordered_bars")
    rows = []
    config = case["config"]
    commission = config["commission_per_lot"]
    commission = economics.commission_per_lot if commission is None else _number(commission)
    slippage = _number(config["expected_slippage_price"])
    if min(commission, slippage) < 0:
        raise ValueError("diagnosis_negative_cost")
    start = datetime.fromisoformat(case["metrics"]["start"])
    end = datetime.fromisoformat(case["metrics"]["end"])
    cutoff = datetime.fromisoformat(case["metrics"]["entry_cutoff_exclusive"])
    for index, (trade, trace) in enumerate(zip(case["potential_trades"], case["growth_sizing"], strict=True)):
        entry, exit_at = trade["entry_at"], trade["exit_at"]
        if (trace["trade_id"] != f"growth-{index:06d}" or type(trace["approved"]) is not bool
                or not start <= entry < cutoff or not entry < exit_at < end
                or entry not in indices or exit_at not in indices or entry not in vectors):
            raise ValueError("diagnosis_trade_identity_or_boundary_mismatch")
        vector = vectors[entry]
        features = vector.feature_map()
        permission = entry_eligibility(vector, strategy.spec.direction, policy)
        if (vector.at != entry or not strategy.entry_matches(features) or not permission.allowed
                or trade["side"] != strategy.spec.direction
                or trade["strategy_hash"] != strategy.strategy_hash):
            raise ValueError("diagnosis_entry_context_mismatch")
        entry_price, exit_price = _number(trade["entry_price"]), _number(trade["exit_price"])
        _reconcile(entry_price, bars[indices[entry]].market_price_at_availability)
        if min(entry_price, exit_price) <= 0:
            raise ValueError("diagnosis_nonpositive_price")
        spread = _number(trace["spread_price_used"])
        bar = bars[indices[entry]]
        expected_spread = config["spread_price"]
        if bar.decision_spread_proxy_points is not None and economics.point_size > 0:
            expected_spread = max(expected_spread, bar.decision_spread_proxy_points * economics.point_size)
        _reconcile(spread, expected_spread)
        if spread < 0:
            raise ValueError("diagnosis_negative_cost")
        direction = 1 if trade["side"] == "long" else -1
        gross_per_lot = direction * (exit_price - entry_price) / economics.tick_size * economics.tick_value
        costs = {"spread_cost": spread / economics.tick_size * economics.tick_value,
                 "slippage_cost": slippage / economics.tick_size * economics.tick_value,
                 "commission_cost": commission}
        volume = trace["sizing"]["approved_volume"] if trace["approved"] else 0.0
        if trace["approved"] and _number(volume) <= 0:
            raise ValueError("diagnosis_approved_volume_missing")
        growth = _cash(gross_per_lot, costs, volume)
        if trace["approved"]:
            _reconcile(growth["net_pnl"], trace["expected_net_pnl"])
        label = ("growth_entry_rejected" if not trace["approved"] else
                 "price_loss_before_modeled_costs" if growth["gross_pnl"] < 0 else
                 "modeled_costs_turn_nonnegative_gross_into_loss" if growth["net_pnl"] < 0 else
                 "positive_net_after_modeled_costs" if growth["net_pnl"] > 0 else "flat_after_modeled_costs")
        groups = [{"mode": group.mode.value, "passed": group.evaluate(features),
                   "clauses": [{"feature": c.feature, "op": c.op.value, "threshold": c.value,
                                "observed": features.get(c.feature), "passed": c.evaluate(features)}
                               for c in group.clauses]} for group in strategy.spec.entry_groups]
        rows.append({"trade_id": trace["trade_id"], "entry_at": entry, "exit_at": exit_at,
                     "side": trade["side"], "entry_price": entry_price, "exit_price": exit_price,
                     "initial_stop": trade["stop_price"], "target": trade["target_price"],
                     "exit_reason": trade["exit_reason"],
                     "observed_hold_steps": indices[exit_at] - indices[entry],
                     "elapsed_minutes": (exit_at - entry).total_seconds() / 60,
                     "entry_context": {"available_at": entry, "source_open_at": bar.source_open_at,
                                       "features": {k: features.get(k) for k in
                                                    ("close", "rsi", "return_1", "atr", "sma_20", "ema_20")},
                                       "rule_groups": groups, "entry_policy": policy.value,
                                       "entry_policy_reason": permission.reason},
                     "spread_basis": trace["spread_basis"], "cost_per_lot": costs,
                     "minimum_lot": _cash(gross_per_lot, costs, economics.volume_min),
                     "growth": growth, "growth_approved": trace["approved"],
                     "growth_rejection_reasons": list(trace["reasons"]), "outcome_label": label})
    totals = {key: _totals(rows, key) for key in ("minimum_lot", "growth")}
    for key in totals:
        backtest = case[f"{key}_backtest"]
        _reconcile(totals[key]["net_pnl"], backtest["net_pnl"])
        _reconcile(backtest["ending_balance"] - backtest["starting_equity"], totals[key]["net_pnl"])
        metric_prefix = "minimum_lot" if key == "minimum_lot" else "growth"
        _reconcile(totals[key]["net_pnl"], case["metrics"][f"{metric_prefix}_net_pnl"])
        if totals[key]["trade_count"] != backtest["trade_count"]:
            raise ValueError("diagnosis_trade_count_mismatch")
    return {"protocol": DIAGNOSIS_PROTOCOL, "source_case_fingerprint": case["case_fingerprint"],
            "rows": rows, "totals": totals,
            "growth_exit_counts": dict(sorted(Counter(r["exit_reason"] for r in rows if r["growth_approved"]).items())),
            "growth_outcomes": dict(sorted(Counter(r["outcome_label"] for r in rows).items())),
            "growth_rejections": sum(not r["growth_approved"] for r in rows),
            "causal_explanation_claimed": False, "promotion_eligible": False}


def attribute_cost_pair(baseline: dict, stressed: dict) -> dict:
    """Arithmetic overlay at baseline volumes, not another admissible backtest.

If potential trade identities change, abstain from matched attribution. When
approvals change, the residual includes selection as well as sizing, explicitly.
"""
    if (baseline["candidate_id"] != stressed["candidate_id"] or baseline["segment"] != stressed["segment"]
            or baseline["cost_scenario"] != "configured" or stressed["cost_scenario"] != "stress-plus-10-points"):
        raise ValueError("diagnosis_cost_pair_identity_mismatch")
    result = {"candidate_id": baseline["candidate_id"], "segment": baseline["segment"],
              "baseline_case_fingerprint": baseline["case_fingerprint"],
              "stressed_case_fingerprint": stressed["case_fingerprint"],
              "promotion_eligible": False, "risk_feasibility_retested": False}
    if baseline["potential_trades"] != stressed["potential_trades"]:
        return {**result, "status": "UNAVAILABLE_TRADE_PATH_CHANGED"}
    before, after = baseline["diagnosis"]["rows"], stressed["diagnosis"]["rows"]
    overlays = []
    for b, s in zip(before, after, strict=True):
        extra = math.fsum(s["cost_per_lot"].values()) - math.fsum(b["cost_per_lot"].values())
        direct = -extra * b["growth"]["volume"]
        fixed = b["growth"]["net_pnl"] + direct
        overlays.append({"trade_id": b["trade_id"], "entry_at": b["entry_at"],
                         "baseline_volume": b["growth"]["volume"], "stressed_volume": s["growth"]["volume"],
                         "direct_cost_effect": direct, "fixed_volume_stressed_net_pnl": fixed,
                         "sizing_and_selection_effect": s["growth"]["net_pnl"] - fixed})
    base_net = baseline["diagnosis"]["totals"]["growth"]["net_pnl"]
    stress_net = stressed["diagnosis"]["totals"]["growth"]["net_pnl"]
    direct = math.fsum(r["direct_cost_effect"] for r in overlays)
    residual = math.fsum(r["sizing_and_selection_effect"] for r in overlays)
    _reconcile(stress_net - base_net, direct + residual)
    return {**result, "status": "MATCHED_PATH_ARITHMETIC_ONLY", "rows": overlays,
            "baseline_net_pnl": base_net, "stressed_resized_net_pnl": stress_net,
            "direct_cost_effect": direct, "fixed_volume_stressed_net_pnl": base_net + direct,
            "sizing_and_selection_effect": residual,
            "volume_changed_trades": sum(r["baseline_volume"] != r["stressed_volume"] for r in overlays)}


def diagnosis_summary(report: dict, currency: str, *, details: bool = False) -> str:
    lines = ["POST-RUN DIAGNOSIS — simulated cash, not causal proof or trading approval.",
             f"Amounts are {currency}. Broker costs remain unverified; swaps/fees are incomplete.",
             "Entry values were available at entry; outcomes were learned only afterward.",
             "No thresholds, risk rules, or strategies were changed. Reused history is not new validation."]
    for case in report["cases"]:
        diagnosis = case["diagnosis"]
        t = diagnosis["totals"]["growth"]
        lines.extend(["", f"{case['candidate_id']} | {case['cost_scenario']} | {case['segment']}",
                      f"Growth: price {t['gross_pnl']:+.2f} - costs {t['total_cost']:.2f} = net {t['net_pnl']:+.2f}; "
                      f"{t['wins']} wins / {t['losses']} losses / {t['flat']} flat; rejected {diagnosis['growth_rejections']}.",
                      f"Recorded growth exits: {diagnosis['growth_exit_counts'] or 'none'}."])
        if details:
            for row in diagnosis["rows"]:
                g, m = row["growth"], row["minimum_lot"]
                lines.extend([f"  {row['trade_id']} {row['side']} | {row['entry_at']} -> {row['exit_at']}",
                              f"    Entry {row['entry_price']:g}; exit {row['exit_price']:g}; initial stop {row['initial_stop']:g}; target {row['target']}.",
                              f"    Exit: {row['exit_reason']}; {row['observed_hold_steps']} observed steps / {row['elapsed_minutes']:g} elapsed minutes.",
                              f"    Entry features: {row['entry_context']['features']}",
                              f"    Entry policy: {row['entry_context']['entry_policy_reason']}; rules: {row['entry_context']['rule_groups']}",
                              f"    Min lot {m['volume']:g}: net {m['net_pnl']:+.2f}. Growth {g['volume']:g}: price {g['gross_pnl']:+.2f}, "
                              f"spread {g['spread_cost']:.2f}, slippage {g['slippage_cost']:.2f}, commission {g['commission_cost']:.2f}, net {g['net_pnl']:+.2f}.",
                              f"    Outcome: {row['outcome_label']}; rejection reasons: {row['growth_rejection_reasons']}."])
    if not details:
        lines.extend(["", "COST ATTRIBUTION — same trade path, baseline volumes held fixed.",
                      "Arithmetic only: baseline sizes are NOT reapproved under stressed risk/margin."])
        for pair in report["cost_attribution"]:
            lines.append(f"{pair['candidate_id']} | {pair['segment']}: " + (
                f"baseline {pair['baseline_net_pnl']:+.2f}; stressed fixed-size {pair['fixed_volume_stressed_net_pnl']:+.2f}; "
                f"stressed re-sized {pair['stressed_resized_net_pnl']:+.2f}; direct cost effect {pair['direct_cost_effect']:+.2f}; "
                f"sizing/selection effect {pair['sizing_and_selection_effect']:+.2f}."
                if pair["status"] == "MATCHED_PATH_ARITHMETIC_ONLY" else "unavailable: trade path changed."))
    return "\n".join(lines)
