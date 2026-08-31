from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Callable, Iterable, Mapping

from .experience import TradeSide
from .research import Scalar
from .strategy_ir import StrategySpecV2


_EXECUTION_PRICE_KEY = "__execution_price__"


class PriceRuleKind(StrEnum):
    OFF = "off"
    PCT = "pct"
    PRICE = "price"
    ATR = "atr"
    RR = "rr"


@dataclass(frozen=True, slots=True)
class PriceRule:
    kind: PriceRuleKind
    value: float = 0.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.value) or self.value < 0:
            raise ValueError("price-rule value must be finite and nonnegative")
        if self.kind is not PriceRuleKind.OFF and self.value <= 0:
            raise ValueError("active price-rule value must be positive")


@dataclass(frozen=True, slots=True)
class CompiledStrategy:
    spec: StrategySpecV2
    stop: PriceRule
    target: PriceRule
    trailing: PriceRule
    breakeven_rr: float | None

    @property
    def strategy_hash(self) -> str:
        return self.spec.strategy_hash

    def entry_matches(self, features: Mapping[str, Scalar], *, session: str = "", event_blocked: bool = False) -> bool:
        if event_blocked and self.spec.event_exclusion_minutes > 0:
            return False
        if self.spec.session_filters and session.upper() not in {item.upper() for item in self.spec.session_filters}:
            return False
        return self.spec.entry_matches(features)

    def initial_stop(self, entry_price: float, features: Mapping[str, Scalar]) -> float:
        return _protective_price(self.stop, entry_price, self.spec.direction, features, protective=True)

    def initial_target(self, entry_price: float, stop_price: float, features: Mapping[str, Scalar]) -> float | None:
        if self.target.kind is PriceRuleKind.OFF:
            return None
        if self.target.kind is PriceRuleKind.RR:
            risk = abs(entry_price - stop_price)
            direction = 1.0 if self.spec.direction is TradeSide.LONG else -1.0
            return entry_price + direction * risk * self.target.value
        return _protective_price(self.target, entry_price, self.spec.direction, features, protective=False)

    def tightened_stop(
        self,
        *,
        entry_price: float,
        current_stop: float,
        current_price: float,
        features: Mapping[str, Scalar],
    ) -> float:
        candidates = [current_stop]
        initial_risk = abs(entry_price - current_stop)
        direction = 1.0 if self.spec.direction is TradeSide.LONG else -1.0
        favorable = direction * (current_price - entry_price)
        if self.breakeven_rr is not None and initial_risk > 0 and favorable >= initial_risk * self.breakeven_rr:
            candidates.append(entry_price)
        if self.trailing.kind is not PriceRuleKind.OFF:
            trail = _distance(self.trailing, current_price, features)
            candidates.append(current_price - trail if self.spec.direction is TradeSide.LONG else current_price + trail)
        if self.spec.direction is TradeSide.LONG:
            return max(candidates)
        return min(candidates)


def _rule(text: str, *, allow_off: bool, allow_rr: bool) -> PriceRule:
    raw = text.strip().lower()
    if raw in {"", "off"}:
        if allow_off:
            return PriceRule(PriceRuleKind.OFF)
        raise ValueError("this strategy rule cannot be off")
    if ":" not in raw:
        raise ValueError(f"strategy rule must use typed form kind:value, got {text!r}")
    kind_text, value_text = raw.split(":", 1)
    try:
        kind = PriceRuleKind(kind_text)
    except ValueError as exc:
        raise ValueError(f"unsupported strategy price rule: {kind_text}") from exc
    if kind is PriceRuleKind.OFF or (kind is PriceRuleKind.RR and not allow_rr):
        raise ValueError(f"rule kind {kind.value} is not allowed here")
    value = float(value_text)
    return PriceRule(kind, value)


