from __future__ import annotations

"""M186 no-send Shadow Execution Mode.

M186 freezes a governance-approved OrderIntent for the currently ACTIVE M185
Champion and evaluates that intent only against immutable market quotes.  This
module intentionally imports no MT5 execution adapter and exposes no send,
retry, cancel, position-mutation, promotion, or risk-override surface.

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
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m186-market-quote-v1",
            self.symbol,
            self.observed_at.isoformat(),
            self.bid,
            self.ask,
            self.source_fingerprint,
        ))


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
    capture_quote_fingerprint: str

    def __post_init__(self) -> None:
        for field, label in (
            ("champion_fingerprint", "shadow Champion"),
            ("champion_deployment_fingerprint", "shadow Champion deployment"),
            ("intent_hash", "shadow intent"),
            ("strategy_fingerprint", "shadow strategy"),
            ("session_fingerprint", "shadow session"),
            ("cognition_fingerprint", "shadow cognition"),
            ("capture_quote_fingerprint", "shadow capture quote"),
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

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m186-shadow-intent-v1",
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
            "capture_quote_fingerprint": self.capture_quote_fingerprint,
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


def capture_shadow_intent(
    registry: FrozenChampionRegistry,
    champion: FrozenChampionRecord,
    intent: OrderIntent,
    cognition: CognitionAssessment,
    capture_quote: ShadowMarketQuote,
    *,
    captured_at: datetime,
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
    if captured < _aware(intent.created_at, "OrderIntent created_at") or captured > _aware(intent.expires_at, "OrderIntent expires_at"):
        raise ValueError("shadow capture must occur within OrderIntent validity window")
    if not all((intent.pm_approved, intent.risk_approved, intent.guardian_approved)) or intent.growth_multiplier <= 0:
        raise ValueError("shadow execution requires fully governance-approved OrderIntent")
    if cognition.cognition.guardian is GuardianState.STOP:
        raise ValueError("Guardian STOP cannot be frozen as executable shadow intent")
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
        capture_quote.fingerprint,
    )


class ShadowAssessmentStatus(StrEnum):
    WOULD_EXECUTE = "would_execute"
    WOULD_NOT_EXECUTE = "would_not_execute"
    EXPIRED_UNFILLED = "expired_unfilled"
    INSUFFICIENT_MARKET_EVIDENCE = "insufficient_market_evidence"


@dataclass(frozen=True, slots=True)
class ShadowExecutionAssessment:
    shadow_fingerprint: str
    status: ShadowAssessmentStatus
    evaluated_at: datetime
    quote_fingerprints: tuple[str, ...]
    trigger_quote_fingerprint: str | None
    executable_quote_fingerprint: str | None
    theoretical_execution_price: float | None
    adverse_price_delta: float | None
    adverse_price_fraction: float | None
    latency_ms: float | None
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "shadow_fingerprint", _sha(self.shadow_fingerprint, "assessment shadow"))
        object.__setattr__(self, "evaluated_at", _aware(self.evaluated_at, "assessment evaluated_at"))
        quotes = tuple(_sha(value, "assessment quote") for value in self.quote_fingerprints)
        if len(quotes) != len(set(quotes)):
            raise ValueError("assessment quote evidence must be unique")
        object.__setattr__(self, "quote_fingerprints", quotes)
        for field in ("trigger_quote_fingerprint", "executable_quote_fingerprint"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _sha(value, field))
        for field in ("theoretical_execution_price", "adverse_price_delta", "adverse_price_fraction", "latency_ms"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _finite(value, field))
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("shadow latency cannot be negative")
        object.__setattr__(self, "reasons", tuple(sorted({_text(value, "assessment reason", maximum=128) for value in self.reasons})))
        has_execution = self.status is ShadowAssessmentStatus.WOULD_EXECUTE
        execution_values = (
            self.executable_quote_fingerprint,
            self.theoretical_execution_price,
            self.adverse_price_delta,
            self.adverse_price_fraction,
            self.latency_ms,
        )
        if has_execution != all(value is not None for value in execution_values):
            raise ValueError("shadow execution metrics/status identity drift")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m186-shadow-assessment-v1",
            self.shadow_fingerprint,
            self.status.value,
            self.evaluated_at.isoformat(),
            self.quote_fingerprints,
            self.trigger_quote_fingerprint,
            self.executable_quote_fingerprint,
            self.theoretical_execution_price,
            self.adverse_price_delta,
            self.adverse_price_fraction,
            self.latency_ms,
            self.reasons,
        ))

    @property
    def broker_write_authority(self) -> bool:
        return False


def _executable_price(shadow: ShadowExecutionIntent, quote: ShadowMarketQuote) -> float:
    return quote.ask if shadow.side is TradeSide.LONG else quote.bid


def assess_shadow_execution(
    shadow: ShadowExecutionIntent,
    quotes: Iterable[ShadowMarketQuote],
    *,
    evaluated_at: datetime,
) -> ShadowExecutionAssessment:
    """Evaluate the frozen intent against read-only quote history.

    This is intentionally a market-observability comparison, not broker-fill
    simulation. M188 will later reconcile M187 demo execution against expected
    execution. Here, a quote satisfying order geometry means only WOULD_EXECUTE.
    """

    evaluated = _aware(evaluated_at, "shadow evaluation time")
    rows = tuple(sorted(quotes, key=lambda row: (row.observed_at, row.fingerprint)))
    quote_fps = tuple(row.fingerprint for row in rows)
    if len(quote_fps) != len(set(quote_fps)):
        raise ValueError("duplicate market quote evidence")
    if any(row.symbol != shadow.symbol for row in rows):
        raise ValueError("market quote symbol drift in shadow assessment")
    if any(row.observed_at < shadow.captured_at for row in rows):
        raise ValueError("shadow assessment cannot use pre-capture market quote")
    if rows and evaluated < rows[-1].observed_at:
        raise ValueError("shadow evaluation time predates supplied quote evidence")
    if not rows:
        return ShadowExecutionAssessment(
            shadow.fingerprint,
            ShadowAssessmentStatus.INSUFFICIENT_MARKET_EVIDENCE,
            evaluated,
            (),
            None,
            None,
            None,
            None,
            None,
            None,
            ("no_post_capture_market_quotes",),
        )

    trigger_quote: ShadowMarketQuote | None = None
    executable_quote: ShadowMarketQuote | None = None
    market_expiry = shadow.pending_expires_at if shadow.order_style is not OrderStyle.MARKET else shadow.intent_expires_at
    if market_expiry is None:
        market_expiry = shadow.intent_expires_at

    if shadow.order_style is OrderStyle.MARKET:
        first = rows[0]
        if first.observed_at <= shadow.intent_expires_at:
            price = _executable_price(shadow, first)
            drift = abs(price - shadow.reference_price) / shadow.reference_price
            if drift <= shadow.max_price_drift_fraction:
                trigger_quote = executable_quote = first
    elif shadow.order_style is OrderStyle.LIMIT:
        for row in rows:
            if row.observed_at > market_expiry:
                break
            price = _executable_price(shadow, row)
            if (shadow.side is TradeSide.LONG and price <= shadow.reference_price) or (
                shadow.side is TradeSide.SHORT and price >= shadow.reference_price
            ):
                trigger_quote = executable_quote = row
                break
    elif shadow.order_style is OrderStyle.STOP:
        for row in rows:
            if row.observed_at > market_expiry:
                break
            price = _executable_price(shadow, row)
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
            if row.observed_at > market_expiry:
                break
            price = _executable_price(shadow, row)
            if not triggered:
                trigger = (shadow.side is TradeSide.LONG and price >= shadow.reference_price) or (
                    shadow.side is TradeSide.SHORT and price <= shadow.reference_price
                )
                if trigger:
                    trigger_quote = row
                    triggered = True
            if triggered and (
                (shadow.side is TradeSide.LONG and price <= shadow.stop_limit_price)
                or (shadow.side is TradeSide.SHORT and price >= shadow.stop_limit_price)
            ):
                executable_quote = row
                break
    else:  # pragma: no cover - defensive against future enum expansion
        raise ValueError(f"unsupported shadow order style: {shadow.order_style}")

    if executable_quote is not None:
        price = _executable_price(shadow, executable_quote)
        direction = 1.0 if shadow.side is TradeSide.LONG else -1.0
        adverse = (price - shadow.reference_price) * direction
        latency = (executable_quote.observed_at - shadow.captured_at).total_seconds() * 1000.0
        return ShadowExecutionAssessment(
            shadow.fingerprint,
            ShadowAssessmentStatus.WOULD_EXECUTE,
            evaluated,
            quote_fps,
            None if trigger_quote is None else trigger_quote.fingerprint,
            executable_quote.fingerprint,
            price,
            adverse,
            adverse / shadow.reference_price,
            latency,
            ("read_only_market_geometry_became_executable",),
        )

    expired = evaluated >= market_expiry
    if expired:
        reason = "pending_order_expired_without_executable_quote" if shadow.order_style is not OrderStyle.MARKET else "market_intent_expired_without_acceptable_quote"
        status = ShadowAssessmentStatus.EXPIRED_UNFILLED
    else:
        reason = "market_geometry_not_yet_executable"
        status = ShadowAssessmentStatus.WOULD_NOT_EXECUTE
    return ShadowExecutionAssessment(
        shadow.fingerprint,
        status,
        evaluated,
        quote_fps,
        None if trigger_quote is None else trigger_quote.fingerprint,
        None,
        None,
        None,
        None,
        None,
        (reason,),
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
                shadow.capture_quote_fingerprint,
            ),
            now=shadow.captured_at,
        )

    def record_assessment(self, assessment: ShadowExecutionAssessment) -> ResearchArtifactRecord:
        payload = {
            "protocol": "dusty-m186-shadow-assessment-v1",
            "fingerprint": assessment.fingerprint,
            "shadow_fingerprint": assessment.shadow_fingerprint,
            "status": assessment.status.value,
            "evaluated_at": assessment.evaluated_at.isoformat(),
            "quote_fingerprints": list(assessment.quote_fingerprints),
            "trigger_quote_fingerprint": assessment.trigger_quote_fingerprint,
            "executable_quote_fingerprint": assessment.executable_quote_fingerprint,
            "theoretical_execution_price": assessment.theoretical_execution_price,
            "adverse_price_delta": assessment.adverse_price_delta,
            "adverse_price_fraction": assessment.adverse_price_fraction,
            "latency_ms": assessment.latency_ms,
            "reasons": list(assessment.reasons),
        }
        sources = tuple(sorted({assessment.shadow_fingerprint, *assessment.quote_fingerprints}))
        return self._vault.store_bytes(
            _canonical(payload).encode("utf-8"),
            kind=ArtifactKind.OTHER,
            content_type=SHADOW_ASSESSMENT_CONTENT_TYPE,
            producer_fingerprint=self._producer,
            subject_fingerprint=assessment.shadow_fingerprint,
            source_fingerprints=sources,
            now=assessment.evaluated_at,
        )
