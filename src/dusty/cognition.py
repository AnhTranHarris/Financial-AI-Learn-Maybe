from __future__ import annotations

import json
import math
from dataclasses import dataclass
from hashlib import sha256
from statistics import fmean
from typing import Mapping, Sequence

from .core import (
    AnalystState,
    Cognition,
    CoherenceResult,
    CoherenceState,
    GuardianState,
    HealthState,
    PatienceState,
    SkepticState,
)
from .experience import TradeSide
from .forecasting import Forecast
from .research import Scalar
from .risk import RiskAssessment, RiskState
from .runtime import CompiledStrategy


@dataclass(frozen=True, slots=True)
class CognitionPolicy:
    forecast_neutral_return: float = 0.0001
    max_spread_points_normal: float = 50.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.forecast_neutral_return) or self.forecast_neutral_return < 0:
            raise ValueError("forecast neutral return must be finite and nonnegative")
        if not math.isfinite(self.max_spread_points_normal) or self.max_spread_points_normal <= 0:
            raise ValueError("normal spread ceiling must be finite and positive")


@dataclass(frozen=True, slots=True)
class EntryCognitionRequest:
    strategy: CompiledStrategy
    features: tuple[tuple[str, Scalar], ...]
    coherence: CoherenceResult
    risk: RiskAssessment
    health: HealthState = HealthState.HEALTHY
    session: str = ""
    event_blocked: bool = False
    cooldown_remaining: int = 0
    spread_points: float = 0.0
    forecasts: tuple[Forecast, ...] = ()

    @classmethod
    def of(
        cls,
        *,
        strategy: CompiledStrategy,
        features: Mapping[str, Scalar],
        coherence: CoherenceResult,
        risk: RiskAssessment,
        health: HealthState = HealthState.HEALTHY,
        session: str = "",
        event_blocked: bool = False,
        cooldown_remaining: int = 0,
        spread_points: float = 0.0,
        forecasts: Sequence[Forecast] = (),
    ) -> "EntryCognitionRequest":
        return cls(strategy, tuple(sorted(features.items())), coherence, risk, health, session, event_blocked, cooldown_remaining, spread_points, tuple(forecasts))

    def __post_init__(self) -> None:
        if self.cooldown_remaining < 0:
            raise ValueError("cooldown remaining cannot be negative")
        if not math.isfinite(self.spread_points) or self.spread_points < 0:
            raise ValueError("spread points must be finite and nonnegative")

    def feature_map(self) -> dict[str, Scalar]:
        return dict(self.features)


@dataclass(frozen=True, slots=True)
class RoleJustification:
    role: str
    state: str
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CognitionAssessment:
    cognition: Cognition
    justifications: tuple[RoleJustification, ...]
    fingerprint: str

    def reasons_for(self, role: str) -> tuple[str, ...]:
        for item in self.justifications:
            if item.role == role:
                return item.reasons
        return ()


def _forecast_consensus(forecasts: Sequence[Forecast], threshold: float) -> tuple[int, float]:
    meaningful = [forecast.predicted_return for forecast in forecasts if abs(forecast.predicted_return) > threshold]
    if not meaningful:
        return 0, 0.0
    mean = fmean(meaningful)
    return (1 if mean > 0 else -1 if mean < 0 else 0), mean