def compile_strategy(spec: StrategySpecV2) -> CompiledStrategy:
    """Compile StrategySpecV2 into the only executable Dusty strategy semantics.

    Free-form exit strings remain representable for research provenance, but they are not executable.
    Promotion to runtime requires the small typed DSL enforced here. StrategySpecV2 can represent
    future scaling semantics, but the current single-position runtime refuses those fields rather than
    silently ignoring them.
    """
    if spec.scale_in_limit:
        raise ValueError("runtime_scaling_not_supported:scale_in_limit")
    if spec.scale_out_fractions:
        raise ValueError("runtime_scaling_not_supported:scale_out_fractions")
    stop = _rule(spec.exit_plan.stop_rule, allow_off=False, allow_rr=False)
    if stop.kind is PriceRuleKind.RR:
        raise ValueError("initial stop cannot be an RR rule")
    target = _rule(spec.exit_plan.target_rule, allow_off=True, allow_rr=True)
    trailing = _rule(spec.exit_plan.trailing_rule, allow_off=True, allow_rr=False)
    breakeven_raw = spec.exit_plan.breakeven_rule.strip().lower()
    if breakeven_raw in {"", "off"}:
        breakeven = None
    else:
        parsed = _rule(breakeven_raw, allow_off=False, allow_rr=True)
        if parsed.kind is not PriceRuleKind.RR:
            raise ValueError("breakeven rule must be off or rr:<multiple>")
        breakeven = parsed.value
    return CompiledStrategy(spec, stop, target, trailing, breakeven)


def _feature_float(features: Mapping[str, Scalar], name: str) -> float:
    value = features.get(name)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"runtime feature {name!r} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"runtime feature {name!r} must be finite and positive")
    return result


def _distance(rule: PriceRule, reference_price: float, features: Mapping[str, Scalar]) -> float:
    if not math.isfinite(reference_price) or reference_price <= 0:
        raise ValueError("reference price must be finite and positive")
    if rule.kind is PriceRuleKind.PCT:
        return reference_price * rule.value
    if rule.kind is PriceRuleKind.ATR:
        return _feature_float(features, "atr") * rule.value
    raise ValueError(f"rule {rule.kind.value} does not define a distance")


def _protective_price(
    rule: PriceRule,
    entry_price: float,
    side: TradeSide,
    features: Mapping[str, Scalar],
    *,
    protective: bool,
) -> float:
    if not math.isfinite(entry_price) or entry_price <= 0:
        raise ValueError("entry price must be finite and positive")
    if rule.kind is PriceRuleKind.PRICE:
        result = rule.value
    elif rule.kind in {PriceRuleKind.PCT, PriceRuleKind.ATR}:
        distance = _distance(rule, entry_price, features)
        if side is TradeSide.LONG:
            result = entry_price - distance if protective else entry_price + distance
        else:
            result = entry_price + distance if protective else entry_price - distance
    else:
        raise ValueError(f"rule {rule.kind.value} cannot produce an absolute price")
    if result <= 0 or not math.isfinite(result):
        raise ValueError("derived strategy price must be finite and positive")
    if protective:
        valid = result < entry_price if side is TradeSide.LONG else result > entry_price
    else:
        valid = result > entry_price if side is TradeSide.LONG else result < entry_price
    if not valid:
        raise ValueError("strategy protective/target price is on the wrong side of entry")
    return result


@dataclass(frozen=True, slots=True)
class RuntimeBar:
    at: datetime
    open: float
    high: float
    low: float
    close: float
    features: tuple[tuple[str, Scalar], ...]
    session: str = ""
    event_blocked: bool = False
    execution_price: float | None = None

    @classmethod
    def of(
        cls,
        at: datetime,
        *,
        open: float,
        high: float,
        low: float,
        close: float,
        features: Mapping[str, Scalar],
        session: str = "",
        event_blocked: bool = False,
        execution_price: float | None = None,
    ) -> "RuntimeBar":
        reserved = features.get(_EXECUTION_PRICE_KEY)
        if execution_price is None and reserved is not None:
            if isinstance(reserved, bool) or not isinstance(reserved, (int, float)):
                raise ValueError("reserved execution price must be numeric")
            execution_price = float(reserved)
        return cls(
            at,
            open,
            high,
            low,
            close,
            tuple(sorted(features.items())),
            session,
            event_blocked,
            execution_price,
        )

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("runtime bars must be timezone-aware")
        if any(not math.isfinite(value) or value <= 0 for value in (self.open, self.high, self.low, self.close)):
            raise ValueError("runtime OHLC prices must be finite and positive")
        if self.execution_price is not None and (
            not math.isfinite(self.execution_price) or self.execution_price <= 0
        ):
            raise ValueError("runtime execution price must be finite and positive")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close, self.high):
            raise ValueError("runtime OHLC geometry is invalid")

    @property
    def market_price(self) -> float:
        return self.close if self.execution_price is None else self.execution_price

    def feature_map(self) -> dict[str, Scalar]:
        return dict(self.features)


