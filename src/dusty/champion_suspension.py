from __future__ import annotations

"""M193 automatic Frozen Champion suspension governance.

M193 converts precommitted operational evidence into one irreversible M185
lifecycle transition.  It never closes or modifies positions, sends broker
requests, changes risk, or mutates the Frozen Champion payload.  Existing
position supervision remains the responsibility of the established execution
and Guardian layers.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .champion_registry import (
    ChampionLifecycleEvent,
    ChampionLifecycleEventType,
    ChampionLifecycleState,
    FrozenChampionRecord,
    FrozenChampionRegistry,
)
from .strategy_drift import StrategyDriftAssessment, StrategyDriftBaseline, StrategyDriftStatus


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _unit(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{label} must be finite and in [0,1]")
    return rendered


def _positive_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 1:
        raise ValueError(f"{label} must be a positive integer")
    return int(value)


def _text(value: str, label: str, *, maximum: int = 512) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


class ChampionSuspensionDecision(StrEnum):
    KEEP_ACTIVE = "keep_active"
    SUSPEND = "suspend"


@dataclass(frozen=True, slots=True)
class ChampionSuspensionPolicy:
    maximum_forward_drawdown_fraction: float
    minimum_drawdown_observations: int
    suspend_on_structural_drift: bool
    suspend_on_data_or_replay_drift: bool
    suspend_on_governance_failure: bool
    suspend_on_execution_drift_only: bool
    minimum_execution_confirmation_count: int

    def __post_init__(self) -> None:
        drawdown = _unit(self.maximum_forward_drawdown_fraction, "maximum_forward_drawdown_fraction")
        if drawdown <= 0:
            raise ValueError("maximum_forward_drawdown_fraction must be positive")
        object.__setattr__(self, "maximum_forward_drawdown_fraction", drawdown)
        object.__setattr__(
            self,
            "minimum_drawdown_observations",
            _positive_int(self.minimum_drawdown_observations, "minimum_drawdown_observations"),
        )
        object.__setattr__(
            self,
            "minimum_execution_confirmation_count",
            _positive_int(self.minimum_execution_confirmation_count, "minimum_execution_confirmation_count"),
        )
        for name in (
            "suspend_on_structural_drift",
            "suspend_on_data_or_replay_drift",
            "suspend_on_governance_failure",
            "suspend_on_execution_drift_only",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m193-champion-suspension-policy-v1",
            self.maximum_forward_drawdown_fraction,
            self.minimum_drawdown_observations,
            self.suspend_on_structural_drift,
            self.suspend_on_data_or_replay_drift,
            self.suspend_on_governance_failure,
            self.suspend_on_execution_drift_only,
            self.minimum_execution_confirmation_count,
        ))


@dataclass(frozen=True, slots=True)
class ForwardDrawdownEvidence:
    champion_fingerprint: str
    baseline_fingerprint: str
    period_start: datetime
    period_end: datetime
    observation_count: int
    maximum_drawdown_fraction: float
    data_integrity_ok: bool
    source_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "champion_fingerprint", _sha(self.champion_fingerprint, "drawdown Champion"))
        object.__setattr__(self, "baseline_fingerprint", _sha(self.baseline_fingerprint, "drawdown baseline"))
        object.__setattr__(self, "period_start", _aware(self.period_start, "drawdown period_start"))
        object.__setattr__(self, "period_end", _aware(self.period_end, "drawdown period_end"))
        if self.period_end <= self.period_start:
            raise ValueError("drawdown evidence period_end must be after period_start")
        object.__setattr__(self, "observation_count", _positive_int(self.observation_count, "drawdown observation_count"))
        object.__setattr__(self, "maximum_drawdown_fraction", _unit(self.maximum_drawdown_fraction, "maximum_drawdown_fraction"))
        if not isinstance(self.data_integrity_ok, bool):
            raise ValueError("drawdown data_integrity_ok must be boolean")
        sources = tuple(sorted(_sha(value, "drawdown source") for value in self.source_fingerprints))
        if not sources or len(sources) != len(set(sources)):
            raise ValueError("drawdown evidence sources must be unique and nonempty")
        object.__setattr__(self, "source_fingerprints", sources)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m193-forward-drawdown-evidence-v1",
            self.champion_fingerprint,
            self.baseline_fingerprint,
            self.period_start.isoformat(),
            self.period_end.isoformat(),
            self.observation_count,
            self.maximum_drawdown_fraction,
            self.data_integrity_ok,
            self.source_fingerprints,
        ))


@dataclass(frozen=True, slots=True)
class ChampionSuspensionAssessment:
    decision: ChampionSuspensionDecision
    champion_fingerprint: str
    baseline_fingerprint: str
    drift_fingerprint: str
    drawdown_fingerprint: str
    policy_fingerprint: str
    assessed_at: datetime
    reasons: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        for field, label in (
            ("champion_fingerprint", "suspension Champion"),
            ("baseline_fingerprint", "suspension baseline"),
            ("drift_fingerprint", "suspension drift"),
            ("drawdown_fingerprint", "suspension drawdown"),
            ("policy_fingerprint", "suspension policy"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "assessed_at", _aware(self.assessed_at, "suspension assessed_at"))
        reasons = tuple(sorted({_text(value, "suspension reason") for value in self.reasons}))
        if self.decision is ChampionSuspensionDecision.SUSPEND and not reasons:
            raise ValueError("suspension decision requires at least one reason")
        object.__setattr__(self, "reasons", reasons)
        evidence = tuple(sorted(_sha(value, "suspension evidence") for value in self.evidence_fingerprints))
        if not evidence or len(evidence) != len(set(evidence)):
            raise ValueError("suspension assessment evidence must be unique and nonempty")
        object.__setattr__(self, "evidence_fingerprints", evidence)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m193-champion-suspension-assessment-v1",
            self.decision.value,
            self.champion_fingerprint,
            self.baseline_fingerprint,
            self.drift_fingerprint,
            self.drawdown_fingerprint,
            self.policy_fingerprint,
            self.assessed_at.isoformat(),
            self.reasons,
            self.evidence_fingerprints,
        ))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def position_mutation_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


def evaluate_automatic_suspension(
    champion: FrozenChampionRecord,
    baseline: StrategyDriftBaseline,
    drift: StrategyDriftAssessment,
    drawdown: ForwardDrawdownEvidence,
    *,
    execution_confirmation_fingerprints: Iterable[str],
    policy: ChampionSuspensionPolicy,
    assessed_at: datetime,
) -> ChampionSuspensionAssessment:
    """Evaluate only precommitted suspension policy; perform no registry mutation."""

    now = _aware(assessed_at, "suspension assessed_at")
    if baseline.champion_fingerprint != champion.fingerprint:
        raise ValueError("Champion/baseline identity drift")
    if drift.champion_fingerprint != champion.fingerprint:
        raise ValueError("Champion/drift identity drift")
    if drift.baseline_fingerprint != baseline.fingerprint:
        raise ValueError("drift assessment is not bound to supplied baseline")
    if drawdown.champion_fingerprint != champion.fingerprint:
        raise ValueError("Champion/drawdown identity drift")
    if drawdown.baseline_fingerprint != baseline.fingerprint:
        raise ValueError("drawdown evidence is not bound to supplied baseline")
    if drawdown.period_start <= baseline.period_end:
        raise ValueError("drawdown evidence must begin strictly after certified baseline")
    if now < drawdown.period_end:
        raise ValueError("suspension assessment cannot predate drawdown evidence")

    confirmations = tuple(sorted(_sha(value, "execution confirmation") for value in execution_confirmation_fingerprints))
    if len(confirmations) != len(set(confirmations)):
        raise ValueError("execution confirmation evidence must be unique")

    reasons: list[str] = []
    if not drawdown.data_integrity_ok:
        reasons.append("forward_drawdown_data_integrity_failure")
    if (
        drawdown.observation_count >= policy.minimum_drawdown_observations
        and drawdown.maximum_drawdown_fraction > policy.maximum_forward_drawdown_fraction
    ):
        reasons.append("forward_drawdown_exceeded")

    if drift.status is StrategyDriftStatus.STRUCTURAL_DRIFT and policy.suspend_on_structural_drift:
        reasons.append("structural_strategy_drift")
    elif drift.status is StrategyDriftStatus.DATA_OR_REPLAY_DRIFT and policy.suspend_on_data_or_replay_drift:
        reasons.append("data_or_PIT_replay_integrity_failure")
    elif drift.status is StrategyDriftStatus.GOVERNANCE_FAILURE and policy.suspend_on_governance_failure:
        reasons.append("forward_governance_failure")
    elif drift.status is StrategyDriftStatus.EXECUTION_DRIFT_ONLY and policy.suspend_on_execution_drift_only:
        if len(confirmations) >= policy.minimum_execution_confirmation_count:
            reasons.append("confirmed_execution_mismatch")

    evidence = {
        baseline.fingerprint,
        drift.fingerprint,
        drawdown.fingerprint,
        policy.fingerprint,
        *baseline.evidence_fingerprints,
        *drift.evidence_fingerprints,
        *drawdown.source_fingerprints,
        *confirmations,
    }
    decision = ChampionSuspensionDecision.SUSPEND if reasons else ChampionSuspensionDecision.KEEP_ACTIVE
    return ChampionSuspensionAssessment(
        decision,
        champion.fingerprint,
        baseline.fingerprint,
        drift.fingerprint,
        drawdown.fingerprint,
        policy.fingerprint,
        now,
        tuple(reasons),
        tuple(evidence),
    )


def apply_automatic_suspension(
    registry: FrozenChampionRegistry,
    champion: FrozenChampionRecord,
    assessment: ChampionSuspensionAssessment,
    *,
    actor_fingerprint: str,
) -> ChampionLifecycleEvent:
    """Append the M185 SUSPENDED event; never touch broker or position state."""

    if assessment.decision is not ChampionSuspensionDecision.SUSPEND:
        raise ValueError("KEEP_ACTIVE assessment cannot suspend a Champion")
    if assessment.champion_fingerprint != champion.fingerprint:
        raise ValueError("Champion/suspension assessment identity drift")
    stored = registry.get(champion.fingerprint)
    if stored is None or stored != champion:
        raise ValueError("exact Frozen Champion is not registered")
    state = registry.state(champion.fingerprint)
    latest = registry.latest_event(champion.fingerprint)
    if latest is None:
        raise ValueError("registered Champion is missing lifecycle state")
    if state is ChampionLifecycleState.SUSPENDED:
        if latest.event_type is not ChampionLifecycleEventType.SUSPENDED:
            raise ValueError("Champion lifecycle state/event inconsistency")
        return latest
    if state is not ChampionLifecycleState.ACTIVE:
        raise ValueError(f"only ACTIVE Champion may be automatically suspended; found {state.value}")
    if assessment.assessed_at < latest.created_at:
        raise ValueError("suspension assessment predates current Champion lifecycle")

    event = ChampionLifecycleEvent(
        champion.fingerprint,
        ChampionLifecycleEventType.SUSPENDED,
        _sha(actor_fingerprint, "suspension actor"),
        tuple(sorted({assessment.fingerprint, *assessment.evidence_fingerprints})),
        "M193 automatic suspension: " + ", ".join(assessment.reasons),
        assessment.assessed_at,
    )
    return registry.append_lifecycle_event(event)
