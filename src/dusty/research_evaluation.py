"""Chronological, fixed-window research. Never an assertion that data was unseen."""
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Sequence

from .features import FeatureBar
from .investment_lab import LaboratoryConfig, LaboratoryRun, run_laboratory_from_bars
from .markets import InstrumentEconomics
from .runtime import CompiledStrategy
from .research_eligibility import EntryPolicy


EVALUATION_PROTOCOL = "fixed-history-flat-reset-max-hold-tail-v1"


def require_m15_utc(value: datetime) -> None:
    if (not isinstance(value, datetime) or value.utcoffset() != timedelta(0)
            or value.minute % 15 or value.second or value.microsecond):
        raise ValueError("fixed_dates_require_UTC_and_15_minute_alignment")


def parse_fixed_end(text: str) -> datetime | None:
    if not text.strip():
        return None
    try:
        value = datetime.strptime(text.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError("fixed_end_format_is_YYYY-MM-DD_HH:MM_UTC") from exc
    require_m15_utc(value)
    return value


@dataclass(frozen=True, slots=True)
class FixedEvaluationPlan:
    start: datetime
    holdout_start: datetime
    end: datetime

    def __post_init__(self) -> None:
        for value in (self.start, self.holdout_start, self.end):
            require_m15_utc(value)
        if not self.start < self.holdout_start < self.end or self.end - self.start > timedelta(days=30):
            raise ValueError("fixed_evaluation_requires_ordered_boundaries_within_30_days")

    def payload(self) -> dict[str, object]:
        return {
            "protocol": EVALUATION_PROTOCOL, "start": self.start.isoformat(),
            "holdout_start": self.holdout_start.isoformat(), "end": self.end.isoformat(),
            "interval_convention": "availability_time_start_inclusive_end_exclusive",
            "minimum_bars_per_segment": 64, "capital_reset_between_segments": True,
            "prior_exposure": "UNKNOWN", "unseen_data_verified": False,
            "automatic_optimization": False, "promotion_eligible": False,
        }

    @property
    def fingerprint(self) -> str:
        return sha256(json.dumps(self.payload(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_fixed_evaluation(
    strategy: CompiledStrategy, bars: Sequence[FeatureBar], *, symbol: str,
    economics: InstrumentEconomics, config: LaboratoryConfig, plan: FixedEvaluationPlan,
    entry_policy: EntryPolicy = EntryPolicy.SEED,
) -> tuple[LaboratoryRun, dict[str, object]]:
    """Each segment starts flat at the same equity, with only past indicator warm-up.

    No entry is allowed in the last max_hold_steps observations of either segment.
    That prespecified tail guard prevents a trade/label from crossing the boundary
    and prevents dropping unresolved end-of-sample positions. It uses observations,
    not wall-clock hours, so a closed-market gap is never filled or shortened.
    """
    horizon = strategy.spec.exit_plan.max_hold_steps
    if type(horizon) is not int or horizon < 1:
        raise ValueError("fixed_evaluation_requires_a_finite_max_hold")
    rows = tuple(bars)
    if (tuple(sorted(rows, key=lambda b: b.at)) != rows
            or len({b.at for b in rows}) != len(rows)):
        raise ValueError("fixed_evaluation_requires_unique_chronological_bars")
    segments = {}
    runs = {}
    for name, start, end in (("development", plan.start, plan.holdout_start),
                             ("holdout", plan.holdout_start, plan.end)):
        selected = tuple(bar for bar in rows if start <= bar.at < end)
        if len(selected) < max(64, horizon + 2):
            raise ValueError(f"{name}_has_insufficient_observed_bars_no_window_expansion")
        warmup = tuple(bar for bar in rows if bar.at < start)
        cutoff = selected[-horizon].at
        run = run_laboratory_from_bars(
            strategy, selected, symbol=symbol, economics=economics, config=config,
            feature_warmup_bars=warmup, entry_cutoff=cutoff,
            entry_policy=entry_policy,
        )
        if any(not start <= trade.entry_at < cutoff or not trade.entry_at < trade.exit_at < end
               for trade in run.potential_trades):
            raise ValueError("evaluation_trade_crossed_declared_boundary")
        runs[name] = run
        segments[name] = {
            "start": start.isoformat(), "end": end.isoformat(), "observed_bars": len(selected),
            "first_available_at": selected[0].at.isoformat(), "last_available_at": selected[-1].at.isoformat(),
            "past_warmup_bars": len(warmup), "entry_cutoff_exclusive": cutoff.isoformat(),
            "tail_entry_guard_bars": horizon,
            "minimum_lot_trades": run.minimum_lot_backtest.trade_count,
            "minimum_lot_net_pnl": run.minimum_lot_backtest.net_pnl,
            "growth_trades": run.growth_backtest.trade_count,
            "growth_net_pnl": run.growth_backtest.net_pnl,
            "growth_drawdown": run.growth_backtest.max_drawdown_fraction,
        }
    return runs["holdout"], {
        "plan": plan.payload(), "plan_fingerprint": plan.fingerprint, "segments": segments,
        "development_laboratory": asdict(runs["development"]),
        "primary_laboratory": "holdout", "statistical_confidence_claimed": False,
        "verdict": "RESEARCH_ONLY_NOT_QUALIFIED",
        "limitations": ["historical_holdout_prior_exposure_unknown", "repeated_trials_not_independent",
                        "bar_coverage_not_verified_against_broker_calendar",
                        "broker_costs_not_verified", "native_parity_missing", "no_live_or_demo_authority"],
    }
