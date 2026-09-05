from __future__ import annotations

"""M179 matched forecast ablation laboratory."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .forecast_campaign import EXPECTED_PROVIDERS


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


class AblationEffect(StrEnum):
    BENEFICIAL = "beneficial"
    NEUTRAL = "neutral"
    HARMFUL = "harmful"


@dataclass(frozen=True, slots=True)
class ForecastAblationVariant:
    providers: tuple[str, ...]

    def __post_init__(self) -> None:
        providers = tuple(sorted(str(value).strip().lower() for value in self.providers))
        if len(providers) != len(set(providers)) or any(value not in EXPECTED_PROVIDERS for value in providers):
            raise ValueError("ablation provider set must be unique and known")
        object.__setattr__(self, "providers", providers)

    @property
    def is_control(self) -> bool:
        return not self.providers

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m179-ablation-variant-v1", self.providers))


@dataclass(frozen=True, slots=True)
class ForecastAblationResult:
    strategy_fingerprint: str
    evaluation_fingerprint: str
    execution_cost_fingerprint: str
    variant: ForecastAblationVariant
    net_return: float
    max_drawdown: float
    trade_count: int
    passed: bool

    def __post_init__(self) -> None:
        for name in ("strategy_fingerprint", "evaluation_fingerprint", "execution_cost_fingerprint"):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        object.__setattr__(self, "net_return", _finite(self.net_return, "net_return"))
        drawdown = _finite(self.max_drawdown, "max_drawdown")
        if drawdown < 0:
            raise ValueError("max_drawdown must be nonnegative")
        object.__setattr__(self, "max_drawdown", drawdown)
        if isinstance(self.trade_count, bool) or int(self.trade_count) != self.trade_count or int(self.trade_count) < 0:
            raise ValueError("trade_count must be nonnegative")
        object.__setattr__(self, "trade_count", int(self.trade_count))

    @property
    def fingerprint(self) -> str:
        return _digest((self.strategy_fingerprint, self.evaluation_fingerprint, self.execution_cost_fingerprint, self.variant.fingerprint, self.net_return, self.max_drawdown, self.trade_count, self.passed))


@dataclass(frozen=True, slots=True)
class AblationPolicy:
    neutral_return_delta: float = 0.0

    def __post_init__(self) -> None:
        value = _finite(self.neutral_return_delta, "neutral_return_delta")
        if value < 0:
            raise ValueError("neutral_return_delta must be nonnegative")


@dataclass(frozen=True, slots=True)
class ForecastAblationComparison:
    strategy_fingerprint: str
    evaluation_fingerprint: str
    execution_cost_fingerprint: str
    variant: ForecastAblationVariant
    control_fingerprint: str
    variant_result_fingerprint: str
    effect: AblationEffect
    net_return_delta: float
    max_drawdown_delta: float
    trade_count_delta: int
    pass_changed: bool

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m179-ablation-comparison-v1", self.strategy_fingerprint, self.evaluation_fingerprint, self.execution_cost_fingerprint, self.variant.fingerprint, self.control_fingerprint, self.variant_result_fingerprint, self.effect.value, self.net_return_delta, self.max_drawdown_delta, self.trade_count_delta, self.pass_changed))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def strategy_mutation_authority(self) -> bool:
        return False


def compare_forecast_ablations(
    results: Iterable[ForecastAblationResult],
    *,
    policy: AblationPolicy = AblationPolicy(),
) -> tuple[ForecastAblationComparison, ...]:
    rows = tuple(results)
    if len(rows) < 2:
        raise ValueError("ablation laboratory requires control and at least one forecast variant")
    identities = {(row.strategy_fingerprint, row.evaluation_fingerprint, row.execution_cost_fingerprint) for row in rows}
    if len(identities) != 1:
        raise ValueError("ablation results must share frozen strategy/evaluation/execution-cost identity")
    variant_fps = tuple(row.variant.fingerprint for row in rows)
    if len(variant_fps) != len(set(variant_fps)):
        raise ValueError("ablation variants must be unique")
    controls = tuple(row for row in rows if row.variant.is_control)
    if len(controls) != 1:
        raise ValueError("ablation laboratory requires exactly one NO_FORECAST control")
    control = controls[0]
    comparisons: list[ForecastAblationComparison] = []
    for row in sorted((value for value in rows if not value.variant.is_control), key=lambda value: value.variant.providers):
        delta = row.net_return - control.net_return
        threshold = policy.neutral_return_delta
        effect = AblationEffect.BENEFICIAL if delta > threshold else (AblationEffect.HARMFUL if delta < -threshold else AblationEffect.NEUTRAL)
        comparisons.append(ForecastAblationComparison(row.strategy_fingerprint, row.evaluation_fingerprint, row.execution_cost_fingerprint, row.variant, control.fingerprint, row.fingerprint, effect, delta, row.max_drawdown - control.max_drawdown, row.trade_count - control.trade_count, row.passed != control.passed))
    return tuple(comparisons)
