from __future__ import annotations

"""Read-only planning primitives for the first genuine M165 calibration round trip.

This module has no MetaTrader5 import, no execution adapter and no broker-write
or promotion authority.  It derives a minimum-lot calibration envelope from
native symbol geometry and Dusty's existing normal-trade risk constitution.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import math

from .risk import RiskConstitution


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _positive(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or rendered <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return rendered


@dataclass(frozen=True, slots=True)
class CalibrationPlanningPolicy:
    normal_risk_fraction: float = RiskConstitution().normal_trade_risk
    maximum_geometry_probes: int = 18
    geometry_growth_factor: float = 1.5

    def __post_init__(self) -> None:
        risk = float(self.normal_risk_fraction)
        if not math.isfinite(risk) or not 0 < risk <= RiskConstitution().normal_trade_risk:
            raise ValueError("calibration risk cannot exceed Dusty normal trade risk")
        if isinstance(self.maximum_geometry_probes, bool) or not 1 <= int(self.maximum_geometry_probes) <= 64:
            raise ValueError("maximum_geometry_probes out of range")
        growth = float(self.geometry_growth_factor)
        if not math.isfinite(growth) or not 1.01 <= growth <= 4.0:
            raise ValueError("geometry_growth_factor out of range")


@dataclass(frozen=True, slots=True)
class CalibrationNativeEnvelope:
    symbol: str
    equity: float
    minimum_volume: float
    volume_step: float
    point: float
    trade_tick_size: float
    stops_level_points: int
    freeze_level_points: int
    loss_ceiling_cash: float
    stop_distance_candidates: tuple[float, ...]
    policy_fingerprint: str

    def __post_init__(self) -> None:
        symbol = str(self.symbol).strip().upper()
        if not symbol or len(symbol) > 64:
            raise ValueError("calibration symbol required")
        object.__setattr__(self, "symbol", symbol)
        for name in ("equity", "minimum_volume", "volume_step", "point", "trade_tick_size", "loss_ceiling_cash"):
            object.__setattr__(self, name, _positive(getattr(self, name), name))
        if isinstance(self.stops_level_points, bool) or int(self.stops_level_points) < 0:
            raise ValueError("stops_level_points must be nonnegative")
        if isinstance(self.freeze_level_points, bool) or int(self.freeze_level_points) < 0:
            raise ValueError("freeze_level_points must be nonnegative")
        if not math.isclose(self.minimum_volume / self.volume_step, round(self.minimum_volume / self.volume_step), rel_tol=1e-9, abs_tol=1e-9):
            raise ValueError("minimum volume must align to native volume step")
        rows = tuple(float(value) for value in self.stop_distance_candidates)
        if not rows or any(not math.isfinite(value) or value <= 0 for value in rows):
            raise ValueError("stop distance candidates must be positive")
        if any(rows[index] <= rows[index - 1] for index in range(1, len(rows))):
            raise ValueError("stop distance candidates must be strictly increasing")
        object.__setattr__(self, "stop_distance_candidates", rows)
        fp = str(self.policy_fingerprint).strip().lower()
        if len(fp) != 64 or any(ch not in "0123456789abcdef" for ch in fp):
            raise ValueError("policy_fingerprint requires SHA-256")
        object.__setattr__(self, "policy_fingerprint", fp)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m165-calibration-native-envelope-v1",
            "symbol": self.symbol,
            "equity": self.equity,
            "minimum_volume": self.minimum_volume,
            "volume_step": self.volume_step,
            "point": self.point,
            "trade_tick_size": self.trade_tick_size,
            "stops_level_points": self.stops_level_points,
            "freeze_level_points": self.freeze_level_points,
            "loss_ceiling_cash": self.loss_ceiling_cash,
            "stop_distance_candidates": list(self.stop_distance_candidates),
            "policy_fingerprint": self.policy_fingerprint,
            "authority": {"broker_write": False, "live_write": False, "promotion": False},
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False


def planning_policy_fingerprint(policy: CalibrationPlanningPolicy) -> str:
    return _digest(
        {
            "protocol": "dusty-m165-calibration-planning-policy-v1",
            "normal_risk_fraction": float(policy.normal_risk_fraction),
            "maximum_geometry_probes": int(policy.maximum_geometry_probes),
            "geometry_growth_factor": float(policy.geometry_growth_factor),
        }
    )


def build_native_envelope(
    *,
    symbol: str,
    equity: float,
    minimum_volume: float,
    volume_step: float,
    point: float,
    trade_tick_size: float,
    stops_level_points: int,
    freeze_level_points: int,
    policy: CalibrationPlanningPolicy = CalibrationPlanningPolicy(),
) -> CalibrationNativeEnvelope:
    equity_value = _positive(equity, "equity")
    volume = _positive(minimum_volume, "minimum_volume")
    step = _positive(volume_step, "volume_step")
    point_value = _positive(point, "point")
    tick_size = _positive(trade_tick_size, "trade_tick_size")
    stops = int(stops_level_points)
    freeze = int(freeze_level_points)
    if stops < 0 or freeze < 0:
        raise ValueError("native stop/freeze levels must be nonnegative")
    ratio = volume / step
    if not math.isclose(ratio, round(ratio), rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("native minimum volume is not aligned to volume step")

    native_floor = max(
        tick_size,
        point_value,
        (stops + 1) * point_value,
        (freeze + 1) * point_value,
    )
    candidates: list[float] = []
    distance = native_floor
    for _ in range(int(policy.maximum_geometry_probes)):
        ticks = max(1, math.ceil(distance / tick_size - 1e-12))
        aligned = ticks * tick_size
        if not candidates or aligned > candidates[-1] + tick_size * 1e-9:
            candidates.append(aligned)
        distance = aligned * float(policy.geometry_growth_factor)

    return CalibrationNativeEnvelope(
        symbol=str(symbol),
        equity=equity_value,
        minimum_volume=volume,
        volume_step=step,
        point=point_value,
        trade_tick_size=tick_size,
        stops_level_points=stops,
        freeze_level_points=freeze,
        loss_ceiling_cash=equity_value * float(policy.normal_risk_fraction),
        stop_distance_candidates=tuple(candidates),
        policy_fingerprint=planning_policy_fingerprint(policy),
    )
