from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Any, Callable

from .demo_session import DemoSession, SessionIdentity
from .experience import TradeSide
from .strategy_v3 import OrderStyle


@dataclass(frozen=True, slots=True)
class OrderIntent:
    strategy_hash: str
    session_fingerprint: str
    symbol: str
    side: TradeSide
    volume: float
    reference_price: float
    stop_price: float
    target_price: float | None
    approved_risk_fraction: float
    allowed_loss: float
    pm_approved: bool
    growth_multiplier: float
    risk_approved: bool
    guardian_approved: bool
    created_at: datetime
    expires_at: datetime
    filling_mode: int
    magic: int = 662075
    max_price_drift_fraction: float = 0.001
    order_style: OrderStyle = OrderStyle.MARKET
    pending_expiry: datetime | None = None
    stop_limit_price: float | None = None

    def __post_init__(self) -> None:
        if not self.strategy_hash.strip() or not self.session_fingerprint.strip() or not self.symbol.strip():
            raise ValueError("order intent requires strategy, session and symbol identity")
        values = (
            self.volume,
            self.reference_price,
            self.stop_price,
            self.approved_risk_fraction,
            self.allowed_loss,
            self.growth_multiplier,
            self.max_price_drift_fraction,
        )
        if self.target_price is not None:
            values += (self.target_price,)
        if self.stop_limit_price is not None:
            values += (self.stop_limit_price,)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("order intent economics must be finite")
        if self.volume <= 0 or self.reference_price <= 0 or self.stop_price <= 0 or self.allowed_loss <= 0:
            raise ValueError("order intent volume/prices/loss budget must be positive")
        if not 0 < self.approved_risk_fraction <= 1 or not 0 <= self.growth_multiplier <= 1:
            raise ValueError("intent risk/growth fractions are invalid")
        if not 0 <= self.max_price_drift_fraction < 1 or self.filling_mode < 0 or self.magic < 1:
            raise ValueError("intent execution controls are invalid")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None or self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("intent timestamps must be timezone-aware")
        if self.expires_at <= self.created_at:
            raise ValueError("intent expiry must follow creation")
        if self.order_style is OrderStyle.MARKET:
            if self.pending_expiry is not None or self.stop_limit_price is not None:
                raise ValueError("market intent cannot carry pending-order fields")
        else:
            if self.pending_expiry is None:
                raise ValueError("pending intent requires order expiration")
            if self.pending_expiry.tzinfo is None or self.pending_expiry.utcoffset() is None:
                raise ValueError("pending expiration must be timezone-aware")
            if self.pending_expiry <= self.created_at:
                raise ValueError("pending expiration must follow creation")
            if self.order_style is OrderStyle.STOP_LIMIT:
                if self.stop_limit_price is None or self.stop_limit_price <= 0:
                    raise ValueError("stop-limit intent requires positive limit price")
                if self.side is TradeSide.LONG and self.stop_limit_price > self.reference_price:
                    raise ValueError("buy stop-limit limit price cannot exceed its stop trigger")
                if self.side is TradeSide.SHORT and self.stop_limit_price < self.reference_price:
                    raise ValueError("sell stop-limit limit price cannot be below its stop trigger")
            elif self.stop_limit_price is not None:
                raise ValueError("stop-limit price belongs only to stop-limit intent")
        if self.side is TradeSide.LONG:
            if self.stop_price >= self.reference_price or (self.target_price is not None and self.target_price <= self.reference_price):
                raise ValueError("long intent stop/target are on the wrong side")
        else:
            if self.stop_price <= self.reference_price or (self.target_price is not None and self.target_price >= self.reference_price):
                raise ValueError("short intent stop/target are on the wrong side")

    @property
    def intent_hash(self) -> str:
        payload = {
            "strategy_hash": self.strategy_hash,
            "session_fingerprint": self.session_fingerprint,
            "symbol": self.symbol,
            "side": self.side.value,
            "volume": self.volume,
            "reference_price": self.reference_price,
            "stop_price": self.stop_price,
            "target_price": self.target_price,
            "approved_risk_fraction": self.approved_risk_fraction,
            "allowed_loss": self.allowed_loss,
            "pm_approved": self.pm_approved,
            "growth_multiplier": self.growth_multiplier,
            "risk_approved": self.risk_approved,
            "guardian_approved": self.guardian_approved,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "filling_mode": self.filling_mode,
            "magic": self.magic,
            "max_price_drift_fraction": self.max_price_drift_fraction,
            "order_style": self.order_style.value,
            "pending_expiry": None if self.pending_expiry is None else self.pending_expiry.isoformat(),
            "stop_limit_price": self.stop_limit_price,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @property
    def client_tag(self) -> str:
        return f"DD-{self.intent_hash[:20]}"


@dataclass(frozen=True, slots=True)
class BrokerPreflight:
    intent: OrderIntent
    passed: bool
    loss_at_stop: float
    required_margin: float
    checked_price: float
    request: tuple[tuple[str, object], ...]
    reasons: tuple[str, ...]

    def request_dict(self) -> dict[str, object]:
        return dict(self.request)


class MT5PreflightAdapter:
    """Broker preflight only. Identity is checked on the same initialized connection used for calculations."""

    def __init__(
        self,
        module: Any,
        session: DemoSession,
        connected_identity_reader: Callable[[], SessionIdentity],
    ) -> None:
        self._mt5 = module
        self._session = session
        self._identity_reader = connected_identity_reader

    @property
    def broker_write_authorized(self) -> bool:
        return False

    def check(self, intent: OrderIntent, *, at: datetime) -> BrokerPreflight:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("preflight timestamp must be timezone-aware")
        reasons: list[str] = []
        if intent.session_fingerprint != self._session.identity.fingerprint:
            reasons.append("session_fingerprint_mismatch")
        if at > intent.expires_at:
            reasons.append("intent_expired")
        if not all((intent.pm_approved, intent.risk_approved, intent.guardian_approved)) or intent.growth_multiplier <= 0:
            reasons.append("governance_not_approved")
        if reasons:
            return BrokerPreflight(intent, False, 0.0, 0.0, 0.0, (), tuple(reasons))

        if not self._mt5.initialize(self._session.identity.terminal_path):
            return BrokerPreflight(intent, False, 0.0, 0.0, 0.0, (), ("mt5_initialize_failed",))
        try:
            verification = self._session.verify(self._identity_reader())
            if not verification.valid:
                return BrokerPreflight(
                    intent,
                    False,
                    0.0,
                    0.0,
                    0.0,
                    (),
                    tuple(f"session_fault:{fault.value}" for fault in verification.faults),
                )
            tick = self._mt5.symbol_info_tick(intent.symbol)
            if tick is None:
                return BrokerPreflight(intent, False, 0.0, 0.0, 0.0, (), ("symbol_tick_unavailable",))
            market_price = float(tick.ask if intent.side is TradeSide.LONG else tick.bid)
            if not math.isfinite(market_price) or market_price <= 0:
                return BrokerPreflight(intent, False, 0.0, 0.0, 0.0, (), ("market_price_invalid",))
            if intent.order_style is OrderStyle.MARKET:
                drift = abs(market_price - intent.reference_price) / intent.reference_price
                if drift > intent.max_price_drift_fraction:
                    return BrokerPreflight(intent, False, 0.0, 0.0, market_price, (), ("price_drift_exceeded",))
                order_type = self._mt5.ORDER_TYPE_BUY if intent.side is TradeSide.LONG else self._mt5.ORDER_TYPE_SELL
                execution_price = market_price
            else:
                try:
                    order_type = _pending_order_type(self._mt5, intent.order_style, intent.side)
                except ValueError:
                    return BrokerPreflight(intent, False, 0.0, 0.0, market_price, (), ("pending_order_type_unsupported",))
                execution_price = intent.reference_price
                if not _pending_geometry_valid(intent, market_price):
                    return BrokerPreflight(intent, False, 0.0, 0.0, market_price, (), ("pending_price_geometry_invalid",))
            calculation_type = self._mt5.ORDER_TYPE_BUY if intent.side is TradeSide.LONG else self._mt5.ORDER_TYPE_SELL
            profit = self._mt5.order_calc_profit(calculation_type, intent.symbol, intent.volume, execution_price, intent.stop_price)
            margin = self._mt5.order_calc_margin(calculation_type, intent.symbol, intent.volume, execution_price)
            if profit is None or margin is None:
                return BrokerPreflight(intent, False, 0.0, 0.0, market_price, (), ("broker_calculation_failed",))
            loss = max(0.0, -float(profit))
            required_margin = float(margin)
            if not math.isfinite(loss) or not math.isfinite(required_margin) or required_margin < 0:
                return BrokerPreflight(intent, False, 0.0, 0.0, market_price, (), ("broker_calculation_invalid",))
            if loss > intent.allowed_loss + 1e-9:
                return BrokerPreflight(intent, False, loss, required_margin, market_price, (), ("broker_loss_exceeds_budget",))
            request = {
                "action": self._mt5.TRADE_ACTION_DEAL if intent.order_style is OrderStyle.MARKET else self._mt5.TRADE_ACTION_PENDING,
                "symbol": intent.symbol,
                "volume": intent.volume,
                "type": order_type,
                "price": execution_price,
                "sl": intent.stop_price,
                "deviation": 20,
                "magic": intent.magic,
                "comment": intent.client_tag,
                "type_time": self._mt5.ORDER_TIME_GTC,
                "type_filling": intent.filling_mode,
            }
            if intent.pending_expiry is not None:
                if not hasattr(self._mt5, "ORDER_TIME_SPECIFIED"):
                    return BrokerPreflight(intent, False, loss, required_margin, market_price, tuple(sorted(request.items())), ("pending_expiration_unsupported",))
                request["type_time"] = self._mt5.ORDER_TIME_SPECIFIED
                request["expiration"] = int(intent.pending_expiry.timestamp())
            if intent.stop_limit_price is not None:
                request["stoplimit"] = intent.stop_limit_price
            if intent.target_price is not None:
                request["tp"] = intent.target_price
            check = self._mt5.order_check(request)
            if check is None:
                return BrokerPreflight(intent, False, loss, required_margin, market_price, tuple(sorted(request.items())), ("order_check_failed",))
            retcode = int(getattr(check, "retcode", -1))
            if retcode != 0:
                return BrokerPreflight(intent, False, loss, required_margin, market_price, tuple(sorted(request.items())), (f"order_check_retcode:{retcode}",))
            return BrokerPreflight(intent, True, loss, required_margin, market_price, tuple(sorted(request.items())), ())
        finally:
            self._mt5.shutdown()


def _pending_order_type(module: Any, style: OrderStyle, side: TradeSide) -> int:
    names = {
        (OrderStyle.LIMIT, TradeSide.LONG): "ORDER_TYPE_BUY_LIMIT",
        (OrderStyle.LIMIT, TradeSide.SHORT): "ORDER_TYPE_SELL_LIMIT",
        (OrderStyle.STOP, TradeSide.LONG): "ORDER_TYPE_BUY_STOP",
        (OrderStyle.STOP, TradeSide.SHORT): "ORDER_TYPE_SELL_STOP",
        (OrderStyle.STOP_LIMIT, TradeSide.LONG): "ORDER_TYPE_BUY_STOP_LIMIT",
        (OrderStyle.STOP_LIMIT, TradeSide.SHORT): "ORDER_TYPE_SELL_STOP_LIMIT",
    }
    name = names.get((style, side))
    if name is None or not hasattr(module, name):
        raise ValueError("broker module does not expose requested pending order type")
    return int(getattr(module, name))


def _pending_geometry_valid(intent: OrderIntent, market_price: float) -> bool:
    if intent.order_style is OrderStyle.LIMIT:
        return intent.reference_price < market_price if intent.side is TradeSide.LONG else intent.reference_price > market_price
    if intent.order_style in {OrderStyle.STOP, OrderStyle.STOP_LIMIT}:
        return intent.reference_price > market_price if intent.side is TradeSide.LONG else intent.reference_price < market_price
    return False
