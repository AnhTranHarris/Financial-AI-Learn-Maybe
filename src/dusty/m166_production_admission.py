from __future__ import annotations

"""Production admission boundary from M165 broker calibration into M166 walk-forward.

This wrapper does not alter the deterministic walk-forward laboratory.  It binds an
existing WalkForwardPlan to one exact, genuinely CALIBRATED M165 broker profile so
production M166 cannot start from fixture/synthetic/insufficient broker economics.
It grants no broker-write, live, promotion, retry, sizing, or risk authority.
"""

from dataclasses import dataclass
from hashlib import sha256
import json

from .broker_calibration import BrokerEconomicsCalibration, CalibrationStatus
from .walk_forward_lab import WalkForwardPlan


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha256(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


@dataclass(frozen=True, slots=True)
class M166ProductionAdmission:
    calibration_fingerprint: str
    broker_profile_fingerprint: str
    symbol: str
    observation_count: int
    distinct_days: int
    walk_forward_plan_fingerprint: str

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    promotion_authority = False
    risk_override_authority = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "calibration_fingerprint", _sha256(self.calibration_fingerprint, "M165 calibration"))
        object.__setattr__(self, "broker_profile_fingerprint", _sha256(self.broker_profile_fingerprint, "broker profile"))
        object.__setattr__(self, "walk_forward_plan_fingerprint", _sha256(self.walk_forward_plan_fingerprint, "walk-forward plan"))
        symbol = str(self.symbol).strip().upper()
        if not symbol:
            raise ValueError("M166 admission symbol required")
        object.__setattr__(self, "symbol", symbol)
        if int(self.observation_count) < 30:
            raise ValueError("M166 production admission requires at least 30 broker observations")
        if int(self.distinct_days) < 3:
            raise ValueError("M166 production admission requires at least 3 distinct broker-evidence days")

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m166-production-admission-v1",
            "m165_calibration_fingerprint": self.calibration_fingerprint,
            "broker_profile_fingerprint": self.broker_profile_fingerprint,
            "symbol": self.symbol,
            "observation_count": self.observation_count,
            "distinct_days": self.distinct_days,
            "walk_forward_plan_fingerprint": self.walk_forward_plan_fingerprint,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "retry": False,
                "promotion": False,
                "risk_override": False,
            },
        }


def admit_production_walk_forward(
    *,
    calibration: BrokerEconomicsCalibration,
    plan: WalkForwardPlan,
    expected_symbol: str,
) -> M166ProductionAdmission:
    symbol = str(expected_symbol).strip().upper()
    if not symbol:
        raise ValueError("expected M166 symbol required")
    if calibration.status is not CalibrationStatus.CALIBRATED:
        raise PermissionError("M166 production walk-forward requires CALIBRATED M165 broker economics")
    if calibration.symbol != symbol:
        raise ValueError("M165 calibration symbol does not match M166 production symbol")
    if calibration.observation_count < 30 or calibration.distinct_days < 3:
        raise PermissionError("M165 calibration does not satisfy production breadth requirements")
    return M166ProductionAdmission(
        calibration_fingerprint=calibration.fingerprint,
        broker_profile_fingerprint=calibration.broker_profile_fingerprint,
        symbol=symbol,
        observation_count=calibration.observation_count,
        distinct_days=calibration.distinct_days,
        walk_forward_plan_fingerprint=plan.fingerprint,
    )
