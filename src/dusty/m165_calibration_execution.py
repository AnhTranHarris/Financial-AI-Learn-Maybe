from __future__ import annotations

"""Fail-closed binding for one planned M165 Demo calibration entry.

This module is pure validation. It has no MetaTrader5 import, no order_send
surface and no ambient Demo/live authority. A caller must present an exact,
current, content-addressed planning artifact before M187 may be considered.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Mapping

from .risk import RiskConstitution


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha256(value: object, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: object, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires full Git SHA")
    return rendered


def _positive(value: object, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or rendered <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return rendered


def _aware(value: object, label: str) -> datetime:
    parsed = datetime.fromisoformat(str(value)) if not isinstance(value, datetime) else value
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class M165CalibrationExecutionBinding:
    source_commit: str
    plan_fingerprint: str
    qualification_plan_fingerprint: str
    qualification_manifest_fingerprint: str
    strategy_hash: str
    lane_id: str
    session_fingerprint: str
    symbol_spec_fingerprint: str
    symbol: str
    side: str
    intent_hash: str
    volume_lots: float
    reference_price: float
    stop_price: float
    filling_mode: int
    actual_risk_fraction: float
    execution_allowed_loss_cash: float
    discovery_loss_ceiling_cash: float
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "execution source commit"))
        for name in (
            "plan_fingerprint",
            "qualification_plan_fingerprint",
            "qualification_manifest_fingerprint",
            "strategy_hash",
            "session_fingerprint",
            "symbol_spec_fingerprint",
            "intent_hash",
        ):
            object.__setattr__(self, name, _sha256(getattr(self, name), name))
        lane = str(self.lane_id).strip().lower()
        if not lane or "\n" in lane or "\r" in lane:
            raise ValueError("execution lane_id required")
        object.__setattr__(self, "lane_id", lane)
        symbol = str(self.symbol).strip().upper()
        if not symbol or len(symbol) > 64:
            raise ValueError("execution symbol required")
        object.__setattr__(self, "symbol", symbol)
        if self.side != "long":
            raise ValueError("first calibration execution is fixed to long entry")
        for name in (
            "volume_lots",
            "reference_price",
            "stop_price",
            "execution_allowed_loss_cash",
            "discovery_loss_ceiling_cash",
        ):
            object.__setattr__(self, name, _positive(getattr(self, name), name))
        risk = float(self.actual_risk_fraction)
        if not math.isfinite(risk) or not 0 < risk <= RiskConstitution().normal_trade_risk:
            raise ValueError("execution risk exceeds Dusty normal-trade risk")
        object.__setattr__(self, "actual_risk_fraction", risk)
        if self.stop_price >= self.reference_price:
            raise ValueError("long calibration stop must be below reference price")
        if isinstance(self.filling_mode, bool) or int(self.filling_mode) < 0:
            raise ValueError("execution filling mode invalid")
        object.__setattr__(self, "filling_mode", int(self.filling_mode))
        created = _aware(self.created_at, "execution created_at")
        expires = _aware(self.expires_at, "execution expires_at")
        if expires <= created or expires - created > timedelta(minutes=5):
            raise ValueError("execution plan lifetime must be >0 and <=5 minutes")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)
        if self.execution_allowed_loss_cash > self.discovery_loss_ceiling_cash + 1e-9:
            raise ValueError("execution loss budget exceeds discovery ceiling")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m165-calibration-execution-binding-v1",
            "source_commit": self.source_commit,
            "plan_fingerprint": self.plan_fingerprint,
            "qualification_plan_fingerprint": self.qualification_plan_fingerprint,
            "qualification_manifest_fingerprint": self.qualification_manifest_fingerprint,
            "strategy_hash": self.strategy_hash,
            "lane_id": self.lane_id,
            "session_fingerprint": self.session_fingerprint,
            "symbol_spec_fingerprint": self.symbol_spec_fingerprint,
            "symbol": self.symbol,
            "side": self.side,
            "intent_hash": self.intent_hash,
            "volume_lots": self.volume_lots,
            "reference_price": self.reference_price,
            "stop_price": self.stop_price,
            "filling_mode": self.filling_mode,
            "actual_risk_fraction": self.actual_risk_fraction,
            "execution_allowed_loss_cash": self.execution_allowed_loss_cash,
            "discovery_loss_ceiling_cash": self.discovery_loss_ceiling_cash,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "authority": {"broker_write": False, "live_write": False, "promotion": False, "retry": False},
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    retry_authority = False

    def active_at(self, at: datetime) -> bool:
        observed = _aware(at, "execution binding evaluation")
        return self.created_at <= observed <= self.expires_at


def load_calibration_execution_binding(
    payload: Mapping[str, object],
    *,
    expected_source_commit: str,
    now: datetime,
) -> M165CalibrationExecutionBinding:
    expected = _git_sha(expected_source_commit, "expected execution source commit")
    if payload.get("protocol") != "dusty-m165-calibration-roundtrip-plan-v1" or payload.get("status") != "planned":
        raise ValueError("calibration plan is not executable")
    if _git_sha(payload.get("source_commit"), "calibration plan source commit") != expected:
        raise ValueError("calibration plan source commit mismatch")
    authority = payload.get("authority")
    if not isinstance(authority, Mapping):
        raise ValueError("calibration plan authority missing")
    if any(bool(authority.get(name, False)) for name in ("broker_write", "live_write", "promotion", "execution_bridge_invoked")):
        raise ValueError("planning artifact already carries execution authority")
    supplied_fp = _sha256(payload.get("plan_fingerprint"), "calibration plan fingerprint")
    unsigned = dict(payload)
    unsigned.pop("plan_fingerprint", None)
    if _digest(unsigned) != supplied_fp:
        raise ValueError("calibration plan fingerprint mismatch")
    selected = payload.get("selected")
    if not isinstance(selected, Mapping):
        raise ValueError("calibration plan selection missing")
    binding = M165CalibrationExecutionBinding(
        source_commit=expected,
        plan_fingerprint=supplied_fp,
        qualification_plan_fingerprint=payload.get("qualification_plan_fingerprint"),
        qualification_manifest_fingerprint=payload.get("qualification_manifest_fingerprint"),
        strategy_hash=payload.get("strategy_hash"),
        lane_id=str(payload.get("lane_id", "")),
        session_fingerprint=payload.get("session_fingerprint"),
        symbol_spec_fingerprint=payload.get("symbol_spec_fingerprint"),
        symbol=str(payload.get("symbol", "")),
        side=str(payload.get("side", "")),
        intent_hash=selected.get("intent_hash"),
        volume_lots=selected.get("volume_lots"),
        reference_price=selected.get("reference_price"),
        stop_price=selected.get("stop_price"),
        filling_mode=selected.get("filling_mode"),
        actual_risk_fraction=selected.get("actual_risk_fraction"),
        execution_allowed_loss_cash=selected.get("execution_allowed_loss_cash"),
        discovery_loss_ceiling_cash=selected.get("discovery_loss_ceiling_cash"),
        created_at=_aware(selected.get("created_at"), "calibration selected created_at"),
        expires_at=_aware(selected.get("expires_at"), "calibration selected expires_at"),
    )
    observed_loss = _positive(selected.get("loss_at_stop"), "selected loss_at_stop")
    if not math.isclose(observed_loss, binding.execution_allowed_loss_cash, rel_tol=1e-9, abs_tol=1e-9):
        raise ValueError("execution loss budget is not bound to measured native loss")
    if not binding.active_at(now):
        raise PermissionError("calibration execution binding is expired or not yet active")
    return binding
