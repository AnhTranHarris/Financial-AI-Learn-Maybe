from __future__ import annotations

"""M186 no-send Shadow Execution Mode.

M186 freezes a governance-approved OrderIntent for the currently ACTIVE M185
Champion and evaluates it only against immutable market quotes. This module
intentionally imports no MT5 execution adapter and exposes no send, retry,
cancel, position-mutation, promotion, or risk-override surface.

Actual broker orders/deals/fills belong to M187/M188, not M186.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .artifact_vault import ArtifactKind, ResearchArtifactRecord, ResearchArtifactVault
from .champion_registry import ChampionLifecycleState, FrozenChampionRecord, FrozenChampionRegistry
from .cognition import CognitionAssessment
from .core import AnalystState, GuardianState, PatienceState, SkepticState
from .experience import TradeSide
from .order_intent import OrderIntent
from .strategy_v3 import OrderStyle


SHADOW_INTENT_CONTENT_TYPE = "application/vnd.dusty.m186-shadow-intent+json"
SHADOW_ASSESSMENT_CONTENT_TYPE = "application/vnd.dusty.m186-shadow-assessment+json"


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


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


def _text(value: str, label: str, *, maximum: int = 256) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _validate_cognition(assessment: CognitionAssessment) -> None:
    _sha(assessment.fingerprint, "cognition")
    expected = {
        "analyst": assessment.cognition.analyst.value,
        "skeptic": assessment.cognition.skeptic.value,
        "patience": assessment.cognition.patience.value,
        "guardian": assessment.cognition.guardian.value,
    }
    observed: dict[str, str] = {}
    for row in assessment.justifications:
        role = _text(row.role, "cognition role", maximum=32).lower()
        state = _text(row.state, "cognition state", maximum=32).lower()
        if role in observed:
            raise ValueError("duplicate cognition role justification")
        for reason in row.reasons:
            _text(reason, "cognition reason", maximum=256)
        observed[role] = state
    if observed != expected:
        raise ValueError("cognition justification/state identity drift")


@dataclass(frozen=True, slots=True)
class ShadowCapturePolicy:
    maximum_quote_age_ms: float

    def __post_init__(self) -> None:
        value = _finite(self.maximum_quote_age_ms, "maximum_quote_age_ms")
        if value < 0:
            raise ValueError("maximum_quote_age_ms must be nonnegative")
        object.__setattr__(self, "maximum_quote_age_ms", value)

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m186-capture-policy-v1", self.maximum_quote_age_ms))


@dataclass(frozen=True, slots=True)
class ShadowMarketQuote:
    symbol: str
    observed_at: datetime
    bid: float
    ask: float
    source_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", _text(self.symbol, "quote symbol", maximum=64).upper())
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "quote observed_at"))
        object.__setattr__(self, "bid", _finite(self.bid, "quote bid"))
        object.__setattr__(self, "ask", _finite(self.ask, "quote ask"))
        object.__setattr__(self, "source_fingerprint", _sha(self.source_fingerprint, "quote source"))
        if self.bid <= 0 or self.ask <= 0 or self.ask < self.bid:
            raise ValueError("quote requires positive bid <= ask")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "observed_at": self.observed_at.isoformat(),
            "bid": self.bid,
            "ask": self.ask,
            "source_fingerprint": self.source_fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m186-market-quote-v1", self.payload))


@dataclass(frozen=True, slots=True)
class ShadowQuoteWindow:
    symbol: str
    coverage_start: datetime
    coverage_end: datetime
    complete: bool
    source_fingerprint: str
    quotes: tuple[ShadowMarketQuote, ...]

    def __post_init__(self) -> None:
        symbol = _text(self.symbol, "quote-window symbol", maximum=64).upper()
        start = _aware(self.coverage_start, "quote-window coverage_start")
        end = _aware(self.coverage_end, "quote-window coverage_end")
        if end < start:
            raise ValueError("quote-window coverage_end cannot precede coverage_start")
        source = _sha(self.source_fingerprint, "quote-window source")
        rows = tuple(sorted(self.quotes, key=lambda row: (row.observed_at, row.fingerprint)))
        fingerprints = tuple(row.fingerprint for row in rows)
        if len(fingerprints) != len(set(fingerprints)):
            raise ValueError("quote-window contains duplicate quote evidence")
        for row in rows:
            if row.symbol != symbol:
                raise ValueError("quote-window symbol drift")
            if not start <= row.observed_at <= end:
                raise ValueError("quote lies outside declared observation coverage")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "coverage_start", start)
        object.__setattr__(self, "coverage_end", end)
        object.__setattr__(self, "source_fingerprint", source)
        object.__setattr__(self, "quotes", rows)

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m186-quote-window-v1",
                self.symbol,
                self.coverage_start.isoformat(),
                self.coverage_end.isoformat(),
                self.complete,
                self.source_fingerprint,
                tuple(row.fingerprint for row in self.quotes),
            )
        )


@dataclass(frozen=True, slots=True)
class ShadowExecutionIntent:
    champion_fingerprint: str
    champion_deployment_fingerprint: str
    lane_id: str
    intent_hash: str
    client_tag: str
    strategy_fingerprint: str
    session_fingerprint: str
    cognition_fingerprint: str
    analyst_state: AnalystState
    skeptic_state: SkepticState
    patience_state: PatienceState
    guardian_state: GuardianState
    symbol: str
    side: TradeSide
    order_style: OrderStyle
    volume: float
    reference_price: float
    stop_price: float
    target_price: float | None
    stop_limit_price: float | None
    approved_risk_fraction: float
    allowed_loss: float
    max_price_drift_fraction: float
    captured_at: datetime
    intent_expires_at: datetime
    pending_expires_at: datetime | None
    capture_quote: ShadowMarketQuote
    capture_policy_fingerprint: str

    def __post_init__(self) -> None:
        for field, label in (
            ("champion_fingerprint", "shadow Champion"),
            ("champion_deployment_fingerprint", "shadow Champion deployment"),
            ("intent_hash", "shadow intent"),
            ("strategy_fingerprint", "shadow strategy"),
            ("session_fingerprint", "shadow session"),
            ("cognition_fingerprint", "shadow cognition"),
            ("capture_policy_fingerprint", "shadow capture policy"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "lane_id", _text(self.lane_id, "shadow lane", maximum=128).lower())
        object.__setattr__(self, "client_tag", _text(self.client_tag, "shadow client_tag", maximum=64))
        object.__setattr__(self, "symbol", _text(self.symbol, "shadow symbol", maximum=64).upper())
        for field in (
            "volume",
            "reference_price",
            "stop_price",
            "approved_risk_fraction",
            "allowed_loss",
            "max_price_drift_fraction",
        ):
            object.__setattr__(self, field, _finite(getattr(self, field), field))
        for field in ("target_price", "stop_limit_price"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _finite(value, field))
        object.__setattr__(self, "captured_at", _aware(self.captured_at, "shadow captured_at"))
        object.__setattr__(self, "intent_expires_at", _aware(self.intent_expires_at, "shadow intent expiry"))
        if self.pending_expires_at is not None:
            object.__setattr__(self, "pending_expires_at", _aware(self.pending_expires_at, "shadow pending expiry"))
        if self.volume <= 0 or self.reference_price <= 0 or self.stop_price <= 0 or self.allowed_loss <= 0:
            raise ValueError("shadow intent economics must be positive")
        if not 0 < self.approved_risk_fraction <= 1 or not 0 <= self.max_price_drift_fraction < 1:
            raise ValueError("shadow intent risk/drift controls are invalid")
        if self.captured_at > self.intent_expires_at:
            raise ValueError("shadow capture cannot occur after intent expiry")
        if self.capture_quote.symbol != self.symbol:
            raise ValueError("embedded capture quote symbol drift")
        if self.capture_quote.observed_at > self.captured_at:
            raise ValueError("embedded capture quote cannot come from the future")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m186-shadow-intent-v2",
            "champion_fingerprint": self.champion_fingerprint,
            "champion_deployment_fingerprint": self.champion_deployment_fingerprint,
            "lane_id": self.lane_id,
            "intent_hash": self.intent_hash,
            "client_tag": self.client_tag,
            "strategy_fingerprint": self.strategy_fingerprint,
            "session_fingerprint": self.session_fingerprint,
            "cognition_fingerprint": self.cognition_fingerprint,
            "person": {
                "analyst": self.analyst_state.value,
                "skeptic": self.skeptic_state.value,
                "patience": self.patience_state.value,
                "guardian": self.guardian_state.value,
            },
            "symbol": self.symbol,
            "side": self.side.value,
            "order_style": self.order_style.value,
            "volume": self.volume,
            "reference_price": self.reference_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "stop_limit_price": self.stop_limit_price,
            "approved_risk_fraction": self.approved_risk_fraction,
            "allowed_loss": self.allowed_loss,
            "max_price_drift_fraction": self.max_price_drift_fraction,
            "captured_at": self.captured_at.isoformat(),
            "intent_expires_at": self.intent_expires_at.isoformat(),
            "pending_expires_at": None if self.pending_expires_at is None else self.pending_expires_at.isoformat(),
            "capture_quote": {**self.capture_quote.payload, "fingerprint": self.capture_quote.fingerprint},
            "capture_policy_fingerprint": self.capture_policy_fingerprint,
            "execution_blocked": True,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def order_send_authority(self) -> bool:
        return False

    @property
    def retry_authority(self) -> bool:
        return False

    @property
    def position_mutation_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def _market_price(side: TradeSide, quote: ShadowMarketQuote) -> float:
    return quote.ask if side is TradeSide.LONG else quote.bid


def _pending_geometry_valid(intent: OrderIntent, market_price: float) -> bool:
    if intent.order_style is OrderStyle.LIMIT:
        return intent.reference_price < market_price if intent.side is TradeSide.LONG else intent.reference_price > market_price
    if intent.order_style in {OrderStyle.STOP, OrderStyle.STOP_LIMIT}:
        return intent.reference_price > market_price if intent.side is TradeSide.LONG else intent.reference_price < market_price
    return True


def capture_shadow_intent(
    registry: FrozenChampionRegistry,
    champion: FrozenChampionRecord,
    intent: OrderIntent,
    cognition: CognitionAssessment,
    capture_quote: ShadowMarketQuote,
    *,
    captured_at: datetime,
    policy: ShadowCapturePolicy,
) -> ShadowExecutionIntent:
    """Freeze an approved intent while guaranteeing execution remains blocked."""

    _validate_cognition(cognition)
    captured = _aware(captured_at, "shadow capture time")
    if registry.state(champion.fingerprint) is not ChampionLifecycleState.ACTIVE:
        raise ValueError("shadow execution requires ACTIVE Frozen Champion")
    active = registry.active_for_lane(champion.lane_id)
    if active is None or active.fingerprint != champion.fingerprint:
        raise ValueError("shadow Champion is not the unique active Champion for its lane")
    if _sha(intent.strategy_hash, "OrderIntent strategy") != champion.strategy_fingerprint:
        raise ValueError("OrderIntent strategy does not match Frozen Champion")
    if capture_quote.symbol != intent.symbol.strip().upper():
        raise ValueError("capture quote symbol does not match OrderIntent")
    if capture_quote.observed_at > captured:
        raise ValueError("future quote cannot be used to capture shadow intent")
    age_ms = (captured - capture_quote.observed_at).total_seconds() * 1000.0
    if age_ms > policy.maximum_quote_age_ms + 1e-9:
        raise ValueError("capture quote exceeds explicit staleness policy")
    if captured < _aware(intent.created_at, "OrderIntent created_at") or captured > _aware(intent.expires_at, "OrderIntent expires_at"):
        raise ValueError("shadow capture must occur within OrderIntent validity window")
    if not all((intent.pm_approved, intent.risk_approved, intent.guardian_approved)) or intent.growth_multiplier <= 0:
        raise ValueError("shadow execution requires fully governance-approved OrderIntent")
    if cognition.cognition.guardian is GuardianState.STOP:
        raise ValueError("Guardian STOP cannot be frozen as executable shadow intent")
    if not _pending_geometry_valid(intent, _market_price(intent.side, capture_quote)):
        raise ValueError("pending OrderIntent geometry is invalid at capture quote")
    return ShadowExecutionIntent(
        champion.fingerprint,
        champion.deployment_fingerprint,
        champion.lane_id,
        intent.intent_hash,
        intent.client_tag,
        champion.strategy_fingerprint,
        _sha(intent.session_fingerprint, "OrderIntent session"),
        cognition.fingerprint,
        cognition.cognition.analyst,
        cognition.cognition.skeptic,
        cognition.cognition.patience,
        cognition.cognition.guardian,
        intent.symbol,
        intent.side,
        intent.order_style,
        intent.volume,
        intent.reference_price,
        intent.stop_price,
        intent.target_price,
        intent.stop_limit_price,
        intent.approved_risk_fraction,
        intent.allowed_loss,
        intent.max_price_drift_fraction,
        captured,
        intent.expires_at,
        intent.pending_expiry,
        capture_quote,
        policy.fingerprint,
    )


class ShadowAssessmentStatus(StrEnum):
    WOULD_EXECUTE = "would_execute"
    WOULD_NOT_EXECUTE = "would_not_execute"
    EXPIRED_UNFILLED = "expired_unfilled"
    INSUFFICIENT_MARKET_EVIDENCE = "insufficient_market_evidence"


@dataclass(frozen=True, slots=True)
class ShadowExecutionAssessment:
    shadow_fingerprint: str
    quote_window_fingerprint: str
    status: ShadowAssessmentStatus
    evaluated_at: datetime
    trigger_quote_fingerprint: str | None
    executable_quote_fingerprint: str | None
    theoretical_execution_price: float | None
    adverse_price_delta: float | None
    adverse_price_fraction: float | None
    time_to_executable_ms: float | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "shadow_fingerprint", _sha(self.shadow_fingerprint, "assessment shadow"))
        object.__setattr__(self, "quote_window_fingerprint", _sha(self.quote_window_fingerprint, "assessment quote window"))
        object.__setattr__(self, "evaluated_at", _aware(self.evaluated_at, "assessment evaluated_at"))
        for field in ("trigger_quote_fingerprint", "executable_quote_fingerprint"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _sha(value, field))
        for field in (
            "theoretical_execution_price",
            "adverse_price_delta",
            "adverse_price_fraction",
            "time_to_executable_ms",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _finite(value, field))
        if self.time_to_executable_ms is not None and self.time_to_executable_ms < 0:
            raise ValueError("shadow time_to_executable_ms cannot be negative")
        reasons = tuple(sorted({_text(value, "assessment reason", maximum=128) for value in self.reasons}))
        if not reasons:
            raise ValueError("shadow assessment requires reason evidence")
        object.__setattr__(self, "reasons", reasons)
        has_execution = self.status is ShadowAssessmentStatus.WOULD_EXECUTE
        execution_values = (
            self.executable_quote_fingerprint,
            self.theoretical_execution_price,
            self.adverse_price_delta,
            self.adverse_price_fraction,
            self.time_to_executable_ms,
        )
        if has_execution != all(value is not None for value in execution_values):
            raise ValueError("shadow execution metrics/status identity drift")

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m186-shadow-assessment-v2",
                self.shadow_fingerprint,
                self.quote_window_fingerprint,
                self.status.value,
                self.evaluated_at.isoformat(),
                self.trigger_quote_fingerprint,
                self.executable_quote_fingerprint,
                self.theoretical_execution_price,
                self.adverse_price_delta,
                self.adverse_price_fraction,
                self.time_to_executable_ms,
                self.reasons,
            )
        )

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def order_send_authority(self) -> bool:
        return False


def _execution_assessment(
    shadow: ShadowExecutionIntent,
    window: ShadowQuoteWindow,
    quote: ShadowMarketQuote,
    trigger: ShadowMarketQuote | None,
    evaluated: datetime,
) -> ShadowExecutionAssessment:
    price = _market_price(shadow.side, quote)
    direction = 1.0 if shadow.side is TradeSide.LONG else -1.0
    adverse = (price - shadow.reference_price) * direction
    latency = max(0.0, (quote.observed_at - shadow.captured_at).total_seconds() * 1000.0)
    return ShadowExecutionAssessment(
        shadow.fingerprint,
        window.fingerprint,
        ShadowAssessmentStatus.WOULD_EXECUTE,
        evaluated,
        None if trigger is None else trigger.fingerprint,
        quote.fingerprint,
        price,
        adverse,
        adverse / shadow.reference_price,
        latency,
        ("read_only_market_geometry_became_executable",),
    )


def assess_shadow_execution(
    shadow: ShadowExecutionIntent,
    window: ShadowQuoteWindow,
    *,
    evaluated_at: datetime,
) -> ShadowExecutionAssessment:
    """Evaluate a frozen intent against a provenance-bound quote window.

    ``WOULD_EXECUTE`` means only observable bid/ask geometry became executable.
    It is not a broker-fill claim. Missing/sparse coverage never becomes an
    invented unfilled outcome; M188 later reconciles real M187 broker evidence.
    """

    evaluated = _aware(evaluated_at, "shadow evaluation time")
    if window.symbol != shadow.symbol:
        raise ValueError("quote window symbol does not match shadow intent")
    if window.coverage_start > shadow.captured_at:
        raise ValueError("quote window does not cover shadow capture time")
    if evaluated < window.coverage_end:
        raise ValueError("shadow evaluation time predates quote-window coverage end")
    rows = tuple(row for row in window.quotes if row.observed_at >= shadow.captured_at)

    if shadow.order_style is OrderStyle.MARKET:
        price = _market_price(shadow.side, shadow.capture_quote)
        drift = abs(price - shadow.reference_price) / shadow.reference_price
        if drift <= shadow.max_price_drift_fraction:
            return _execution_assessment(shadow, window, shadow.capture_quote, shadow.capture_quote, evaluated)
        if evaluated >= shadow.intent_expires_at and window.complete and window.coverage_end >= shadow.intent_expires_at:
            return ShadowExecutionAssessment(
                shadow.fingerprint,
                window.fingerprint,
                ShadowAssessmentStatus.EXPIRED_UNFILLED,
                evaluated,
                shadow.capture_quote.fingerprint,
                None,
                None,
                None,
                None,
                None,
                ("market_quote_exceeded_intent_price_drift_until_expiry",),
            )
        return ShadowExecutionAssessment(
            shadow.fingerprint,
            window.fingerprint,
            ShadowAssessmentStatus.INSUFFICIENT_MARKET_EVIDENCE if not window.complete else ShadowAssessmentStatus.WOULD_NOT_EXECUTE,
            evaluated,
            shadow.capture_quote.fingerprint,
            None,
            None,
            None,
            None,
            None,
            ("capture_price_drift_exceeded",),
        )

    expiry = shadow.pending_expires_at
    if expiry is None:
        raise ValueError("pending shadow intent lost pending expiration")
    trigger_quote: ShadowMarketQuote | None = None
    executable_quote: ShadowMarketQuote | None = None

    if shadow.order_style is OrderStyle.LIMIT:
        for row in rows:
            if row.observed_at > expiry:
                break
            price = _market_price(shadow.side, row)
            if (shadow.side is TradeSide.LONG and price <= shadow.reference_price) or (
                shadow.side is TradeSide.SHORT and price >= shadow.reference_price
            ):
                trigger_quote = executable_quote = row
                break
    elif shadow.order_style is OrderStyle.STOP:
        for row in rows:
            if row.observed_at > expiry:
                break
            price = _market_price(shadow.side, row)
            if (shadow.side is TradeSide.LONG and price >= shadow.reference_price) or (
                shadow.side is TradeSide.SHORT and price <= shadow.reference_price
            ):
                trigger_quote = executable_quote = row
                break
    elif shadow.order_style is OrderStyle.STOP_LIMIT:
        if shadow.stop_limit_price is None:
            raise ValueError("stop-limit shadow intent lost limit price")
        triggered = False
        for row in rows:
            if row.observed_at > expiry:
                break
            price = _market_price(shadow.side, row)
            if not triggered:
                hit = (shadow.side is TradeSide.LONG and price >= shadow.reference_price) or (
                    shadow.side is TradeSide.SHORT and price <= shadow.reference_price
                )
                if hit:
                    trigger_quote = row
                    triggered = True
            if triggered and (
                (shadow.side is TradeSide.LONG and price <= shadow.stop_limit_price)
                or (shadow.side is TradeSide.SHORT and price >= shadow.stop_limit_price)
            ):
                executable_quote = row
                break
    else:  # pragma: no cover
        raise ValueError(f"unsupported shadow order style: {shadow.order_style}")

    if executable_quote is not None:
        return _execution_assessment(shadow, window, executable_quote, trigger_quote, evaluated)

    if evaluated >= expiry:
        if not window.complete or window.coverage_end < expiry:
            return ShadowExecutionAssessment(
                shadow.fingerprint,
                window.fingerprint,
                ShadowAssessmentStatus.INSUFFICIENT_MARKET_EVIDENCE,
                evaluated,
                None if trigger_quote is None else trigger_quote.fingerprint,
                None,
                None,
                None,
                None,
                None,
                ("incomplete_quote_coverage_cannot_prove_unfilled_expiry",),
            )
        return ShadowExecutionAssessment(
            shadow.fingerprint,
            window.fingerprint,
            ShadowAssessmentStatus.EXPIRED_UNFILLED,
            evaluated,
            None if trigger_quote is None else trigger_quote.fingerprint,
            None,
            None,
            None,
            None,
            None,
            ("pending_order_expired_without_executable_quote",),
        )

    return ShadowExecutionAssessment(
        shadow.fingerprint,
        window.fingerprint,
        ShadowAssessmentStatus.INSUFFICIENT_MARKET_EVIDENCE if not window.complete else ShadowAssessmentStatus.WOULD_NOT_EXECUTE,
        evaluated,
        None if trigger_quote is None else trigger_quote.fingerprint,
        None,
        None,
        None,
        None,
        None,
        ("market_geometry_not_yet_executable",),
    )


class ShadowExecutionVault:
    """M164-backed persistence for immutable M186 shadow evidence."""

    def __init__(self, vault: ResearchArtifactVault, *, producer_fingerprint: str) -> None:
        self._vault = vault
        self._producer = _sha(producer_fingerprint, "shadow producer")

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def order_send_authorized(self) -> bool:
        return False

    def record_intent(self, shadow: ShadowExecutionIntent) -> ResearchArtifactRecord:
        data = _canonical(shadow.payload).encode("utf-8")
        existing = tuple(
            row for row in self._vault.list_subject(shadow.intent_hash)
            if row.content_type == SHADOW_INTENT_CONTENT_TYPE
        )
        if existing:
            if len(existing) != 1 or self._vault.read_bytes(existing[0].record_fingerprint) != data:
                raise ValueError("OrderIntent already has different M186 shadow evidence")
            return existing[0]
        return self._vault.store_bytes(
            data,
            kind=ArtifactKind.OTHER,
            content_type=SHADOW_INTENT_CONTENT_TYPE,
            producer_fingerprint=self._producer,
            subject_fingerprint=shadow.intent_hash,
            source_fingerprints=(
                shadow.champion_fingerprint,
                shadow.champion_deployment_fingerprint,
                shadow.cognition_fingerprint,
                shadow.capture_quote.fingerprint,
                shadow.capture_quote.source_fingerprint,
                shadow.capture_policy_fingerprint,
            ),
            now=shadow.captured_at,
        )

    def record_assessment(
        self,
        assessment: ShadowExecutionAssessment,
        window: ShadowQuoteWindow,
    ) -> ResearchArtifactRecord:
        if assessment.quote_window_fingerprint != window.fingerprint:
            raise ValueError("assessment/quote-window identity drift")
        payload = {
            "protocol": "dusty-m186-shadow-assessment-v2",
            "fingerprint": assessment.fingerprint,
            "shadow_fingerprint": assessment.shadow_fingerprint,
            "quote_window_fingerprint": assessment.quote_window_fingerprint,
            "status": assessment.status.value,
            "evaluated_at": assessment.evaluated_at.isoformat(),
            "trigger_quote_fingerprint": assessment.trigger_quote_fingerprint,
            "executable_quote_fingerprint": assessment.executable_quote_fingerprint,
            "theoretical_execution_price": assessment.theoretical_execution_price,
            "adverse_price_delta": assessment.adverse_price_delta,
            "adverse_price_fraction": assessment.adverse_price_fraction,
            "time_to_executable_ms": assessment.time_to_executable_ms,
            "reasons": list(assessment.reasons),
            "quote_window": {
                "symbol": window.symbol,
                "coverage_start": window.coverage_start.isoformat(),
                "coverage_end": window.coverage_end.isoformat(),
                "complete": window.complete,
                "source_fingerprint": window.source_fingerprint,
                "quote_fingerprints": [row.fingerprint for row in window.quotes],
            },
        }
        sources = tuple(
            sorted(
                {
                    assessment.shadow_fingerprint,
                    window.fingerprint,
                    window.source_fingerprint,
                    *(row.fingerprint for row in window.quotes),
                    *(row.source_fingerprint for row in window.quotes),
                }
            )
        )
        return self._vault.store_bytes(
            _canonical(payload).encode("utf-8"),
            kind=ArtifactKind.OTHER,
            content_type=SHADOW_ASSESSMENT_CONTENT_TYPE,
            producer_fingerprint=self._producer,
            subject_fingerprint=assessment.shadow_fingerprint,
            source_fingerprints=sources,
            now=assessment.evaluated_at,
        )
