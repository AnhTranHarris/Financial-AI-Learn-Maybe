from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Mapping

from .core import HealthState
from .experience import TradeSide


class OrderStyle(StrEnum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class TradeDirective(StrEnum):
    WAIT = "wait"
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    HOLD = "hold"
    TIGHTEN_PROTECTION = "tighten_protection"
    PARTIAL_EXIT = "partial_exit"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class EntryPolicy:
    long_output: str
    short_output: str
    order_style: OrderStyle = OrderStyle.MARKET
    pending_expiry_steps: int = 0

    def __post_init__(self) -> None:
        if not self.long_output.strip() or not self.short_output.strip() or self.long_output == self.short_output:
            raise ValueError("entry policy requires distinct long and short outputs")
        if self.pending_expiry_steps < 0:
            raise ValueError("pending expiry steps cannot be negative")
        if self.order_style is not OrderStyle.MARKET and self.pending_expiry_steps < 1:
            raise ValueError("pending entry requires a positive expiry")


@dataclass(frozen=True, slots=True)
class HoldPolicy:
    long_output: str
    short_output: str
    max_hold_steps: int
    tighten_long_output: str = ""
    tighten_short_output: str = ""

    def __post_init__(self) -> None:
        if not self.long_output.strip() or not self.short_output.strip() or self.max_hold_steps < 1:
            raise ValueError("hold policy requires long/short outputs and a positive maximum")
        if bool(self.tighten_long_output) != bool(self.tighten_short_output):
            raise ValueError("protection tightening outputs must be configured symmetrically")


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    long_output: str
    short_output: str
    partial_long_output: str = ""
    partial_short_output: str = ""
    partial_fraction: float = 0.0

    def __post_init__(self) -> None:
        if not self.long_output.strip() or not self.short_output.strip():
            raise ValueError("exit policy requires long and short outputs")
        if bool(self.partial_long_output) != bool(self.partial_short_output):
            raise ValueError("partial exit outputs must be configured symmetrically")
        if self.partial_long_output and not 0 < self.partial_fraction < 1:
            raise ValueError("partial exit requires a fraction in (0,1)")
        if not self.partial_long_output and self.partial_fraction != 0:
            raise ValueError("partial fraction requires partial exit outputs")


@dataclass(frozen=True, slots=True)
class ProtectionPolicy:
    stop_rule: str
    target_rule: str = "off"
    trailing_rule: str = "off"
    breakeven_rule: str = "off"
    stop_widening_allowed: bool = False

    def __post_init__(self) -> None:
        if not self.stop_rule.strip() or self.stop_rule.strip().lower() == "off":
            raise ValueError("strategy v3 requires an initial stop")
        if self.stop_widening_allowed:
            raise ValueError("Dusty constitution prohibits stop widening")


@dataclass(frozen=True, slots=True)
class StrategySpecV3:
    strategy_id: str
    analysis_graph_hash: str
    tool_fingerprints: tuple[str, ...]
    entry: EntryPolicy
    hold: HoldPolicy
    exit: ExitPolicy
    protection: ProtectionPolicy
    source_reference: str
    decision_timeframe: str
    intended_horizon_minutes: int
    unresolved_claims: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or len(self.analysis_graph_hash) != 64:
            raise ValueError("strategy v3 requires identity and graph hash")
        if not self.tool_fingerprints or any(len(value) != 64 for value in self.tool_fingerprints):
            raise ValueError("strategy v3 requires SHA-256 tool dependencies")
        if len(set(self.tool_fingerprints)) != len(self.tool_fingerprints):
            raise ValueError("strategy v3 tool dependencies must be unique")
        if not self.source_reference.strip() or not self.decision_timeframe.strip():
            raise ValueError("strategy v3 requires source and timeframe")
        if self.intended_horizon_minutes < 15:
            raise ValueError("strategy v3 prohibits horizons below 15 minutes")
        if self.unresolved_claims:
            raise ValueError("unresolved source claims cannot become executable strategy semantics")

    @property
    def strategy_hash(self) -> str:
        payload = {
            "strategy_id": self.strategy_id,
            "analysis_graph_hash": self.analysis_graph_hash,
            "tool_fingerprints": self.tool_fingerprints,
            "entry": {
                "long": self.entry.long_output,
                "short": self.entry.short_output,
                "order_style": self.entry.order_style.value,
                "pending_expiry_steps": self.entry.pending_expiry_steps,
            },
            "hold": {
                "long": self.hold.long_output,
                "short": self.hold.short_output,
                "max_hold_steps": self.hold.max_hold_steps,
                "tighten_long": self.hold.tighten_long_output,
                "tighten_short": self.hold.tighten_short_output,
            },
            "exit": {
                "long": self.exit.long_output,
                "short": self.exit.short_output,
                "partial_long": self.exit.partial_long_output,
                "partial_short": self.exit.partial_short_output,
                "partial_fraction": self.exit.partial_fraction,
            },
            "protection": {
                "stop": self.protection.stop_rule,
                "target": self.protection.target_rule,
                "trailing": self.protection.trailing_rule,
                "breakeven": self.protection.breakeven_rule,
                "stop_widening_allowed": self.protection.stop_widening_allowed,
            },
            "source_reference": self.source_reference,
            "decision_timeframe": self.decision_timeframe,
            "intended_horizon_minutes": self.intended_horizon_minutes,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PositionView:
    side: TradeSide
    entry_price: float
    current_stop: float
    volume: float
    held_steps: int

    def __post_init__(self) -> None:
        if any(not math.isfinite(value) or value <= 0 for value in (self.entry_price, self.current_stop, self.volume)):
            raise ValueError("position view economics must be finite and positive")
        if self.held_steps < 0:
            raise ValueError("position held steps cannot be negative")


@dataclass(frozen=True, slots=True)
class TradeLifecycleRequest:
    at: datetime
    outputs: tuple[tuple[str, bool], ...]
    health: HealthState
    position: PositionView | None = None
    event_blocked: bool = False
    spread_acceptable: bool = True
    governance_approved: bool = True
    tools_valid: bool = True

    @classmethod
    def of(
        cls,
        at: datetime,
        outputs: Mapping[str, bool],
        health: HealthState,
        **kwargs: object,
    ) -> "TradeLifecycleRequest":
        return cls(at, tuple(sorted(outputs.items())), health, **kwargs)

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("trade lifecycle time must be timezone-aware")
        if len(dict(self.outputs)) != len(self.outputs) or any(not isinstance(value, bool) for _, value in self.outputs):
            raise ValueError("trade lifecycle outputs must be unique booleans")


@dataclass(frozen=True, slots=True)
class TradeLifecycleDecision:
    directive: TradeDirective
    side: TradeSide | None
    order_style: OrderStyle | None
    partial_fraction: float
    reasons: tuple[str, ...]


def reason_trade_lifecycle(strategy: StrategySpecV3, request: TradeLifecycleRequest) -> TradeLifecycleDecision:
    values = dict(request.outputs)

    def signal(name: str) -> bool:
        if name not in values:
            raise ValueError(f"strategy output missing at runtime: {name}")
        return values[name]

    unsafe = []
    if not request.tools_valid:
        unsafe.append("analytical_tool_invalid")
    if request.health is HealthState.FAILED:
        unsafe.append(f"health:{request.health.value}")
    if not request.governance_approved:
        unsafe.append("governance_not_approved")

    if request.position is None:
        if unsafe or request.event_blocked or not request.spread_acceptable:
            reasons = unsafe + (["event_blocked"] if request.event_blocked else []) + (["spread_unacceptable"] if not request.spread_acceptable else [])
            return TradeLifecycleDecision(TradeDirective.WAIT, None, None, 0.0, tuple(reasons))
        long_entry = signal(strategy.entry.long_output)
        short_entry = signal(strategy.entry.short_output)
        if long_entry and short_entry:
            return TradeLifecycleDecision(TradeDirective.WAIT, None, None, 0.0, ("conflicting_entry_signals",))
        if long_entry:
            return TradeLifecycleDecision(TradeDirective.ENTER_LONG, TradeSide.LONG, strategy.entry.order_style, 0.0, ("long_entry_graph_true",))
        if short_entry:
            return TradeLifecycleDecision(TradeDirective.ENTER_SHORT, TradeSide.SHORT, strategy.entry.order_style, 0.0, ("short_entry_graph_true",))
        return TradeLifecycleDecision(TradeDirective.WAIT, None, None, 0.0, ("no_entry_setup",))

    side = request.position.side
    if unsafe:
        return TradeLifecycleDecision(TradeDirective.EXIT, side, None, 0.0, tuple(unsafe))
    exit_name = strategy.exit.long_output if side is TradeSide.LONG else strategy.exit.short_output
    partial_name = strategy.exit.partial_long_output if side is TradeSide.LONG else strategy.exit.partial_short_output
    hold_name = strategy.hold.long_output if side is TradeSide.LONG else strategy.hold.short_output
    tighten_name = strategy.hold.tighten_long_output if side is TradeSide.LONG else strategy.hold.tighten_short_output
    if signal(exit_name):
        return TradeLifecycleDecision(TradeDirective.EXIT, side, None, 0.0, ("exit_graph_true",))
    if request.position.held_steps >= strategy.hold.max_hold_steps:
        return TradeLifecycleDecision(TradeDirective.EXIT, side, None, 0.0, ("maximum_hold_reached",))
    if partial_name and signal(partial_name):
        return TradeLifecycleDecision(TradeDirective.PARTIAL_EXIT, side, None, strategy.exit.partial_fraction, ("partial_exit_graph_true",))
    if not signal(hold_name):
        return TradeLifecycleDecision(TradeDirective.EXIT, side, None, 0.0, ("hold_thesis_invalidated",))
    if tighten_name and signal(tighten_name):
        return TradeLifecycleDecision(TradeDirective.TIGHTEN_PROTECTION, side, None, 0.0, ("protection_tightening_graph_true",))
    return TradeLifecycleDecision(TradeDirective.HOLD, side, None, 0.0, ("hold_thesis_valid",))


@dataclass(frozen=True, slots=True)
class SourceStrategyClaim:
    claim_id: str
    source_url: str
    captured_at: datetime
    declared_rules: tuple[tuple[str, str], ...]
    unresolved: tuple[str, ...] = ()
    executable_source_supplied: bool = False

    def __post_init__(self) -> None:
        if not self.claim_id.strip() or not self.source_url.startswith("https://") or not self.declared_rules:
            raise ValueError("source strategy claim requires identity, HTTPS source and rules")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("source strategy capture time must be timezone-aware")


@dataclass(frozen=True, slots=True)
class StrategyTranslation:
    claim_id: str
    research_ready: bool
    normalized_rules: tuple[tuple[str, str], ...]
    reasons: tuple[str, ...]
    executable_source_accepted: bool = False


def translate_source_claim(claim: SourceStrategyClaim) -> StrategyTranslation:
    reasons: list[str] = []
    if claim.unresolved:
        reasons.extend(f"unresolved:{value}" for value in claim.unresolved)
    rules = tuple(sorted((name.strip().lower(), value.strip()) for name, value in claim.declared_rules))
    if len({name for name, _ in rules}) != len(rules):
        reasons.append("duplicate_rule_name")
    if any(not name or not value for name, value in rules):
        reasons.append("empty_rule")
    if claim.executable_source_supplied:
        reasons.append("external_executable_quarantined")
    return StrategyTranslation(claim.claim_id, not reasons, rules, tuple(reasons), False)


@dataclass(frozen=True, slots=True)
class FrozenStrategyDeployment:
    strategy_hash: str
    graph_hash: str
    tool_fingerprints: tuple[str, ...]
    generation_id: str

    def __post_init__(self) -> None:
        values = (self.strategy_hash, self.graph_hash, *self.tool_fingerprints)
        if any(len(value) != 64 for value in values) or not self.generation_id.strip():
            raise ValueError("frozen deployment requires hashes and generation identity")

    def verify(
        self,
        *,
        strategy_hash: str,
        graph_hash: str,
        tool_fingerprints: tuple[str, ...],
    ) -> tuple[bool, tuple[str, ...]]:
        reasons: list[str] = []
        if strategy_hash != self.strategy_hash:
            reasons.append("strategy_hash_drift")
        if graph_hash != self.graph_hash:
            reasons.append("analysis_graph_drift")
        if tool_fingerprints != self.tool_fingerprints:
            reasons.append("analytical_tool_drift")
        return not reasons, tuple(reasons)
