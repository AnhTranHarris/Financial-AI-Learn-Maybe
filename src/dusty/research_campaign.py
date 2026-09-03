"""A fixed 30-case queue over one read-only acquisition; never an optimizer.

Three chronological test folds, expanding past-only training, both fixed seeds
with/without forecast conflict veto, a no-trade control, and two cost assumptions.
Every case starts flat. Later training can include earlier test observations;
these are dependent historical experiments, not three untouched holdouts.
"""
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from typing import Callable, Sequence

from .connected_forecast import HORIZON, forecast_contract, forecast_fold, ridge_forecast_map
from .features import FeatureBar, compute_standard_features
from .investment_lab import LaboratoryConfig, run_laboratory_from_bars
from .markets import InstrumentEconomics
from .research_comparison import _fingerprint, _screen
from .research_diagnosis import diagnose_case, attribute_cost_pair
from .research_eligibility import EntryPolicy
from .research_evaluation import require_m15_utc
from .reviewed_strategies import reviewed_research_packages


CAMPAIGN_PROTOCOL = "three-fold-fitted-forecast-campaign-v1"
FOLDS = 3


def campaign_contract(start: datetime, end: datetime, test_days: int) -> dict:
    require_m15_utc(start)
    require_m15_utc(end)
    if (type(test_days) is not int or test_days < 1 or end-start > timedelta(days=30)
            or end-start < timedelta(days=FOLDS*test_days+2)):
        raise ValueError("campaign_requires_history_at_least_3_times_holdout_plus_2_days_max_30")
    packages = reviewed_research_packages()
    return {"protocol": CAMPAIGN_PROTOCOL, "forecast": forecast_contract(),
            "start": start, "end": end,
            "folds": [{"id": f"fold-{i+1}", "start": end-timedelta(days=(FOLDS-i)*test_days),
                       "end": end-timedelta(days=(FOLDS-i-1)*test_days)} for i in range(FOLDS)],
            "candidates": [{"id": f"{p.spec.direction.value}:{mode}", "forecast_required": mode == "ridge-veto",
                            "package_fingerprint": p.fingerprint} for p in packages for mode in ("seed", "ridge-veto")]
                          + [{"id": "no-trade", "forecast_required": False,
                              "package_fingerprint": packages[0].fingerprint}],
            "cost_scenarios": [{"id": "configured", "extra_slippage_points": 0.0},
                               {"id": "stress-plus-10-points", "extra_slippage_points": 10.0}],
            "screen": {"minimum_closed_trades": 20, "net_pnl_strictly_above": 0.0,
                       "maximum_marked_drawdown": 0.02},
            "expected_cases": 30, "capital_reset_every_case": True,
            "training": "expanding_past_only_frozen_within_fold", "prior_exposure": "UNKNOWN",
            "automatic_selection": False, "promotion_eligible": False}


