from __future__ import annotations

"""M174 fail-closed robustness certification gate.

This gate may classify a strategy as a serious research challenger only. It has
no Champion-promotion, broker-write, sizing, entry-veto, or risk authority.
Numeric risk/decay limits are supplied by an explicit policy; Dusty does not
invent universal financial thresholds inside the gate.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math

from .broker_calibration import BrokerEconomicsCalibration, CalibrationStatus
from .cost_torture import CostTortureAssessment
from .forward_decay import HistoricalForwardDecay, DecayStatus
from .parameter_stability import NeighborhoodAssessment, NeighborhoodStatus
from .regime_torture import RegimeTortureAssessment, RegimeTortureStatus
from .strategy_dependency import StrategyDependencyMatrix, DependencyStatus
from .tail_risk import TailRiskReport, TailRiskStatus
from .walk_forward_lab import WalkForwardSummary


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode()).hexdigest()


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


class RobustnessGateStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    SERIOUS_CHALLENGER = "serious_challenger"


@dataclass(frozen=True, slots=True)
class RobustnessCertificationPolicy:
    minimum_walk_forward_pass_fraction: float
    minimum_forward_retention_ratio: float
    maximum_tail_drawdown: float
    maximum_cvar: float

    def __post_init__(self) -> None:
        for name in ("minimum_walk_forward_pass_fraction", "minimum_forward_retention_ratio"):
            value = _finite(getattr(self, name), name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be in [0,1]")
        for name in ("maximum_tail_drawdown", "maximum_cvar"):
            value = _finite(getattr(self, name), name)
            if value < 0:
                raise ValueError(f"{name} must be nonnegative")


@dataclass(frozen=True, slots=True)
class RobustnessCertification:
    status: RobustnessGateStatus
    checks: tuple[tuple[str, str], ...]
    blockers: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest({"protocol":"dusty-m174-robustness-v1","status":self.status.value,"checks":self.checks,"blockers":self.blockers,"evidence":self.evidence_fingerprints})

    @property
    def broker_write_authority(self) -> bool: return False
    @property
    def promotion_authority(self) -> bool: return False
    @property
    def entry_veto_authority(self) -> bool: return False
    @property
    def risk_override_authority(self) -> bool: return False


def certify_robustness(*, calibration: BrokerEconomicsCalibration, walk_forward: WalkForwardSummary,
    neighborhood: NeighborhoodAssessment, regime: RegimeTortureAssessment, cost: CostTortureAssessment,
    decay: HistoricalForwardDecay, tail: TailRiskReport, dependency: StrategyDependencyMatrix,
    policy: RobustnessCertificationPolicy) -> RobustnessCertification:
    checks: list[tuple[str,str]] = []
    blockers: list[str] = []
    pending = False
    def add(name: str, state: str, ok: bool | None) -> None:
        nonlocal pending
        checks.append((name,state))
        if ok is None: pending = True; blockers.append(name)
        elif not ok: blockers.append(name)

    add("broker_calibration", calibration.status.value, True if calibration.status is CalibrationStatus.CALIBRATED else None)
    add("walk_forward", f"pass_fraction={walk_forward.pass_fraction:.12g}", walk_forward.pass_fraction >= policy.minimum_walk_forward_pass_fraction)
    add("parameter_neighborhood", neighborhood.status.value, True if neighborhood.status is NeighborhoodStatus.STABLE else (None if neighborhood.status is NeighborhoodStatus.INSUFFICIENT else False))
    add("regime_torture", regime.status.value, True if regime.status is RegimeTortureStatus.PASSED else (None if regime.status is RegimeTortureStatus.INSUFFICIENT else False))
    add("cost_torture", f"passed={cost.passed}", cost.passed)
    if decay.status is DecayStatus.MEASURED:
        assert decay.retention_ratio is not None
        add("historical_forward_decay", f"retention={decay.retention_ratio:.12g}", decay.retention_ratio >= policy.minimum_forward_retention_ratio)
    else:
        add("historical_forward_decay", decay.status.value, None)
    if tail.status is TailRiskStatus.MEASURED:
        assert tail.max_drawdown is not None and tail.conditional_value_at_risk is not None
        add("tail_risk", f"dd={tail.max_drawdown:.12g};cvar={tail.conditional_value_at_risk:.12g}", tail.max_drawdown <= policy.maximum_tail_drawdown and tail.conditional_value_at_risk <= policy.maximum_cvar)
    else:
        add("tail_risk", tail.status.value, None)
    add("strategy_dependency", dependency.status.value, True if dependency.status is DependencyStatus.DIVERSIFIED else (None if dependency.status is DependencyStatus.INSUFFICIENT else False))
    status = RobustnessGateStatus.PENDING if pending else (RobustnessGateStatus.REJECTED if blockers else RobustnessGateStatus.SERIOUS_CHALLENGER)
    evidence = tuple(sorted({calibration.fingerprint, walk_forward.plan_fingerprint, neighborhood.fingerprint, regime.fingerprint, cost.calibration_fingerprint, decay.fingerprint, tail.fingerprint, dependency.fingerprint}))
    return RobustnessCertification(status, tuple(checks), tuple(blockers), evidence)
