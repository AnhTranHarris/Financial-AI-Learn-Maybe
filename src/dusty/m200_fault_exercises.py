from __future__ import annotations

"""Bounded M200 exercises of existing fail-closed production policies.

These helpers never claim that the external broker/provider actually failed.
They construct CONTROLLED_EXERCISE evidence only after the pre-existing M191,
M196.7 and market-clock boundaries demonstrate the expected safe behavior.
"""

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json

from .features import FeatureVector
from .long_running_soak import (
    SoakDisturbanceEvidence,
    SoakDisturbanceKind,
    SoakEvidenceMode,
    SoakRecoveryStatus,
)
from .market_clock import (
    BrokerMarketSchedule,
    MarketClockObservation,
    MarketClockState,
    SessionKind,
    SymbolTradeMode,
    WeeklySession,
    assess_market_clock,
)
from .multitimeframe_context import bind_multitimeframe_context
from .provider_degradation import (
    ProviderDegradationPolicy,
    ProviderHealthObservation,
    ProviderObservationOutcome,
    ProviderOperationalStatus,
    assess_provider_degradation,
)
from .strategy_reconstruction_campaign import (
    AssignmentBasis,
    TimeframeMode,
    TimeframeProfile,
)


def _digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("M200 exercise time must be timezone-aware")
    return value.astimezone(timezone.utc)


def exercise_provider_failure(
    *,
    at: datetime,
    provider_id: str,
    model_identity_fingerprint: str,
) -> SoakDisturbanceEvidence:
    """Prove M191 makes a failing optional model unavailable and then recovers."""

    at = _utc(at)
    policy = ProviderDegradationPolicy()
    failed = tuple(
        ProviderHealthObservation(
            provider_id=provider_id,
            model_identity_fingerprint=model_identity_fingerprint,
            observed_at=at + timedelta(seconds=index),
            outcome=ProviderObservationOutcome.TRANSIENT_FAILURE,
            evidence_fingerprint=_digest(("m200-provider-failure", index)),
            detail="m200_controlled_transient_failure",
        )
        for index in range(policy.unavailable_after_consecutive_failures)
    )
    unavailable = assess_provider_degradation(
        failed,
        provider_id=provider_id,
        model_identity_fingerprint=model_identity_fingerprint,
        policy=policy,
    )
    recovered_rows = failed + tuple(
        ProviderHealthObservation(
            provider_id=provider_id,
            model_identity_fingerprint=model_identity_fingerprint,
            observed_at=at + timedelta(seconds=100 + index),
            outcome=ProviderObservationOutcome.SUCCESS,
            evidence_fingerprint=_digest(("m200-provider-recovery", index)),
            detail="m200_controlled_recovery_success",
        )
        for index in range(policy.recovery_successes_required)
    )
    recovered = assess_provider_degradation(
        recovered_rows,
        provider_id=provider_id,
        model_identity_fingerprint=model_identity_fingerprint,
        policy=policy,
    )
    if unavailable.status is not ProviderOperationalStatus.UNAVAILABLE:
        raise RuntimeError("M191 did not fail closed under controlled provider failures")
    if unavailable.new_evidence_allowed or not unavailable.deterministic_core_operational:
        raise RuntimeError("M191 provider failure authority contract changed")
    if recovered.status is not ProviderOperationalStatus.HEALTHY:
        raise RuntimeError("M191 provider recovery contract failed")
    proof = _digest((unavailable.fingerprint, recovered.fingerprint))
    return SoakDisturbanceEvidence(
        SoakDisturbanceKind.PROVIDER_OR_MODEL_FAILURE,
        at,
        SoakRecoveryStatus.RECOVERED,
        proof,
        SoakEvidenceMode.CONTROLLED_EXERCISE,
    )