@dataclass(frozen=True, slots=True)
class RuntimeTrade:
    strategy_hash: str
    entry_at: datetime
    exit_at: datetime
    side: TradeSide
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float | None
    exit_reason: str
    exit_stop_price: float | None = None


EntryAuthorizer = Callable[[RuntimeBar, CompiledStrategy], bool]


def generate_runtime_trades(
    compiled: CompiledStrategy,
    bars: Iterable[RuntimeBar],
    *,
    entry_authorizer: EntryAuthorizer | None = None,
) -> tuple[RuntimeTrade, ...]:
    """Single-position deterministic interpreter used by research, shadow and future demo intent generation.

    A rule can become true only after the completed observation bar is available; entry and time-based
    exit references therefore use ``RuntimeBar.market_price`` rather than silently reusing the completed
    bar's old close. ``stop_price`` is the immutable initial protective stop used for sizing and MT5
    manifest parity. ``exit_stop_price`` records the final tightened stop for audit. If stop and target
    are both touched inside one bar, stop wins. An optional entry_authorizer can only veto a rule-matched
    entry; it cannot create an entry when strategy rules fail. Cooldown is enforced after each completed
    trade. Scaling remains an explicit compile-time rejection until quantity-aware runtime semantics exist.
    """
    rows = tuple(bars)
    if tuple(sorted(rows, key=lambda row: row.at)) != rows:
        raise ValueError("runtime bars must be chronological")
    trades: list[RuntimeTrade] = []
    entry_bar: RuntimeBar | None = None
    entry_price = stop_price = initial_stop_price = 0.0
    target_price: float | None = None
    held_steps = 0
    cooldown_remaining = 0
    for bar in rows:
        features = bar.feature_map()
        if entry_bar is None:
            if cooldown_remaining > 0:
                cooldown_remaining -= 1
                continue
            matches = compiled.entry_matches(features, session=bar.session, event_blocked=bar.event_blocked)
            authorized = entry_authorizer is None or (matches and entry_authorizer(bar, compiled))
            if matches and authorized:
                entry_bar = bar
                entry_price = bar.market_price
                initial_stop_price = compiled.initial_stop(entry_price, features)
                stop_price = initial_stop_price
                target_price = compiled.initial_target(entry_price, stop_price, features)
                held_steps = 0
            continue

        held_steps += 1
        side = compiled.spec.direction
        stop_hit = bar.low <= stop_price if side is TradeSide.LONG else bar.high >= stop_price
        target_hit = False
        if target_price is not None:
            target_hit = bar.high >= target_price if side is TradeSide.LONG else bar.low <= target_price
        reason = ""
        exit_price = 0.0
        if stop_hit:
            reason = "stop"
            if side is TradeSide.LONG and bar.open < stop_price:
                exit_price = bar.open
            elif side is TradeSide.SHORT and bar.open > stop_price:
                exit_price = bar.open
            else:
                exit_price = stop_price
        elif target_hit:
            reason, exit_price = "target", target_price or bar.close
        elif held_steps >= compiled.spec.exit_plan.max_hold_steps:
            reason, exit_price = "max_hold", bar.market_price
        if reason:
            trades.append(
                RuntimeTrade(
                    compiled.strategy_hash,
                    entry_bar.at,
                    bar.at,
                    side,
                    entry_price,
                    exit_price,
                    initial_stop_price,
                    target_price,
                    reason,
                    exit_stop_price=stop_price,
                )
            )
            entry_bar = None
            target_price = None
            held_steps = 0
            cooldown_remaining = compiled.spec.cooldown_steps
            continue
        stop_price = compiled.tightened_stop(
            entry_price=entry_price,
            current_stop=stop_price,
            current_price=bar.market_price,
            features=features,
        )
    return tuple(trades)
