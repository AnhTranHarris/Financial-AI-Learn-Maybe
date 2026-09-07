from __future__ import annotations

"""M196.13 Strategy Estate -> executable A1 research bridge.

This module binds one immutable Strategy Estate reconstruction to deterministic
research execution. It never invents missing multi-timeframe provenance: a
single-timeframe profile may be derived from the persisted primary timeframe,
while multi-timeframe context must be supplied explicitly and must bind the
same primary timeframe. The bridge carries no broker, live, promotion, risk,
or Guardian authority.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Iterable

from .analysis_runtime import AnalysisReplay, AnalysisReplayTrade
from .multitimeframe_research_frame import AnalysisFrame
from .runtime import RuntimeBar, RuntimeTrade, compile_strategy, generate_runtime_trades
from .strategy_reconstruction_campaign import AssignmentBasis, TimeframeMode, TimeframeProfile
from .trading_skills import StrategyReconstruction


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _timeframe_from_minutes(minutes: int) -> str:
    if minutes % 60 == 0:
        hours = minutes // 60
        if hours in {1, 4}:
            return f"H{hours}"
    if minutes in {5, 15, 30}:
        return f"M{minutes}"
    raise ValueError("Estate primary timeframe is outside M196 executable ladder")


@dataclass(frozen=True, slots=True)
class EstateA1Binding:
    reconstruction_fingerprint: str
    strategy_hash: str
    strategy_id: str
    symbol: str
    profile: TimeframeProfile

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m19613-estate-a1-binding-v1",
                "reconstruction": self.reconstruction_fingerprint,
                "strategy_hash": self.strategy_hash,
                "strategy_id": self.strategy_id,
                "symbol": self.symbol,
                "profile": self.profile.fingerprint,
            }
        )

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False


def bind_estate_candidate(
    reconstruction: StrategyReconstruction,
    *,
    symbol: str,
    profile: TimeframeProfile | None = None,
) -> EstateA1Binding:
    symbol = symbol.strip().upper()
    if symbol not in reconstruction.symbols:
        raise ValueError("requested symbol is not allowed by Strategy Estate reconstruction")
    primary = _timeframe_from_minutes(reconstruction.candidate_spec.decision_timeframe_minutes)
    if primary != reconstruction.timeframe:
        raise ValueError("Strategy Estate primary timeframe identity drift")
    if profile is None:
        profile = TimeframeProfile(primary, (), TimeframeMode.SINGLE, AssignmentBasis.SOURCE_DECLARED)
    elif profile.primary != primary:
        raise ValueError("explicit multi-timeframe profile does not bind Estate primary timeframe")
    return EstateA1Binding(
        reconstruction.fingerprint,
        reconstruction.candidate_spec.strategy_hash,
        reconstruction.candidate_spec.strategy_id,
        symbol,
        profile,
    )


def runtime_bars_from_research_frames(
    binding: EstateA1Binding,
    frames: Iterable[AnalysisFrame],
) -> tuple[RuntimeBar, ...]:
    rows = tuple(frames)
    if not rows:
        return ()
    ordered = tuple(row.snapshot.at for row in rows)
    if tuple(sorted(ordered)) != ordered or len(set(ordered)) != len(ordered):
        raise ValueError("Estate A1 research frames must be strictly chronological")
    result: list[RuntimeBar] = []
    for frame in rows:
        features = dict(frame.snapshot.values)
        required: dict[str, float] = {}
        for key in ("open", "high", "low", "close"):
            value = features.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"Estate A1 primary feature {key!r} is required")
            required[key] = float(value)
        result.append(
            RuntimeBar.of(
                frame.snapshot.at,
                open=required["open"],
                high=required["high"],
                low=required["low"],
                close=required["close"],
                features=features,
                event_blocked=frame.event_blocked,
                execution_price=frame.execution_price,
            )
        )
    return tuple(result)


def _compiled_for_binding(reconstruction: StrategyReconstruction, binding: EstateA1Binding):
    if reconstruction.fingerprint != binding.reconstruction_fingerprint:
        raise ValueError("Estate A1 reconstruction identity drift")
    if reconstruction.candidate_spec.strategy_hash != binding.strategy_hash:
        raise ValueError("Estate A1 strategy identity drift")
    return compile_strategy(reconstruction.candidate_spec)


def execute_estate_candidate(
    reconstruction: StrategyReconstruction,
    binding: EstateA1Binding,
    frames: Iterable[AnalysisFrame],
) -> tuple[RuntimeTrade, ...]:
    """Low-level deterministic execution over exactly the supplied observations."""
    compiled = _compiled_for_binding(reconstruction, binding)
    return generate_runtime_trades(compiled, runtime_bars_from_research_frames(binding, frames))


def execute_estate_candidate_closed_window(
    reconstruction: StrategyReconstruction,
    binding: EstateA1Binding,
    frames: Iterable[AnalysisFrame],
) -> tuple[RuntimeTrade, ...]:
    """Execute an A1 window while guaranteeing no position can remain unresolved.

    New entries are vetoed for the final ``max_hold_steps`` real observations.
    Because the current V2 runtime guarantees a max-hold exit, any position
    entered before that tail must be closed by the final supplied observation.
    No synthetic prices or post-window observations are introduced.
    """
    compiled = _compiled_for_binding(reconstruction, binding)
    bars = runtime_bars_from_research_frames(binding, frames)
    tail = compiled.spec.exit_plan.max_hold_steps
    if tail < 1 or len(bars) <= tail:
        raise ValueError("Estate A1 closed window requires observations beyond max_hold_steps")
    entry_cutoff = len(bars) - tail
    allowed_entry_times = frozenset(row.at for row in bars[:entry_cutoff])

    def authorize(bar: RuntimeBar, _compiled) -> bool:
        return bar.at in allowed_entry_times

    trades = generate_runtime_trades(compiled, bars, entry_authorizer=authorize)
    if any(row.exit_at > bars[-1].at for row in trades):
        raise AssertionError("Estate A1 runtime trade escaped closed observation window")
    return trades


def analysis_replay_from_runtime_trades(
    binding: EstateA1Binding,
    trades: Iterable[RuntimeTrade],
) -> AnalysisReplay:
    rows = tuple(trades)
    for row in rows:
        if row.strategy_hash != binding.strategy_hash:
            raise ValueError("runtime trade does not bind Estate strategy hash")
    replay_trades = tuple(
        AnalysisReplayTrade(
            row.side,
            row.entry_at,
            row.exit_at,
            row.entry_price,
            row.exit_price,
            1.0,
            row.exit_reason,
        )
        for row in rows
    )
    return AnalysisReplay(binding.strategy_hash, binding.fingerprint, (), replay_trades, None)