def run_forecast_campaign(bars: Sequence[FeatureBar], *, symbol: str, economics: InstrumentEconomics,
                          config: LaboratoryConfig, contract: dict,
                          checkpoint: Callable[[list[dict], dict | None], None] | None = None) -> dict:
    expected = campaign_contract(contract["start"], contract["end"],
                                 (contract["folds"][0]["end"]-contract["folds"][0]["start"]).days)
    if contract != expected:
        raise ValueError("campaign_contract_modified")
    rows = tuple(bars)
    if (not rows or len(rows) > 3000 or any(a.at >= b.at for a, b in zip(rows, rows[1:]))
            or any(not contract["start"] <= b.at <= contract["end"] for b in rows)):
        raise ValueError("campaign_bars_outside_frozen_window_or_unordered")
    rows = tuple(b for b in rows if b.at < contract["end"])
    packages = reviewed_research_packages()
    queue = [{"id": f"case-{i:03d}", "fold": f["id"], "candidate_id": c["id"],
              "cost_scenario": cost["id"], "state": "PENDING"}
             for i, (f, c, cost) in enumerate((f, c, cost) for f in contract["folds"]
                 for c in contract["candidates"] for cost in contract["cost_scenarios"])]
    notify = checkpoint or (lambda queue, case: None)
    notify(queue, None)
    data_hash, contract_hash = _fingerprint([asdict(b) for b in rows]), _fingerprint(contract)
    cases, forecasts = [], []
    models = {}
    active = None
    try:
        for item in queue:
            active = item
            item["state"] = "RUNNING"
            notify(queue, None)
            fold = next(f for f in contract["folds"] if f["id"] == item["fold"])
            selected = tuple(b for b in rows if fold["start"] <= b.at < fold["end"])
            if len(selected) < 64:
                raise ValueError("campaign_fold_insufficient_bars_no_window_expansion")
            if fold["id"] not in models:
                evidence = forecast_fold(rows, start=fold["start"], end=fold["end"])
                forecasts.append({"fold": fold, **evidence})
                models[fold["id"]] = ridge_forecast_map(evidence)
            candidate = next(c for c in contract["candidates"] if c["id"] == item["candidate_id"])
            package = next(p for p in packages if p.fingerprint == candidate["package_fingerprint"])
            cost = next(c for c in contract["cost_scenarios"] if c["id"] == item["cost_scenario"])
            scenario = replace(config, feature_config=package.features, cognition_policy=package.cognition,
                               expected_slippage_price=config.expected_slippage_price
                               + cost["extra_slippage_points"]*economics.point_size)
            policy = EntryPolicy.NO_TRADE if candidate["id"] == "no-trade" else EntryPolicy.SEED
            forecast_map = models[fold["id"]] if candidate["forecast_required"] else None
            warmup = tuple(b for b in rows if b.at < fold["start"])
            cutoff = selected[-HORIZON].at
            run = run_laboratory_from_bars(package.compiled, selected, symbol=symbol, economics=economics,
                config=scenario, feature_warmup_bars=warmup, entry_cutoff=cutoff, entry_policy=policy,
                forecasts_by_time=forecast_map, require_forecasts=candidate["forecast_required"])
            if any(not fold["start"] <= t.entry_at < cutoff or not t.entry_at < t.exit_at < fold["end"]
                   for t in run.potential_trades):
                raise ValueError("campaign_trade_crossed_fold_boundary")
            metrics = {"start": fold["start"].isoformat(), "end": fold["end"].isoformat(),
                       "observed_bars": len(selected), "entry_cutoff_exclusive": cutoff.isoformat(),
                       "minimum_lot_net_pnl": run.minimum_lot_backtest.net_pnl,
                       "minimum_lot_trades": run.minimum_lot_backtest.trade_count,
                       "growth_net_pnl": run.growth_backtest.net_pnl,
                       "growth_trades": run.growth_backtest.trade_count,
                       "growth_drawdown": run.growth_backtest.max_drawdown_fraction}
            identity = {"contract": contract_hash, "data": data_hash, "symbol": symbol,
                        "economics": asdict(economics), "config": asdict(scenario), "queue_id": item["id"]}
            case = {"id": item["id"], "case_fingerprint": _fingerprint(identity),
                    "candidate_id": candidate["id"], "cost_scenario": cost["id"], "segment": fold["id"],
                    "metrics": metrics, "config": asdict(scenario),
                    "screen": _screen(metrics, contract["screen"], control=policy is EntryPolicy.NO_TRADE),
                    "forecast_required": candidate["forecast_required"], "promotion_eligible": False}
            case["potential_trades"] = [asdict(t) for t in run.potential_trades]
            case["growth_sizing"] = [asdict(t) for t in run.growth_sizing]
            for key in ("minimum_lot_backtest", "growth_backtest"):
                case[key] = asdict(getattr(run, key))
            # Compact decision witnesses avoid duplicating full feature/evidence objects
            # thirty times. Raw bars, model evidence and config reproduce those objects.
            case["entry_decisions"] = [{"at": t.at, "decision": t.decision,
                "cognition_fingerprint": t.assessment.fingerprint,
                "analyst_reasons": t.assessment.reasons_for("analyst")} for t in run.cognition]
            vectors = {v.at: v for v in compute_standard_features(warmup+selected, package.features)}
            case["diagnosis"] = diagnose_case(case, strategy=package.compiled, policy=policy,
                                               vectors=vectors, bars=rows, economics=economics)
            if forecast_map is not None:
                for trade in case["diagnosis"]["rows"]:
                    fs = forecast_map[trade["entry_at"]]
                    trade["entry_context"]["forecast"] = [asdict(f) for f in fs]
            cases.append(case)
            item["state"] = "COMPLETED"
            notify(queue, case)
    except Exception as exc:
        if active is not None:
            active["state"] = "FAILED"
            active["error"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        for item in queue:
            if item["state"] == "PENDING":
                item["state"] = "NOT_RUN"
        notify(queue, None)
        raise
    indexed = {(c["candidate_id"], c["segment"], c["cost_scenario"]): c for c in cases}
    attribution = [attribute_cost_pair(indexed[c["id"], f["id"], "configured"],
                                       indexed[c["id"], f["id"], "stress-plus-10-points"])
                   for f in contract["folds"] for c in contract["candidates"]]
    return {"contract": contract, "contract_fingerprint": contract_hash, "data_fingerprint": data_hash,
            "queue": queue, "cases": cases, "forecast_evaluation": forecasts, "cost_attribution": attribution,
            "selected_winner": None, "deployment_decision": "ABSTAIN_UNQUALIFIED", "promotion_eligible": False,
            "limitations": ["historical_prior_exposure_unknown", "later_training_includes_earlier_test_prices",
                            "overlapping_forecast_targets_not_independent", "no_model_or_strategy_selection",
                            "unverified_costs_current_economics_and_margin_proxies", "bar_gaps_not_filled",
                            "forecast_is_close_target_not_trade_exit", "native_parity_missing"]}


def campaign_summary(report: dict, currency: str) -> str:
    lines = ["FORECAST CAMPAIGN COMPLETED — RESEARCH ONLY / NO WINNER",
             "30 queued cases; three historical test folds; all outcomes retained.",
             "Fitted ridge forecast is an entry-conflict veto, not a profit prediction.",
             "Training expands using earlier observations; test folds are not independent or proven unseen.",
             "Forecast MAE is in symbol price units; lower is better. Skill > 0 beats no-change on this sample.",
             "Targets overlap. No-change predicts flat (direction hits only flat outcomes), not a 50%-chance classifier.",
             "Native trading, verified fees and forecasting reliability are NOT established.", ""]
    for fold in report["forecast_evaluation"]:
        lines.append(f"{fold['fold']['id']}: {fold['model']['pairs']} earlier training pairs; "
                     f"{fold['scores']['ridge']['count']} scored forecasts.")
        for name, score in fold["scores"].items():
            skill = score["mae_skill_vs_no_change"]
            lines.append(f"  {name}: MAE {score['mae']:.4f}; direction {score['directional_accuracy']:.1%}; "
                         + (f"skill {skill:+.1%}" if skill is not None else "skill undefined (zero baseline error)"))
    lines.append(f"\nSimulated growth P&L ({currency}); inspect Cases for costs, exits and entry evidence:")
    for c in report["cases"]:
        m = c["metrics"]
        lines.append(f"{c['segment']} | {c['candidate_id']} | {c['cost_scenario']}: "
                     f"{m['growth_net_pnl']:+,.2f}; {m['growth_trades']} trades; DD {m['growth_drawdown']:.2%}")
    return "\n".join(lines)