def exercise_data_gap(*, at: datetime) -> SoakDisturbanceEvidence:
    """Prove M196.7 refuses silent primary-timeframe backfill."""

    at = _utc(at)
    previous = at - timedelta(minutes=15)
    profile = TimeframeProfile(
        "M15",
        ("H1",),
        TimeframeMode.MULTI,
        AssignmentBasis.RESEARCH_EXPLORATION,
    )
    series = {
        "M15": (FeatureVector.of(previous, {"atr": 0.001}),),
        "H1": (FeatureVector.of(previous - timedelta(minutes=45), {"atr": 0.002}),),
    }
    try:
        bind_multitimeframe_context(profile, series, decision_at=at)
    except ValueError as exc:
        detail = str(exc)
        if "cannot be silently backfilled" not in detail:
            raise RuntimeError("M196.7 rejected the data gap for an unexpected reason") from exc
    else:
        raise RuntimeError("M196.7 accepted a missing primary decision bar")
    return SoakDisturbanceEvidence(
        SoakDisturbanceKind.DATA_GAP,
        at,
        SoakRecoveryStatus.SAFE_HALT,
        _digest(("m1967-primary-gap", profile.fingerprint, previous.isoformat(), at.isoformat())),
        SoakEvidenceMode.CONTROLLED_EXERCISE,
    )


def _exercise_schedule(at: datetime) -> BrokerMarketSchedule:
    at = _utc(at)
    # Exercise-only schedule. It exists to drive the already-certified market
    # clock state machine, never to represent Coinexx's actual sessions.
    next_day = (at.weekday() + 1) % 7
    return BrokerMarketSchedule(
        broker="M200-CONTROLLED-EXERCISE",
        server="M200-CONTROLLED-EXERCISE",
        symbol="EURUSD",
        captured_at=at - timedelta(minutes=1),
        server_utc_offset_seconds=0,
        sessions=(WeeklySession(SessionKind.TRADE, next_day, 0, 0, 3600),),
    )


def exercise_market_closure(*, at: datetime) -> SoakDisturbanceEvidence:
    """Prove the market clock suppresses entries during scheduled closure."""

    at = _utc(at)
    assessment = assess_market_clock(
        _exercise_schedule(at),
        MarketClockObservation(at, None, SymbolTradeMode.FULL),
    )
    if assessment.state is not MarketClockState.SCHEDULED_CLOSED:
        raise RuntimeError("market-clock closure exercise did not classify scheduled closure")
    if assessment.new_entries_authorized or not assessment.position_supervision_required:
        raise RuntimeError("market-clock closure exercise violated safe supervision semantics")
    return SoakDisturbanceEvidence(
        SoakDisturbanceKind.MARKET_CLOSURE,
        at,
        SoakRecoveryStatus.SAFE_HALT,
        _digest(("market-closure", assessment.state.value, assessment.reasons)),
        SoakEvidenceMode.CONTROLLED_EXERCISE,
    )


def exercise_abnormal_broker_condition(*, at: datetime) -> SoakDisturbanceEvidence:
    """Prove the market clock halts entries on an impossible open/disabled state."""

    at = _utc(at)
    weekday = at.weekday()
    seconds = at.hour * 3600 + at.minute * 60 + at.second
    start = max(0, seconds - 60)
    end = min(86_400, seconds + 3600)
    if end <= start:
        end = 86_400
    schedule = BrokerMarketSchedule(
        broker="M200-CONTROLLED-EXERCISE",
        server="M200-CONTROLLED-EXERCISE",
        symbol="EURUSD",
        captured_at=at - timedelta(minutes=1),
        server_utc_offset_seconds=0,
        sessions=(WeeklySession(SessionKind.TRADE, weekday, 0, start, end),),
    )
    observation = MarketClockObservation(
        observed_at=at,
        last_tick_at=at,
        trade_mode=SymbolTradeMode.DISABLED,
    )
    assessment = assess_market_clock(schedule, observation)
    if assessment.state is not MarketClockState.HALTED or assessment.new_entries_authorized:
        raise RuntimeError("market-clock abnormal broker exercise did not halt")
    return SoakDisturbanceEvidence(
        SoakDisturbanceKind.ABNORMAL_BROKER_CONDITION,
        at,
        SoakRecoveryStatus.SAFE_HALT,
        _digest(("abnormal-broker-condition", assessment.state.value, assessment.reasons)),
        SoakEvidenceMode.CONTROLLED_EXERCISE,
    )
