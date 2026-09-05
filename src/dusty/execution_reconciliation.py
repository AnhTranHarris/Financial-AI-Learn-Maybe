from __future__ import annotations

"""M188 broker-history execution reconciliation.

M188 reconciles an immutable M186 expected execution and the M187 admission/send
receipt against explicit read-only MT5 order/deal/position evidence. It never
calls order_send, retries, cancels, mutates a position, changes risk, promotes a
Champion, or overrides Guardian.

A successful order_send response is not final fill truth. Broker deal history is
the primary fill evidence; active orders and positions explain pending/partial
states. Missing history remains INCOMPLETE unless an explicit, non-ambiguous
broker rejection is already known.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math

from .artifact_vault import ArtifactKind, ResearchArtifactRecord, ResearchArtifactVault
from .demo_execution_bridge import DemoBridgeExecutionReceipt
from .execution_lifecycle import ExecutionState
from .experience import TradeSide
from .shadow_execution import ShadowExecutionIntent
from .strategy_v3 import OrderStyle


RECONCILIATION_CONTENT_TYPE = "application/vnd.dusty.m188-execution-reconciliation+json"


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


def _text(value: str, label: str, *, maximum: int = 128) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


@dataclass(frozen=True, slots=True)
class BrokerDealEvidence:
    deal_ticket: int
    order_ticket: int
    position_id: int
    symbol: str
    side: TradeSide
    executed_at: datetime
    volume: float
    price: float
    commission: float
    swap: float
    fee: float
    source_fingerprint: str

    def __post_init__(self) -> None:
        for field in ("deal_ticket", "order_ticket"):
            value = getattr(self, field)
            if isinstance(value, bool) or int(value) != value or int(value) <= 0:
                raise ValueError(f"{field} must be a positive integer")
            object.__setattr__(self, field, int(value))
        if isinstance(self.position_id, bool) or int(self.position_id) != self.position_id or int(self.position_id) < 0:
            raise ValueError("position_id must be a nonnegative integer")
        object.__setattr__(self, "position_id", int(self.position_id))
        object.__setattr__(self, "symbol", _text(self.symbol, "deal symbol", maximum=64).upper())
        object.__setattr__(self, "executed_at", _aware(self.executed_at, "deal executed_at"))
        for field in ("volume", "price", "commission", "swap", "fee"):
            object.__setattr__(self, field, _finite(getattr(self, field), field))
        if self.volume <= 0 or self.price <= 0:
            raise ValueError("deal volume/price must be positive")
        object.__setattr__(self, "source_fingerprint", _sha(self.source_fingerprint, "deal source"))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "deal_ticket": self.deal_ticket,
            "order_ticket": self.order_ticket,
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "executed_at": self.executed_at.isoformat(),
            "volume": self.volume,
            "price": self.price,
            "commission": self.commission,
            "swap": self.swap,
            "fee": self.fee,
            "source_fingerprint": self.source_fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m188-deal-v1", self.payload))


@dataclass(frozen=True, slots=True)
class BrokerOrderEvidence:
    order_ticket: int
    symbol: str
    volume_initial: float
    volume_current: float
    state: int
    filling_mode: int
    position_id: int
    source_fingerprint: str

    def __post_init__(self) -> None:
        if isinstance(self.order_ticket, bool) or int(self.order_ticket) != self.order_ticket or int(self.order_ticket) <= 0:
            raise ValueError("order_ticket must be a positive integer")
        object.__setattr__(self, "order_ticket", int(self.order_ticket))
        object.__setattr__(self, "symbol", _text(self.symbol, "order symbol", maximum=64).upper())
        for field in ("volume_initial", "volume_current"):
            object.__setattr__(self, field, _finite(getattr(self, field), field))
        if self.volume_initial <= 0 or not 0 <= self.volume_current <= self.volume_initial:
            raise ValueError("order volume is invalid")
        object.__setattr__(self, "state", int(self.state))
        object.__setattr__(self, "filling_mode", int(self.filling_mode))
        object.__setattr__(self, "position_id", int(self.position_id))
        if self.position_id < 0:
            raise ValueError("order position_id cannot be negative")
        object.__setattr__(self, "source_fingerprint", _sha(self.source_fingerprint, "order source"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m188-order-v1",
            self.order_ticket,
            self.symbol,
            self.volume_initial,
            self.volume_current,
            self.state,
            self.filling_mode,
            self.position_id,
            self.source_fingerprint,
        ))


@dataclass(frozen=True, slots=True)
class BrokerPositionEvidence:
    position_ticket: int
    symbol: str
    side: TradeSide
    volume: float
    price_open: float
    source_fingerprint: str

    def __post_init__(self) -> None:
        if isinstance(self.position_ticket, bool) or int(self.position_ticket) != self.position_ticket or int(self.position_ticket) <= 0:
            raise ValueError("position_ticket must be a positive integer")
        object.__setattr__(self, "position_ticket", int(self.position_ticket))
        object.__setattr__(self, "symbol", _text(self.symbol, "position symbol", maximum=64).upper())
        object.__setattr__(self, "volume", _finite(self.volume, "position volume"))
        object.__setattr__(self, "price_open", _finite(self.price_open, "position price_open"))
        if self.volume <= 0 or self.price_open <= 0:
            raise ValueError("position volume/price must be positive")
        object.__setattr__(self, "source_fingerprint", _sha(self.source_fingerprint, "position source"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m188-position-v1",
            self.position_ticket,
            self.symbol,
            self.side.value,
            self.volume,
            self.price_open,
            self.source_fingerprint,
        ))


@dataclass(frozen=True, slots=True)
class BrokerExecutionEvidence:
    intent_hash: str
    session_fingerprint: str
    symbol: str
    observed_at: datetime
    history_complete: bool
    source_fingerprint: str
    deals: tuple[BrokerDealEvidence, ...] = ()
    active_orders: tuple[BrokerOrderEvidence, ...] = ()
    positions: tuple[BrokerPositionEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_hash", _sha(self.intent_hash, "broker evidence intent"))
        object.__setattr__(self, "session_fingerprint", _sha(self.session_fingerprint, "broker evidence session"))
        symbol = _text(self.symbol, "broker evidence symbol", maximum=64).upper()
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "broker evidence observed_at"))
        object.__setattr__(self, "source_fingerprint", _sha(self.source_fingerprint, "broker evidence source"))
        deals = tuple(sorted(self.deals, key=lambda row: (row.executed_at, row.deal_ticket)))
        orders = tuple(sorted(self.active_orders, key=lambda row: row.order_ticket))
        positions = tuple(sorted(self.positions, key=lambda row: row.position_ticket))
        if len({row.deal_ticket for row in deals}) != len(deals):
            raise ValueError("duplicate broker deal tickets")
        if len({row.order_ticket for row in orders}) != len(orders):
            raise ValueError("duplicate active order tickets")
        if len({row.position_ticket for row in positions}) != len(positions):
            raise ValueError("duplicate position tickets")
        if any(row.symbol != symbol for row in (*deals, *orders, *positions)):
            raise ValueError("broker evidence cannot mix symbols")
        if any(row.executed_at > self.observed_at for row in deals):
            raise ValueError("broker evidence contains future deal")
        object.__setattr__(self, "deals", deals)
        object.__setattr__(self, "active_orders", orders)
        object.__setattr__(self, "positions", positions)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m188-broker-evidence-v1",
            self.intent_hash,
            self.session_fingerprint,
            self.symbol,
            self.observed_at.isoformat(),
            self.history_complete,
            self.source_fingerprint,
            tuple(row.fingerprint for row in self.deals),
            tuple(row.fingerprint for row in self.active_orders),
            tuple(row.fingerprint for row in self.positions),
        ))


class ReconciliationStatus(StrEnum):
    INCOMPLETE = "incomplete"
    INCONSISTENT = "inconsistent"
    REJECTED = "rejected"
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"


@dataclass(frozen=True, slots=True)
class ExecutionReconciliation:
    status: ReconciliationStatus
    intent_hash: str
    shadow_fingerprint: str
    m187_receipt_fingerprint: str
    broker_evidence_fingerprint: str
    observed_at: datetime
    expected_price: float
    filled_volume: float
    fill_fraction: float
    weighted_average_fill_price: float | None
    adverse_slippage_price: float | None
    adverse_slippage_fraction: float | None
    first_fill_latency_ms: float | None
    last_fill_latency_ms: float | None
    commission: float
    swap: float
    fee: float
    order_tickets: tuple[int, ...]
    deal_tickets: tuple[int, ...]
    position_tickets: tuple[int, ...]
    reasons: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]

    def __post_init__(self) -> None:
        for field, label in (
            ("intent_hash", "reconciliation intent"),
            ("shadow_fingerprint", "reconciliation shadow"),
            ("m187_receipt_fingerprint", "M187 receipt"),
            ("broker_evidence_fingerprint", "broker evidence"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "reconciliation observed_at"))
        for field in ("expected_price", "filled_volume", "fill_fraction", "commission", "swap", "fee"):
            object.__setattr__(self, field, _finite(getattr(self, field), field))
        if self.expected_price <= 0 or self.filled_volume < 0 or not 0 <= self.fill_fraction <= 1:
            raise ValueError("reconciliation economics invalid")
        for field in (
            "weighted_average_fill_price",
            "adverse_slippage_price",
            "adverse_slippage_fraction",
            "first_fill_latency_ms",
            "last_fill_latency_ms",
        ):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _finite(value, field))
        if self.weighted_average_fill_price is not None and self.weighted_average_fill_price <= 0:
            raise ValueError("weighted average fill price must be positive")
        if self.first_fill_latency_ms is not None and self.first_fill_latency_ms < 0:
            raise ValueError("first fill latency cannot be negative")
        if self.last_fill_latency_ms is not None and self.last_fill_latency_ms < 0:
            raise ValueError("last fill latency cannot be negative")
        if self.first_fill_latency_ms is not None and self.last_fill_latency_ms is not None and self.last_fill_latency_ms < self.first_fill_latency_ms:
            raise ValueError("last fill latency cannot precede first fill latency")
        if not self.reasons:
            raise ValueError("reconciliation requires at least one reason")
        object.__setattr__(
            self,
            "evidence_fingerprints",
            tuple(sorted({_sha(row, "reconciliation evidence") for row in self.evidence_fingerprints})),
        )

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m188-execution-reconciliation-v1",
            "status": self.status.value,
            "intent_hash": self.intent_hash,
            "shadow_fingerprint": self.shadow_fingerprint,
            "m187_receipt_fingerprint": self.m187_receipt_fingerprint,
            "broker_evidence_fingerprint": self.broker_evidence_fingerprint,
            "observed_at": self.observed_at.isoformat(),
            "expected_price": self.expected_price,
            "filled_volume": self.filled_volume,
            "fill_fraction": self.fill_fraction,
            "weighted_average_fill_price": self.weighted_average_fill_price,
            "adverse_slippage_price": self.adverse_slippage_price,
            "adverse_slippage_fraction": self.adverse_slippage_fraction,
            "first_fill_latency_ms": self.first_fill_latency_ms,
            "last_fill_latency_ms": self.last_fill_latency_ms,
            "commission": self.commission,
            "swap": self.swap,
            "fee": self.fee,
            "order_tickets": list(self.order_tickets),
            "deal_tickets": list(self.deal_tickets),
            "position_tickets": list(self.position_tickets),
            "reasons": list(self.reasons),
            "evidence_fingerprints": list(self.evidence_fingerprints),
            "broker_write_authority": False,
            "retry_authority": False,
            "promotion_authority": False,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
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


def _expected_price(shadow: ShadowExecutionIntent) -> float:
    if shadow.order_style is OrderStyle.MARKET:
        return shadow.capture_quote.ask if shadow.side is TradeSide.LONG else shadow.capture_quote.bid
    return shadow.reference_price


_AMBIGUOUS_RETCODES = {10012}  # TRADE_RETCODE_TIMEOUT: reconcile broker state before any retry.
_SUCCESS_RETCODES = {10008, 10009, 10010}


def reconcile_execution(
    shadow: ShadowExecutionIntent,
    receipt: DemoBridgeExecutionReceipt,
    broker: BrokerExecutionEvidence,
) -> ExecutionReconciliation:
    """Reconcile one admitted Demo operation against explicit broker state."""

    if receipt.admission.intent_hash != shadow.intent_hash or receipt.execution.intent_hash != shadow.intent_hash:
        raise ValueError("M186/M187 intent identity drift")
    if receipt.admission.shadow_fingerprint != shadow.fingerprint:
        raise ValueError("M187 admission does not bind supplied M186 shadow")
    if broker.intent_hash != shadow.intent_hash:
        raise ValueError("broker evidence does not belong to M186/M187 intent")
    if broker.session_fingerprint != shadow.session_fingerprint or receipt.admission.session_fingerprint != shadow.session_fingerprint:
        raise ValueError("M186/M187/broker session identity drift")
    if broker.symbol != shadow.symbol:
        raise ValueError("M186/broker symbol identity drift")
    if broker.observed_at < receipt.admission.admitted_at:
        raise ValueError("broker evidence predates M187 admission")

    returned_order = int(receipt.execution.order_ticket)
    returned_deal = int(receipt.execution.deal_ticket)
    if returned_order < 0 or returned_deal < 0:
        raise ValueError("M187 execution tickets cannot be negative")

    deals = tuple(broker.deals)
    active_orders = tuple(broker.active_orders)
    positions = tuple(broker.positions)
    reasons: list[str] = []

    if returned_order:
        if any(row.order_ticket != returned_order for row in deals):
            reasons.append("broker_deal_order_ticket_mismatch")
        if any(row.order_ticket != returned_order for row in active_orders):
            reasons.append("active_order_ticket_mismatch")
    if returned_deal and deals and returned_deal not in {row.deal_ticket for row in deals}:
        reasons.append("returned_deal_ticket_missing_from_broker_history")
    if any(row.side is not shadow.side for row in deals):
        reasons.append("broker_deal_side_mismatch")
    if any(row.side is not shadow.side for row in positions):
        reasons.append("broker_position_side_mismatch")
    if receipt.execution.state is ExecutionState.REJECTED and deals:
        reasons.append("rejected_send_conflicts_with_observed_deal")

    filled_volume = sum(row.volume for row in deals)
    tolerance = max(1e-12, shadow.volume * 1e-9)
    if filled_volume > shadow.volume + tolerance:
        reasons.append("broker_history_overfill")
    status = ReconciliationStatus.INCONSISTENT if reasons else ReconciliationStatus.INCOMPLETE

    capped_volume = min(filled_volume, shadow.volume)
    fill_fraction = min(1.0, capped_volume / shadow.volume)
    expected_price = _expected_price(shadow)
    if deals:
        vwap = sum(row.volume * row.price for row in deals) / filled_volume
        adverse = vwap - expected_price if shadow.side is TradeSide.LONG else expected_price - vwap
        adverse_fraction = adverse / expected_price
        first_latency = (deals[0].executed_at - receipt.admission.admitted_at).total_seconds() * 1000.0
        last_latency = (deals[-1].executed_at - receipt.admission.admitted_at).total_seconds() * 1000.0
        if first_latency < -1e-9:
            reasons.append("broker_fill_predates_m187_admission")
            status = ReconciliationStatus.INCONSISTENT
    else:
        vwap = None
        adverse = None
        adverse_fraction = None
        first_latency = None
        last_latency = None

    if status is not ReconciliationStatus.INCONSISTENT:
        retcode = int(receipt.execution.retcode)
        if filled_volume >= shadow.volume - tolerance and deals:
            status = ReconciliationStatus.FILLED
            reasons.append("broker_deal_history_confirms_full_fill")
        elif deals:
            status = ReconciliationStatus.PARTIAL
            reasons.append(
                "broker_history_confirms_partial_fill_with_active_remainder"
                if active_orders
                else "broker_history_confirms_partial_fill"
            )
        elif active_orders:
            status = ReconciliationStatus.PENDING
            reasons.append("active_broker_order_without_fill")
        elif retcode in _AMBIGUOUS_RETCODES or receipt.execution.state is ExecutionState.SENT_UNKNOWN:
            status = ReconciliationStatus.INCOMPLETE
            reasons.append("ambiguous_send_requires_more_broker_evidence")
        elif receipt.execution.state is ExecutionState.REJECTED and retcode not in _SUCCESS_RETCODES:
            status = ReconciliationStatus.REJECTED
            reasons.append(f"explicit_broker_rejection_retcode:{retcode}")
        elif not broker.history_complete:
            status = ReconciliationStatus.INCOMPLETE
            reasons.append("broker_history_declared_incomplete")
        else:
            # Absence of deals after an accepted/placed send is not transformed
            # into a rejection; MT5 deal history may become visible later.
            status = ReconciliationStatus.INCOMPLETE
            reasons.append("accepted_send_has_no_broker_fill_evidence_yet")

    evidence = {
        shadow.fingerprint,
        receipt.fingerprint,
        receipt.admission.fingerprint,
        receipt.admission_artifact_record_fingerprint,
        broker.fingerprint,
        broker.source_fingerprint,
        *(row.fingerprint for row in deals),
        *(row.source_fingerprint for row in deals),
        *(row.fingerprint for row in active_orders),
        *(row.source_fingerprint for row in active_orders),
        *(row.fingerprint for row in positions),
        *(row.source_fingerprint for row in positions),
    }
    return ExecutionReconciliation(
        status,
        shadow.intent_hash,
        shadow.fingerprint,
        receipt.fingerprint,
        broker.fingerprint,
        broker.observed_at,
        expected_price,
        capped_volume,
        fill_fraction,
        vwap,
        adverse,
        adverse_fraction,
        first_latency,
        last_latency,
        sum(row.commission for row in deals),
        sum(row.swap for row in deals),
        sum(row.fee for row in deals),
        tuple(sorted({row.order_ticket for row in (*deals, *active_orders)})),
        tuple(row.deal_ticket for row in deals),
        tuple(sorted({row.position_ticket for row in positions})),
        tuple(reasons),
        tuple(evidence),
    )


def persist_reconciliation(
    vault: ResearchArtifactVault,
    reconciliation: ExecutionReconciliation,
    *,
    producer_fingerprint: str,
) -> ResearchArtifactRecord:
    """Persist the deterministic M188 result without creating another broker ledger."""

    producer = _sha(producer_fingerprint, "M188 producer")
    return vault.store_bytes(
        _canonical(reconciliation.payload).encode("utf-8"),
        kind=ArtifactKind.OTHER,
        content_type=RECONCILIATION_CONTENT_TYPE,
        producer_fingerprint=producer,
        subject_fingerprint=reconciliation.intent_hash,
        source_fingerprints=reconciliation.evidence_fingerprints,
        now=reconciliation.observed_at,
    )
