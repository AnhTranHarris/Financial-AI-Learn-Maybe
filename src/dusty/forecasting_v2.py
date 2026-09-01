from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256


class ForecastTargetKind(StrEnum):
    RETURN = "return"
    PRICE = "price"
    VOLATILITY = "volatility"


@dataclass(frozen=True, slots=True)
class ForecastModelIdentity:
    provider: str
    model_name: str
    version: str
    artifact_hash: str
    config_hash: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.provider, self.model_name, self.version)):
            raise ValueError("forecast model identity is incomplete")
        if any(len(value) != 64 for value in (self.artifact_hash, self.config_hash)):
            raise ValueError("forecast model artifacts require SHA-256 identity")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "provider": self.provider,
                "model_name": self.model_name,
                "version": self.version,
                "artifact_hash": self.artifact_hash,
                "config_hash": self.config_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class ForecastKey:
    symbol: str
    timeframe: str
    issued_at: datetime
    origin_at: datetime
    horizon_steps: int
    target: ForecastTargetKind
    regime: str = "unclassified"

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip() or not self.regime.strip():
            raise ValueError("forecast key requires symbol, timeframe and regime")
        _aware(self.issued_at, "forecast issue time")
        _aware(self.origin_at, "forecast origin time")
        if self.origin_at > self.issued_at:
            raise ValueError("forecast origin cannot lie after issue time")
        if self.horizon_steps < 1:
            raise ValueError("forecast horizon must be positive")


@dataclass(frozen=True, slots=True)
class QuantilePoint:
    level: float
    value: float

    def __post_init__(self) -> None:
        if not math.isfinite(self.level) or not 0 < self.level < 1:
            raise ValueError("quantile level must lie in (0,1)")
        if not math.isfinite(self.value):
            raise ValueError("quantile value must be finite")


@dataclass(frozen=True, slots=True)
class ProbabilisticForecast:
    model: ForecastModelIdentity
    key: ForecastKey
    origin_value: float
    quantiles: tuple[QuantilePoint, ...]
    probability_up: float
    training_cutoff: datetime
    valid_until: datetime
    context_hash: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.origin_value) or self.origin_value <= 0:
            raise ValueError("forecast origin value must be finite and positive")
        if len(self.quantiles) < 3:
            raise ValueError("probabilistic forecast requires at least three quantiles")
        levels = tuple(item.level for item in self.quantiles)
        values = tuple(item.value for item in self.quantiles)
        if levels != tuple(sorted(levels)) or len(set(levels)) != len(levels):
            raise ValueError("forecast quantile levels must be unique and ordered")
        if values != tuple(sorted(values)):
            raise ValueError("forecast quantiles cannot cross")
        if not any(abs(level - 0.5) <= 1e-12 for level in levels):
            raise ValueError("forecast requires an explicit median")
        if self.key.target is ForecastTargetKind.PRICE and any(value <= 0 for value in values):
            raise ValueError("price forecast quantiles must be positive")
        if self.key.target is ForecastTargetKind.VOLATILITY and any(value < 0 for value in values):
            raise ValueError("volatility forecast quantiles cannot be negative")
        if not math.isfinite(self.probability_up) or not 0 <= self.probability_up <= 1:
            raise ValueError("probability_up must lie in [0,1]")
        _aware(self.training_cutoff, "forecast training cutoff")
        _aware(self.valid_until, "forecast validity")
        if self.training_cutoff > self.key.issued_at:
            raise ValueError("forecast training data cannot extend beyond issue time")
        if self.valid_until <= self.key.issued_at:
            raise ValueError("forecast validity must follow issue time")
        if len(self.context_hash) != 64:
            raise ValueError("forecast context requires SHA-256 identity")

    @property
    def median(self) -> float:
        return next(item.value for item in self.quantiles if abs(item.level - 0.5) <= 1e-12)

    def quantile(self, level: float) -> float:
        for item in self.quantiles:
            if abs(item.level - level) <= 1e-12:
                return item.value
        raise KeyError(f"forecast does not contain quantile {level}")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "model": self.model.fingerprint,
                "key": {
                    "symbol": self.key.symbol.upper(),
                    "timeframe": self.key.timeframe.upper(),
                    "issued_at": self.key.issued_at.isoformat(),
                    "origin_at": self.key.origin_at.isoformat(),
                    "horizon_steps": self.key.horizon_steps,
                    "target": self.key.target.value,
                    "regime": self.key.regime,
                },
                "origin_value": self.origin_value,
                "quantiles": tuple((item.level, item.value) for item in self.quantiles),
                "probability_up": self.probability_up,
                "training_cutoff": self.training_cutoff.isoformat(),
                "valid_until": self.valid_until.isoformat(),
                "context_hash": self.context_hash,
            }
        )


@dataclass(frozen=True, slots=True)
class ForecastRealization:
    symbol: str
    timeframe: str
    issued_at: datetime
    horizon_steps: int
    target: ForecastTargetKind
    realized_at: datetime
    value: float
    origin_value: float
    regime: str = "unclassified"

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip() or not self.regime.strip():
            raise ValueError("forecast realization identity is incomplete")
        _aware(self.issued_at, "realization issue time")
        _aware(self.realized_at, "realization time")
        if self.realized_at <= self.issued_at or self.horizon_steps < 1:
            raise ValueError("forecast realization timing is invalid")
        if not math.isfinite(self.value) or not math.isfinite(self.origin_value) or self.origin_value <= 0:
            raise ValueError("forecast realization values must be finite with positive origin")

    def matches(self, forecast: ProbabilisticForecast) -> bool:
        key = forecast.key
        return (
            self.symbol.upper() == key.symbol.upper()
            and self.timeframe.upper() == key.timeframe.upper()
            and self.issued_at == key.issued_at
            and self.horizon_steps == key.horizon_steps
            and self.target is key.target
            and self.regime == key.regime
            and abs(self.origin_value - forecast.origin_value) <= 1e-12
        )


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _digest(payload: object) -> str:
    return sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()
