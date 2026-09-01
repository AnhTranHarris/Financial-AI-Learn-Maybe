from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, Callable

from .demo_session import DemoSession, SessionIdentity
from .execution_lifecycle import ExecutionState
from .experience import TradeSide


class PositionActionKind(StrEnum):
    TIGHTEN_STOP = "tighten_stop"
    PARTIAL_CLOSE = "partial_close"
    FULL_CLOSE = "full_close"
    CANCEL_PENDING = "cancel_pending"


@dataclass(frozen=True, slots=True)
class PositionActionIntent:
    strategy_hash: str
    session_fingerprint: str
    kind: PositionActionKind
    symbol: str
    side: TradeSide
    position_ticket: int
    pending_order_ticket: int
    current_volume: float
    action_volume: float
    current_stop: float
    new_stop: float
    target_price: float
    pm_approved: bool
    risk_approved: bool
    guardian_approved: bool
    created_at: datetime
    expires_at: datetime
    filling_mode: int
    magic: int = 662085

    def __post_init__(self) -> None:
        if not self.strategy_hash.strip() or not self.session_fingerprint.strip() or not self.symbol.strip():
            raise ValueError("position action requires strategy, session and symbol identity")
        values = (self.current_volume, self.action_volume, self.current_stop, self.new_stop, self.target_price)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("position action economics must be finite and nonnegative")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None or self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("position action timestamps must be timezone-aware")
        if self.expires_at <= self.created_at or self.filling_mode < 0 or self.magic < 1:
            raise ValueError("position action execution controls are invalid")
        if self.kind is PositionActionKind.CANCEL_PENDING:
            if self.pending_order_ticket <= 0 or self.position_ticket != 0:
                raise ValueError("pending cancellation requires only a pending-order ticket")
            if any(value != 0 for value in values):
                raise ValueError("pending cancellation cannot carry position economics")
            return
        if self.position_ticket <= 0 or self.pending_order_ticket != 0 or self.current_volume <= 0:
            raise ValueError("position action requires only a position ticket and current volume")
        if self.kind is PositionActionKind.TIGHTEN_STOP:
            if self.action_volume != 0 or self.new_stop <= 0:
                raise ValueError("stop action requires new stop and zero action volume")
            if self.current_stop > 0:
                if self.side is TradeSide.LONG and self.new_stop < self.current_stop:
                    raise ValueError("long protective stop cannot widen")
                if self.side is TradeSide.SHORT and self.new_stop > self.current_stop:
                    raise ValueError("short protective stop cannot widen")
        elif self.kind is PositionActionKind.PARTIAL_CLOSE:
            if not 0 < self.action_volume < self.current_volume or self.new_stop != 0:
                raise ValueError("partial close volume must be below current volume")
        elif self.kind is PositionActionKind.FULL_CLOSE:
            if not math.isclose(self.action_volume, self.current_volume, rel_tol=1e-12, abs_tol=1e-12) or self.new_stop != 0:
                raise ValueError("full close must close the complete current volume")

    @property
    def intent_hash(self) -> str:
        payload = {
            "strategy_hash": self.strategy_hash,
            "session_fingerprint": self.session_fingerprint,
            "kind": self.kind.value,
            "symbol": self.symbol,
            "side": self.side.value,
            "position_ticket": self.position_ticket,
            "pending_order_ticket": self.pending_order_ticket,
            "current_volume": self.current_volume,
            "action_volume": self.action_volume,
            "current_stop": self.current_stop,
            "new_stop": self.new_stop,
            "target_price": self.target_price,
            "pm_approved": self.pm_approved,
            "risk_approved": self.risk_approved,
            "guardian_approved": self.guardian_approved,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "filling_mode": self.filling_mode,
            "magic": self.magic,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    @property
    def client_tag(self) -> str:
        return f"DD-A-{self.intent_hash[:18]}"


@dataclass(frozen=True, slots=True)
class PositionActionPreflight:
    intent: PositionActionIntent
    passed: bool
    request: tuple[tuple[str, object], ...]
    reasons: tuple[str, ...]
    success_state: ExecutionState

    def request_dict(self) -> dict[str, object]:
        return dict(self.request)


class MT5PositionActionPreflightAdapter:
    """Build and broker-check governed position actions; exposes no send method."""

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

    def check(self, intent: PositionActionIntent, *, at: datetime) -> PositionActionPreflight:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("position-action preflight time must be timezone-aware")
        success_state = _success_state(intent.kind)
        reasons: list[str] = []
        if intent.session_fingerprint != self._session.identity.fingerprint:
            reasons.append("session_fingerprint_mismatch")
        if at > intent.expires_at:
            reasons.append("intent_expired")
        if not all((intent.pm_approved, intent.risk_approved, intent.guardian_approved)):
            reasons.append("governance_not_approved")
        if reasons:
            return PositionActionPreflight(intent, False, (), tuple(reasons), success_state)
        if not self._mt5.initialize(self._session.identity.terminal_path):
            return PositionActionPreflight(intent, False, (), ("mt5_initialize_failed",), success_state)
        try:
            verification = self._session.verify(self._identity_reader())
            if not verification.valid:
                return PositionActionPreflight(
                    intent,
                    False,
                    (),
                    tuple(f"session_fault:{fault.value}" for fault in verification.faults),
                    success_state,
                )
            request = _request(intent, self._mt5)
            check = self._mt5.order_check(request)
            if check is None:
                return PositionActionPreflight(intent, False, tuple(sorted(request.items())), ("order_check_failed",), success_state)
            retcode = int(getattr(check, "retcode", -1))
            if retcode != 0:
                return PositionActionPreflight(intent, False, tuple(sorted(request.items())), (f"order_check_retcode:{retcode}",), success_state)
            return PositionActionPreflight(intent, True, tuple(sorted(request.items())), (), success_state)
        except (AttributeError, ValueError) as exc:
            return PositionActionPreflight(intent, False, (), (f"unsupported_position_action:{type(exc).__name__}",), success_state)
        finally:
            self._mt5.shutdown()


def _request(intent: PositionActionIntent, module: Any) -> dict[str, object]:
    if intent.kind is PositionActionKind.TIGHTEN_STOP:
        request: dict[str, object] = {
            "action": module.TRADE_ACTION_SLTP,
            "position": intent.position_ticket,
            "symbol": intent.symbol,
            "sl": intent.new_stop,
            "magic": intent.magic,
            "comment": intent.client_tag,
        }
        if intent.target_price > 0:
            request["tp"] = intent.target_price
        return request
    if intent.kind is PositionActionKind.CANCEL_PENDING:
        return {
            "action": module.TRADE_ACTION_REMOVE,
            "order": intent.pending_order_ticket,
            "symbol": intent.symbol,
            "magic": intent.magic,
            "comment": intent.client_tag,
        }
    tick = module.symbol_info_tick(intent.symbol)
    if tick is None:
        raise ValueError("symbol tick unavailable")
    if intent.side is TradeSide.LONG:
        order_type = module.ORDER_TYPE_SELL
        price = float(tick.bid)
    else:
        order_type = module.ORDER_TYPE_BUY
        price = float(tick.ask)
    if not math.isfinite(price) or price <= 0:
        raise ValueError("close price invalid")
    return {
        "action": module.TRADE_ACTION_DEAL,
        "position": intent.position_ticket,
        "symbol": intent.symbol,
        "volume": intent.action_volume,
        "type": order_type,
        "price": price,
        "magic": intent.magic,
        "comment": intent.client_tag,
        "type_time": module.ORDER_TIME_GTC,
        "type_filling": intent.filling_mode,
    }


def _success_state(kind: PositionActionKind) -> ExecutionState:
    if kind is PositionActionKind.TIGHTEN_STOP:
        return ExecutionState.PROTECTED
    if kind is PositionActionKind.PARTIAL_CLOSE:
        return ExecutionState.PARTIAL
    return ExecutionState.CLOSED
