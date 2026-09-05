from __future__ import annotations

"""M170 calibrated transaction-cost and slippage torture contracts."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .broker_calibration import BrokerEconomicsCalibration, CalibrationStatus


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _finite_nonnegative(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return rendered


class CostStressLevel(StrEnum):
    P50 = "observed_p50"
    P95 = "observed_p95"
    P99 = "observed_p99"
    EXTREME = "p99_explicit_multiplier"


@dataclass(frozen=True, slots=True)
class CostStressPolicy:
    extreme_multiplier: float = 1.25
    minimum_pass_fraction: float = 1.0

    def __post_init__(self) -> None:
        multiplier = float(self.extreme_multiplier)
        if not math.isfinite(multiplier) or multiplier < 1.0 or multiplier > 10.0:
            raise ValueError("extreme_multiplier must be finite in [1,10]")
        fraction = float(self.minimum_pass_fraction)
        if not math.isfinite(fraction) or not 0.0 <= fraction <= 1.0:
            raise ValueError("minimum_pass_fraction must be in [0,1]")


@dataclass(frozen=True, slots=True)
class CostStressScenario:
    calibration_fingerprint: str
    level: CostStressLevel
    spread_points: float
    adverse_slippage_points: float
    commission_fee_per_lot: float
    absolute_swap_per_lot: float
    explicit_multiplier: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "calibration_fingerprint", _sha(self.calibration_fingerprint, "cost calibration"))
        for name in ("spread_points", "adverse_slippage_points", "commission_fee_per_lot", "absolute_swap_per_lot"):
            object.__setattr__(self, name, _finite_nonnegative(getattr(self, name), name))
        multiplier = float(self.explicit_multiplier)
        if not math.isfinite(multiplier) or multiplier < 1.0:
            raise ValueError("cost stress explicit multiplier must be >=1")
        object.__setattr__(self, "explicit_multiplier", multiplier)

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m170-cost-stress-v1",
                "calibration": self.calibration_fingerprint,
                "level": self.level.value,
                "spread_points": self.spread_points,
                "adverse_slippage_points": self.adverse_slippage_points,
                "commission_fee_per_lot": self.commission_fee_per_lot,
                "absolute_swap_per_lot": self.absolute_swap_per_lot,
                "explicit_multiplier": self.explicit_multiplier,
            }
        )


@dataclass(frozen=True, slots=True)
class CostStressResult:
    scenario_fingerprint: str
    net_return: float
    max_drawdown: float
    passed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario_fingerprint", _sha(self.scenario_fingerprint, "cost scenario result"))
        for name in ("net_return", "max_drawdown"):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if name == "max_drawdown" and value < 0:
                raise ValueError("max_drawdown cannot be negative")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class CostTortureAssessment:
    calibration_fingerprint: str
    scenario_count: int
    pass_fraction: float
    passed: bool
    worst_net_return: float
    worst_max_drawdown: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "calibration_fingerprint", _sha(self.calibration_fingerprint, "cost assessment calibration"))

    @property
    def broker_write_authority(self) -> bool:
        return False


def build_cost_stress_scenarios(
    calibration: BrokerEconomicsCalibration,
    *,
    policy: CostStressPolicy = CostStressPolicy(),
) -> tuple[CostStressScenario, ...]:
    if calibration.status is not CalibrationStatus.CALIBRATED:
        raise ValueError("M170 calibrated cost stress requires CALIBRATED M165 evidence")
    assert calibration.spread_p50_points is not None
    assert calibration.spread_p95_points is not None
    assert calibration.spread_p99_points is not None
    assert calibration.adverse_slippage_p50_points is not None
    assert calibration.adverse_slippage_p95_points is not None
    assert calibration.adverse_slippage_p99_points is not None
    assert calibration.commission_fee_p50_per_lot is not None
    assert calibration.commission_fee_p95_per_lot is not None
    assert calibration.absolute_swap_p95_per_lot is not None
    fp = calibration.fingerprint
    p50 = CostStressScenario(
        fp, CostStressLevel.P50,
        calibration.spread_p50_points,
        calibration.adverse_slippage_p50_points,
        calibration.commission_fee_p50_per_lot,
        calibration.absolute_swap_p95_per_lot,
        1.0,
    )
    p95 = CostStressScenario(
        fp, CostStressLevel.P95,
        calibration.spread_p95_points,
        calibration.adverse_slippage_p95_points,
        calibration.commission_fee_p95_per_lot,
        calibration.absolute_swap_p95_per_lot,
        1.0,
    )
    p99 = CostStressScenario(
        fp, CostStressLevel.P99,
        calibration.spread_p99_points,
        calibration.adverse_slippage_p99_points,
        calibration.commission_fee_p95_per_lot,
        calibration.absolute_swap_p95_per_lot,
        1.0,
    )
    extreme = CostStressScenario(
        fp, CostStressLevel.EXTREME,
        calibration.spread_p99_points * policy.extreme_multiplier,
        calibration.adverse_slippage_p99_points * policy.extreme_multiplier,
        calibration.commission_fee_p95_per_lot * policy.extreme_multiplier,
        calibration.absolute_swap_p95_per_lot * policy.extreme_multiplier,
        policy.extreme_multiplier,
    )
    return (p50, p95, p99, extreme)


def assess_cost_torture(
    calibration: BrokerEconomicsCalibration,
    scenarios: Iterable[CostStressScenario],
    results: Iterable[CostStressResult],
    *,
    policy: CostStressPolicy = CostStressPolicy(),
) -> CostTortureAssessment:
    expected = tuple(scenarios)
    if not expected:
        raise ValueError("cost torture requires scenarios")
    if calibration.status is not CalibrationStatus.CALIBRATED:
        raise ValueError("cost torture cannot assess uncalibrated broker economics")
    if any(row.calibration_fingerprint != calibration.fingerprint for row in expected):
        raise ValueError("cost stress scenario/calibration identity drift")
    scenario_fps = tuple(row.fingerprint for row in expected)
    if len(scenario_fps) != len(set(scenario_fps)):
        raise ValueError("cost stress scenarios must be unique")
    rows = tuple(results)
    by_fp = {row.scenario_fingerprint: row for row in rows}
    if len(by_fp) != len(rows) or set(by_fp) != set(scenario_fps):
        raise ValueError("cost torture requires exactly one result per scenario")
    ordered = tuple(by_fp[fp] for fp in scenario_fps)
    pass_fraction = sum(1 for row in ordered if row.passed) / len(ordered)
    return CostTortureAssessment(
        calibration.fingerprint,
        len(ordered),
        pass_fraction,
        pass_fraction >= policy.minimum_pass_fraction,
        min(row.net_return for row in ordered),
        max(row.max_drawdown for row in ordered),
    )