def derive_entry_cognition(
    request: EntryCognitionRequest,
    policy: CognitionPolicy = CognitionPolicy(),
) -> CognitionAssessment:
    """Derive entry cognition from machine-observable evidence; roles are outputs, not caller inputs.

    Forecasts may confirm or challenge a strategy setup, but cannot create a setup when entry rules fail.
    Risk/health may reduce or veto authority, never manufacture directional conviction.
    """
    features = request.feature_map()
    pure_rule_match = request.strategy.spec.entry_matches(features)
    session_match = not request.strategy.spec.session_filters or request.session.upper() in {item.upper() for item in request.strategy.spec.session_filters}
    direction_sign = 1 if request.strategy.spec.direction is TradeSide.LONG else -1
    forecast_sign, forecast_mean = _forecast_consensus(request.forecasts, policy.forecast_neutral_return)
    forecast_conflict = pure_rule_match and forecast_sign != 0 and forecast_sign != direction_sign

    analyst_reasons: list[str] = []
    if not pure_rule_match:
        analyst = AnalystState.NEUTRAL
        analyst_reasons.append("entry_rules_not_met")
    elif forecast_conflict:
        analyst = AnalystState.UNCLEAR
        analyst_reasons.extend(("entry_rules_met", "forecast_consensus_conflicts"))
    else:
        analyst = AnalystState.LONG if request.strategy.spec.direction is TradeSide.LONG else AnalystState.SHORT
        analyst_reasons.append("entry_rules_met")
        if forecast_sign == direction_sign:
            analyst_reasons.append("forecast_consensus_confirms")
        elif forecast_sign == 0:
            analyst_reasons.append("forecast_not_directionally_material")

    skeptic_reasons: list[str] = []
    if request.coherence.state is CoherenceState.INCOHERENT:
        skeptic = SkepticState.INVALID
        skeptic_reasons.append("evidence_incoherent")
    elif request.coherence.state in {CoherenceState.INSUFFICIENT, CoherenceState.OVERLOADED}:
        skeptic = SkepticState.UNKNOWN
        skeptic_reasons.append(f"evidence_{request.coherence.state.value}")
    elif forecast_conflict or request.event_blocked or not session_match:
        skeptic = SkepticState.CONCERN
        if forecast_conflict:
            skeptic_reasons.append("forecast_conflict")
        if request.event_blocked:
            skeptic_reasons.append("event_exclusion_active")
        if not session_match:
            skeptic_reasons.append("session_not_allowed")
    else:
        skeptic = SkepticState.CLEAR
        skeptic_reasons.append("no_material_counterevidence")

    patience_reasons: list[str] = []
    if not pure_rule_match:
        patience = PatienceState.WAIT
        patience_reasons.append("wait_for_entry_rules")
    elif request.cooldown_remaining:
        patience = PatienceState.WAIT
        patience_reasons.append("cooldown_active")
    elif request.event_blocked:
        patience = PatienceState.WAIT
        patience_reasons.append("event_exclusion_active")
    elif not session_match:
        patience = PatienceState.WAIT
        patience_reasons.append("session_not_allowed")
    elif not request.risk.allowed:
        patience = PatienceState.WAIT
        patience_reasons.append("risk_not_approved")
    elif forecast_conflict:
        patience = PatienceState.WAIT
        patience_reasons.append("wait_for_forecast_conflict_resolution")
    else:
        patience = PatienceState.READY
        patience_reasons.append("setup_temporally_ready")

    guardian_reasons: list[str] = []
    if request.health is HealthState.FAILED or not request.risk.allowed or request.risk.state in {RiskState.RESEARCH_ONLY, RiskState.FAILED}:
        guardian = GuardianState.STOP
        if request.health is HealthState.FAILED:
            guardian_reasons.append("system_health_failed")
        if not request.risk.allowed:
            guardian_reasons.extend(f"risk:{reason}" for reason in request.risk.reasons or ("not_allowed",))
        if request.risk.state in {RiskState.RESEARCH_ONLY, RiskState.FAILED}:
            guardian_reasons.append(f"risk_state:{request.risk.state.value}")
    elif request.health is HealthState.DEGRADED or request.risk.state in {RiskState.CAUTION, RiskState.DEFENSIVE} or request.spread_points > policy.max_spread_points_normal:
        guardian = GuardianState.CAUTION
        if request.health is HealthState.DEGRADED:
            guardian_reasons.append("system_health_degraded")
        if request.risk.state in {RiskState.CAUTION, RiskState.DEFENSIVE}:
            guardian_reasons.append(f"risk_state:{request.risk.state.value}")
        if request.spread_points > policy.max_spread_points_normal:
            guardian_reasons.append("spread_above_normal_ceiling")
    else:
        guardian = GuardianState.NORMAL
        guardian_reasons.append("execution_and_risk_normal")

    cognition = Cognition(analyst, skeptic, patience, guardian)
    justifications = (
        RoleJustification("analyst", analyst.value, tuple(analyst_reasons)),
        RoleJustification("skeptic", skeptic.value, tuple(skeptic_reasons)),
        RoleJustification("patience", patience.value, tuple(patience_reasons)),
        RoleJustification("guardian", guardian.value, tuple(guardian_reasons)),
    )
    payload = {
        "strategy_hash": request.strategy.strategy_hash,
        "features": request.features,
        "coherence": (request.coherence.state.value, request.coherence.reasons),
        "risk": (request.risk.allowed, request.risk.state.value, request.risk.risk_multiplier, request.risk.reasons),
        "health": request.health.value,
        "session": request.session,
        "event_blocked": request.event_blocked,
        "cooldown_remaining": request.cooldown_remaining,
        "spread_points": request.spread_points,
        "forecasts": tuple((item.provider, item.at.isoformat(), item.horizon_steps, item.origin, item.point, item.lower, item.upper) for item in request.forecasts),
        "forecast_mean": forecast_mean,
        "cognition": (analyst.value, skeptic.value, patience.value, guardian.value),
        "reasons": tuple((item.role, item.reasons) for item in justifications),
    }
    fingerprint = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    return CognitionAssessment(cognition, justifications, fingerprint)
