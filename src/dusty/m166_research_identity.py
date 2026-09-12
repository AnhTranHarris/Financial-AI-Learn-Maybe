from __future__ import annotations

"""Deterministic identities for provisional/production M166 research inputs.

The model is deliberately authority-free.  It freezes the executable strategy,
parameter surface and a concrete read-only MT5 bar dataset so later qualification
can reuse unchanged work by fingerprint instead of rerunning milestones blindly.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import math
from typing import Iterable

from .mt5worker import MT5Bar
from .strategy_ir import StrategySpecV2


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def parameter_payload(spec: StrategySpecV2) -> dict[str, object]:
    """Versioned parameter/tuning projection of one frozen StrategySpecV2.

    This is intentionally distinct from ``strategy_hash``: the strategy hash is
    the executable semantic identity, while this projection is the M168 center
    point from which a bounded parameter neighborhood can later be generated.
    """

    groups: list[dict[str, object]] = []
    for group in spec.entry_groups:
        clauses = sorted(
            (
                {"feature": clause.feature, "op": clause.op.value, "value": clause.value}
                for clause in group.clauses
            ),
            key=lambda row: (str(row["feature"]), str(row["op"]), repr(row["value"])),
        )
        groups.append({"mode": group.mode.value, "clauses": clauses})
    groups.sort(key=_canonical)
    return {
        "protocol": "dusty-m166-parameter-set-v1",
        "entry_groups": groups,
        "exit_plan": {
            "stop_rule": spec.exit_plan.stop_rule,
            "target_rule": spec.exit_plan.target_rule,
            "trailing_rule": spec.exit_plan.trailing_rule,
            "breakeven_rule": spec.exit_plan.breakeven_rule,
            "max_hold_steps": spec.exit_plan.max_hold_steps,
        },
        "decision_timeframe_minutes": spec.decision_timeframe_minutes,
        "intended_horizon_minutes": spec.intended_horizon_minutes,
        "session_filters": sorted(spec.session_filters),
        "event_exclusion_minutes": spec.event_exclusion_minutes,
        "cooldown_steps": spec.cooldown_steps,
        "scale_in_limit": spec.scale_in_limit,
        "scale_out_fractions": list(spec.scale_out_fractions),
        "cost_bps": spec.cost_bps,
        "execution_sensitivity": spec.execution_sensitivity.value,
    }


def parameter_fingerprint(spec: StrategySpecV2) -> str:
    return _digest(parameter_payload(spec))


def canonical_bar_payload(bar: MT5Bar) -> dict[str, object]:
    if bar.at.tzinfo is None or bar.at.utcoffset() is None:
        raise ValueError("M166 dataset bars require timezone-aware timestamps")
    prices = (bar.open, bar.high, bar.low, bar.close)
    if any(not math.isfinite(value) or value <= 0 for value in prices):
        raise ValueError("M166 dataset prices must be finite and positive")
    if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
        raise ValueError("M166 dataset OHLC geometry is invalid")
    if bar.tick_volume < 0 or bar.real_volume < 0 or bar.spread < 0:
        raise ValueError("M166 dataset volume/spread cannot be negative")
    return {
        "time": bar.at.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "tick_volume": bar.tick_volume,
        "spread": bar.spread,
        "real_volume": bar.real_volume,
    }


def dataset_payload(*, symbol: str, timeframe: str, bars: Iterable[MT5Bar]) -> dict[str, object]:
    rows = tuple(bars)
    symbol_norm = str(symbol).strip().upper()
    timeframe_norm = str(timeframe).strip().upper()
    if not symbol_norm or not timeframe_norm:
        raise ValueError("M166 dataset requires symbol/timeframe")
    if not rows:
        raise ValueError("M166 dataset requires bars")
    times = tuple(row.at for row in rows)
    if any(current <= previous for previous, current in zip(times, times[1:])):
        raise ValueError("M166 dataset bars must be strictly chronological and unique")
    canonical_rows = [canonical_bar_payload(row) for row in rows]
    return {
        "protocol": "dusty-m166-mt5-bar-dataset-v1",
        "source": "mt5_copy_rates_range_read_only",
        "symbol": symbol_norm,
        "timeframe": timeframe_norm,
        "bar_count": len(canonical_rows),
        "first_bar_utc": rows[0].at.isoformat(),
        "last_bar_utc": rows[-1].at.isoformat(),
        "bars_sha256": _digest(canonical_rows),
    }


def dataset_fingerprint(*, symbol: str, timeframe: str, bars: Iterable[MT5Bar]) -> str:
    return _digest(dataset_payload(symbol=symbol, timeframe=timeframe, bars=bars))


@dataclass(frozen=True, slots=True)
class M166ResearchIdentity:
    lane_id: str
    strategy_fingerprint: str
    dataset_fingerprint: str
    parameter_fingerprint: str
    dataset_metadata: dict[str, object]

    broker_write_authority = False
    live_write_authority = False
    custody_write_authority = False
    research_execution_authority = False
    promotion_authority = False
    retry_authority = False
    risk_override_authority = False

    def __post_init__(self) -> None:
        lane = str(self.lane_id).strip().lower()
        if not lane:
            raise ValueError("M166 research identity requires lane_id")
        object.__setattr__(self, "lane_id", lane)
        for name in ("strategy_fingerprint", "dataset_fingerprint", "parameter_fingerprint"):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        if self.dataset_metadata.get("protocol") != "dusty-m166-mt5-bar-dataset-v1":
            raise ValueError("M166 research identity requires certified dataset metadata")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m166-research-identity-v1",
            "lane_id": self.lane_id,
            "strategy_fingerprint": self.strategy_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "dataset_metadata": self.dataset_metadata,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "research_execution": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)
