from __future__ import annotations

"""M185 immutable shadow-trade evidence and intent-to-fill comparison.

The recorder freezes what Dusty intended *before* any broker send. It reuses
OrderIntent and CognitionAssessment rather than creating a second execution
model. Persistence is delegated to the M164 append-only artifact vault.
Actual fills are supplied as explicit broker-history deal evidence; this module
does not call MT5, send orders, or infer a fill from order_check/order_send.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Iterable

from .artifact_vault import ArtifactKind, ResearchArtifactRecord, ResearchArtifactVault
from .cognition import CognitionAssessment
from .core import AnalystState, GuardianState, PatienceState, SkepticState
from .experience import TradeSide
from .order_intent import OrderIntent
from .strategy_v3 import OrderStyle


SHADOW_CONTENT_TYPE = "application/vnd.dusty.shadow-trade+json"
COMPARISON_CONTENT_TYPE = "application/vnd.dusty.shadow-fill-comparison+json"


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
    _sha(assessment.fingerprint, "cognition fingerprint")
    expected = {
        "analyst": assessment.cognition.analyst.value,
        "skeptic": assessment.cognition.skeptic.value,
        "patience": assessment.cognition.patience.value,
        "guardian": assessment.cognition.guardian.value,
    }
    observed: dict[str, str] = {}
    for row in assessment.justifications:
        role = _text(row.role, "cognition role", maximum=32).lower()
        if role in observed:
            raise ValueError("duplicate cognition role justification")
        state = _text(row.state, "cognition state", maximum=32).lower()
        for reason in row.reasons:
            _text(reason, "cognition reason", maximum=256)
        observed[role] = state
    if observed != expected:
        raise ValueError("cognition justification/state identity drift")


@dataclass(frozen=True, slots=True)
class ShadowTradeRecord:
    recorded_at: datetime
    intent_hash: str
    client_tag: str
    strategy_hash: str
    session_fingerprint: str
    symbol: str
    side: TradeSide
    order_style: OrderStyle
    planned_volume: float
    planned_entry: float
    planned_stop: float
    planned_target: float | None
    planned_notional: float
    approved_risk_fraction: float
    allowed_loss: float
    analyst_state: AnalystState
    skeptic_state: SkepticState
    patience_state: PatienceState
    guardian_state: GuardianState
    cognition_fingerprint: str
    analyst_score: float | None
    analyst_score_fingerprint: str | None
    spread_points: float
    decision_latency_ms: float
    stage: str
    campaign_fingerprint: str | None
    forecast_integration_fingerprint: str | None
    provider_fingerprints: tuple[str, ...]
    shadow_reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "recorded_at", _aware(self.recorded_at, "shadow recorded_at"))
        object.__setattr__(self, "intent_hash", _sha(self.intent_hash, "shadow intent"))
        object.__setattr__(self, "strategy_hash", _sha(self.strategy_hash, "shadow strategy"))
        object.__setattr__(self, "session_fingerprint", _sha(self.session_fingerprint, "shadow session"))
        object.__setattr__(self, "cognition_fingerprint", _sha(self.cognition_fingerprint, "shadow cognition"))
        object.__setattr__(self, "client_tag", _text(self.client_tag, "shadow client_tag", maximum=64))
        object.__setattr__(self, "symbol", _text(self.symbol, "shadow symbol", maximum=64))
        object.__setattr__(self, "stage", _text(self.stage, "shadow stage", maximum=64).lower())
        object.__setattr__(self, "shadow_reason", _text(self.shadow_reason, "shadow reason", maximum=512))

        for name in (
            "planned_volume",
            "planned_entry",
            "planned_stop",
            "planned_notional",
            "approved_risk_fraction",
            "allowed_loss",
            "spread_points",
            "decision_latency_ms",
        ):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if self.planned_target is not None:
            object.__setattr__(self, "planned_target", _finite(self.planned_target, "planned_target"))
        if self.planned_volume <= 0 or self.planned_entry <= 0 or self.planned_stop <= 0:
            raise ValueError("shadow planned volume/entry/stop must be positive")
        if self.planned_notional <= 0 or self.allowed_loss <= 0:
            raise ValueError("shadow notional/loss budget must be positive")
        if not 0 < self.approved_risk_fraction <= 1:
            raise ValueError("shadow approved risk fraction must be in (0,1]")
        if self.spread_points < 0 or self.decision_latency_ms < 0:
            raise ValueError("shadow spread/latency must be nonnegative")

        if (self.analyst_score is None) != (self.analyst_score_fingerprint is None):
            raise ValueError("analyst score and score-source fingerprint must appear together")
        if self.analyst_score is not None:
            object.__setattr__(self, "analyst_score", _finite(self.analyst_score, "analyst_score"))
            object.__setattr__(
                self,
                "analyst_score_fingerprint",
                _sha(self.analyst_score_fingerprint or "", "analyst score source"),
            )

        if self.campaign_fingerprint is not None:
            object.__setattr__(self, "campaign_fingerprint", _sha(self.campaign_fingerprint, "shadow campaign"))
        if self.forecast_integration_fingerprint is not None:
            object.__setattr__(
                self,
                "forecast_integration_fingerprint",
                _sha(self.forecast_integration_fingerprint, "forecast integration"),
            )
        providers = tuple(sorted(_sha(value, "shadow provider") for value in self.provider_fingerprints))
        if len(providers) != len(set(providers)):
            raise ValueError("shadow provider fingerprints must be unique")
        if providers and self.forecast_integration_fingerprint is None:
            raise ValueError("forecast provider evidence requires M184 integration identity")
        object.__setattr__(self, "provider_fingerprints", providers)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m185-shadow-trade-v1",
            "recorded_at": self.recorded_at.isoformat(),
            "intent_hash": self.intent_hash,
            "client_tag": self.client_tag,
            "strategy_hash": self.strategy_hash,
            "session_fingerprint": self.session_fingerprint,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_style": self.order_style.value,
            "planned_volume": self.planned_volume,
            "planned_entry": self.planned_entry,
            "planned_stop": self.planned_stop,
            "planned_target": self.planned_target,
            "planned_notional": self.planned_notional,
            "approved_risk_fraction": self.approved_risk_fraction,
            "allowed_loss": self.allowed_loss,
            "person": {
                "analyst": self.analyst_state.value,
                "skeptic": self.skeptic_state.value,
                "patience": self.patience_state.value,
                "guardian": self.guardian_state.value,
            },
            "cognition_fingerprint": self.cognition_fingerprint,
            "analyst_score": self.analyst_score,
            "analyst_score_fingerprint": self.analyst_score_fingerprint,
            "spread_points": self.spread_points,
            "decision_latency_ms": self.decision_latency_ms,
            "stage": self.stage,
            "campaign_fingerprint": self.campaign_fingerprint,
            "forecast_integration_fingerprint": self.forecast_integration_fingerprint,
            "provider_fingerprints": list(self.provider_fingerprints),
            "shadow_reason": self.shadow_reason,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def source_fingerprints(self) -> tuple[str, ...]:
        values = {self.cognition_fingerprint}
        for value in (
            self.campaign_fingerprint,
            self.forecast_integration_fingerprint,
            self.analyst_score_fingerprint,
        ):
            if value is not None:
                values.add(value)
        values.update(self.provider_fingerprints)
        return tuple(sorted(values))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def strategy_mutation_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def build_shadow_trade(
    intent: OrderIntent,
    cognition: CognitionAssessment,
    *,
    recorded_at: datetime,
    contract_size: float,
    spread_points: float,
    decision_latency_ms: float,
    stage: str,
    shadow_reason: str,
    campaign_fingerprint: str | None = None,
    forecast_integration_fingerprint: str | None = None,
    provider_fingerprints: Iterable[str] = (),
    analyst_score: float | None = None,
    analyst_score_fingerprint: str | None = None,
) -> ShadowTradeRecord:
    """Freeze one governance-approved pre-send intent/cognition envelope."""

    _validate_cognition(cognition)
    captured = _aware(recorded_at, "shadow recorded_at")
    created = _aware(intent.created_at, "intent created_at")
    expires = _aware(intent.expires_at, "intent expires_at")
    if captured < created or captured > expires:
        raise ValueError("shadow record must be captured after intent creation and before intent expiry")
    if not all((intent.pm_approved, intent.risk_approved, intent.guardian_approved)) or intent.growth_multiplier <= 0:
        raise ValueError("shadow recorder accepts only governance-approved trade intents")

    strategy_hash = _sha(intent.strategy_hash, "intent strategy")
    session_fingerprint = _sha(intent.session_fingerprint, "intent session")
    size = _finite(contract_size, "contract_size")
    if size <= 0:
        raise ValueError("contract_size must be positive")
    notional = intent.volume * size * intent.reference_price
    if not math.isfinite(notional) or notional <= 0:
        raise ValueError("derived planned notional is invalid")

    return ShadowTradeRecord(
        captured,
        intent.intent_hash,
        intent.client_tag,
        strategy_hash,
        session_fingerprint,
        intent.symbol,
        intent.side,
        intent.order_style,
        intent.volume,
        intent.reference_price,
        intent.stop_price,
        intent.target_price,
        notional,
        intent.approved_risk_fraction,
        intent.allowed_loss,
        cognition.cognition.analyst,
        cognition.cognition.skeptic,
        cognition.cognition.patience,
        cognition.cognition.guardian,
        cognition.fingerprint,
        analyst_score,
        analyst_score_fingerprint,
        spread_points,
        decision_latency_ms,
        stage,
        campaign_fingerprint,
        forecast_integration_fingerprint,
        tuple(provider_fingerprints),
        shadow_reason,
    )


@dataclass(frozen=True, slots=True)
class ObservedBrokerFill:
    deal_ticket: int
    order_ticket: int
    filled_at: datetime
    volume: float
    price: float
    source_fingerprint: str

    def __post_init__(self) -> None:
        if isinstance(self.deal_ticket, bool) or int(self.deal_ticket) != self.deal_ticket or int(self.deal_ticket) <= 0:
            raise ValueError("deal ticket must be a positive integer")
        if isinstance(self.order_ticket, bool) or int(self.order_ticket) != self.order_ticket or int(self.order_ticket) <= 0:
            raise ValueError("order ticket must be a positive integer")
        object.__setattr__(self, "deal_ticket", int(self.deal_ticket))
        object.__setattr__(self, "order_ticket", int(self.order_ticket))
        object.__setattr__(self, "filled_at", _aware(self.filled_at, "broker fill time"))
        object.__setattr__(self, "volume", _finite(self.volume, "broker fill volume"))
        object.__setattr__(self, "price", _finite(self.price, "broker fill price"))
        object.__setattr__(self, "source_fingerprint", _sha(self.source_fingerprint, "broker fill source"))
        if self.volume <= 0 or self.price <= 0:
            raise ValueError("broker fill volume/price must be positive")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m185-observed-broker-fill-v1",
            "deal_ticket": self.deal_ticket,
            "order_ticket": self.order_ticket,
            "filled_at": self.filled_at.isoformat(),
            "volume": self.volume,
            "price": self.price,
            "source_fingerprint": self.source_fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


@dataclass(frozen=True, slots=True)
class ShadowFillComparison:
    observed_at: datetime
    shadow_fingerprint: str
    intent_hash: str
    client_tag: str
    fills: tuple[ObservedBrokerFill, ...]
    filled_volume: float
    fill_fraction: float
    weighted_average_fill_price: float | None
    adverse_slippage_price: float | None
    adverse_slippage_fraction: float | None
    first_fill_latency_ms: float | None
    last_fill_latency_ms: float | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "comparison observed_at"))
        object.__setattr__(self, "shadow_fingerprint", _sha(self.shadow_fingerprint, "comparison shadow"))
        object.__setattr__(self, "intent_hash", _sha(self.intent_hash, "comparison intent"))
        object.__setattr__(self, "client_tag", _text(self.client_tag, "comparison client_tag", maximum=64))
        rows = tuple(sorted(self.fills, key=lambda row: (row.filled_at, row.deal_ticket)))
        if len({row.deal_ticket for row in rows}) != len(rows):
            raise ValueError("comparison broker deal tickets must be unique")
        object.__setattr__(self, "fills", rows)

        object.__setattr__(self, "filled_volume", _finite(self.filled_volume, "filled_volume"))
        object.__setattr__(self, "fill_fraction", _finite(self.fill_fraction, "fill_fraction"))
        if self.filled_volume < 0 or not 0.0 <= self.fill_fraction <= 1.0:
            raise ValueError("comparison fill volume/fraction is invalid")
        total = sum(row.volume for row in rows)
        if not math.isclose(self.filled_volume, total, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("comparison filled volume does not match deal evidence")

        optional = (
            "weighted_average_fill_price",
            "adverse_slippage_price",
            "adverse_slippage_fraction",
            "first_fill_latency_ms",
            "last_fill_latency_ms",
        )
        for name in optional:
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _finite(value, name))
        if not rows:
            if self.filled_volume != 0 or self.fill_fraction != 0 or any(getattr(self, name) is not None for name in optional):
                raise ValueError("no-fill comparison cannot carry fill metrics")
        else:
            if self.weighted_average_fill_price is None or self.weighted_average_fill_price <= 0:
                raise ValueError("filled comparison requires positive VWAP")
            expected_vwap = sum(row.price * row.volume for row in rows) / total
            if not math.isclose(self.weighted_average_fill_price, expected_vwap, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("comparison VWAP does not match deal evidence")
            if self.first_fill_latency_ms is None or self.last_fill_latency_ms is None:
                raise ValueError("filled comparison requires fill latency")
            if self.first_fill_latency_ms < 0 or self.last_fill_latency_ms < self.first_fill_latency_ms:
                raise ValueError("comparison fill latency ordering is invalid")
            if self.observed_at < rows[-1].filled_at:
                raise ValueError("comparison observation predates final broker fill")

    @property
    def fill_fingerprints(self) -> tuple[str, ...]:
        return tuple(row.fingerprint for row in self.fills)

    @property
    def fill_source_fingerprints(self) -> tuple[str, ...]:
        return tuple(sorted({row.source_fingerprint for row in self.fills}))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m185-shadow-fill-comparison-v2",
            "observed_at": self.observed_at.isoformat(),
            "shadow_fingerprint": self.shadow_fingerprint,
            "intent_hash": self.intent_hash,
            "client_tag": self.client_tag,
            "fills": [
                {**row.payload, "fingerprint": row.fingerprint}
                for row in self.fills
            ],
            "filled_volume": self.filled_volume,
            "fill_fraction": self.fill_fraction,
            "weighted_average_fill_price": self.weighted_average_fill_price,
            "adverse_slippage_price": self.adverse_slippage_price,
            "adverse_slippage_fraction": self.adverse_slippage_fraction,
            "first_fill_latency_ms": self.first_fill_latency_ms,
            "last_fill_latency_ms": self.last_fill_latency_ms,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def broker_write_authority(self) -> bool:
        return False


def compare_shadow_to_fills(
    shadow: ShadowTradeRecord,
    fills: Iterable[ObservedBrokerFill],
    *,
    observed_at: datetime,
) -> ShadowFillComparison:
    """Compare a frozen intent with explicit broker-history deal evidence.

    Positive slippage is adverse for both LONG and SHORT. Empty fills are a
    valid observation, but are not interpreted as a broker rejection here;
    M187 owns execution-deviation classification.
    """

    observed = _aware(observed_at, "comparison observed_at")
    if observed < shadow.recorded_at:
        raise ValueError("comparison observation predates frozen shadow intent")
    rows = tuple(sorted(fills, key=lambda row: (row.filled_at, row.deal_ticket)))
    if len({row.deal_ticket for row in rows}) != len(rows):
        raise ValueError("duplicate broker deal ticket in fill comparison")
    if any(row.filled_at < shadow.recorded_at for row in rows):
        raise ValueError("broker fill predates frozen shadow intent")
    if rows and observed < rows[-1].filled_at:
        raise ValueError("comparison observation predates broker fill")

    total_volume = sum(row.volume for row in rows)
    tolerance = max(1e-12, shadow.planned_volume * 1e-9)
    if total_volume > shadow.planned_volume + tolerance:
        raise ValueError("broker fills exceed frozen planned volume")
    fill_fraction = 0.0 if not rows else min(1.0, total_volume / shadow.planned_volume)
    if not rows:
        return ShadowFillComparison(
            observed,
            shadow.fingerprint,
            shadow.intent_hash,
            shadow.client_tag,
            (),
            0.0,
            0.0,
            None,
            None,
            None,
            None,
            None,
        )

    vwap = sum(row.price * row.volume for row in rows) / total_volume
    direction = 1.0 if shadow.side is TradeSide.LONG else -1.0
    adverse_price = (vwap - shadow.planned_entry) * direction
    adverse_fraction = adverse_price / shadow.planned_entry
    first_latency = (rows[0].filled_at - shadow.recorded_at).total_seconds() * 1000.0
    last_latency = (rows[-1].filled_at - shadow.recorded_at).total_seconds() * 1000.0
    return ShadowFillComparison(
        observed,
        shadow.fingerprint,
        shadow.intent_hash,
        shadow.client_tag,
        rows,
        total_volume,
        fill_fraction,
        vwap,
        adverse_price,
        adverse_fraction,
        first_latency,
        last_latency,
    )


class ShadowTradeRecorder:
    """Append-only M185 persistence adapter over the M164 artifact vault."""

    def __init__(self, vault: ResearchArtifactVault, *, producer_fingerprint: str) -> None:
        self._vault = vault
        self._producer = _sha(producer_fingerprint, "shadow recorder producer")

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def live_write_authorized(self) -> bool:
        return False

    def record_shadow(self, shadow: ShadowTradeRecord) -> ResearchArtifactRecord:
        data = _canonical(shadow.payload).encode("utf-8")
        existing = tuple(
            row
            for row in self._vault.list_subject(shadow.intent_hash)
            if row.content_type == SHADOW_CONTENT_TYPE
        )
        if existing:
            if len(existing) != 1 or self._vault.read_bytes(existing[0].record_fingerprint) != data:
                raise ValueError("intent already has different shadow evidence")
            return existing[0]
        return self._vault.store_bytes(
            data,
            kind=ArtifactKind.OTHER,
            content_type=SHADOW_CONTENT_TYPE,
            producer_fingerprint=self._producer,
            subject_fingerprint=shadow.intent_hash,
            source_fingerprints=shadow.source_fingerprints,
            now=shadow.recorded_at,
        )

    def record_comparison(self, comparison: ShadowFillComparison) -> ResearchArtifactRecord:
        data = _canonical(comparison.payload).encode("utf-8")
        sources = tuple(
            sorted(
                {
                    comparison.shadow_fingerprint,
                    *comparison.fill_fingerprints,
                    *comparison.fill_source_fingerprints,
                }
            )
        )
        return self._vault.store_bytes(
            data,
            kind=ArtifactKind.OTHER,
            content_type=COMPARISON_CONTENT_TYPE,
            producer_fingerprint=self._producer,
            subject_fingerprint=comparison.shadow_fingerprint,
            source_fingerprints=sources,
            now=comparison.observed_at,
        )
