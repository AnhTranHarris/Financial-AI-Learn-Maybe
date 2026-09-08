from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Any, Mapping

from .broker_calibration import BrokerExecutionObservation, TradeSide


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def broker_profile_fingerprint(*, server: str, currency: str, leverage: float, symbol: str, symbol_spec_fingerprint: str) -> str:
    rendered = str(symbol_spec_fingerprint).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError("symbol spec fingerprint requires SHA-256")
    if not str(server).strip() or not str(currency).strip() or not str(symbol).strip() or float(leverage) <= 0:
        raise ValueError("complete broker profile identity required")
    return _digest(("dusty-m165-broker-profile-v1", str(server).strip(), str(currency).strip().upper(), float(leverage), str(symbol).strip().upper(), rendered))


def utc_from_millis(value: int) -> datetime:
    if isinstance(value, bool) or int(value) <= 0:
        raise ValueError("positive millisecond timestamp required")
    return datetime.fromtimestamp(int(value) / 1000.0, tz=timezone.utc)


def build_observation(
    *,
    broker_profile: str,
    symbol: str,
    side: TradeSide,
    time_msc: int,
    point_size: float,
    bid: float,
    ask: float,
    requested_price: float,
    deal: Mapping[str, Any],
    evidence: object,
) -> BrokerExecutionObservation:
    fill_price = float(deal.get("price", 0.0) or 0.0)
    volume = float(deal.get("volume", 0.0) or 0.0)
    commission = float(deal.get("commission", 0.0) or 0.0)
    fee = float(deal.get("fee", 0.0) or 0.0)
    swap = float(deal.get("swap", 0.0) or 0.0)
    values = (point_size, bid, ask, requested_price, fill_price, volume, commission, fee, swap)
    if any(not math.isfinite(float(value)) for value in values):
        raise ValueError("observation economics must be finite")
    evidence_fp = _digest(("dusty-m165-native-observation-evidence-v1", evidence))
    return BrokerExecutionObservation(
        broker_profile_fingerprint=broker_profile,
        symbol=symbol,
        side=side,
        observed_at=utc_from_millis(time_msc),
        point_size=point_size,
        bid=bid,
        ask=ask,
        requested_price=requested_price,
        fill_price=fill_price,
        volume_lots=volume,
        commission=commission,
        fee=fee,
        swap=swap,
        evidence_fingerprint=evidence_fp,
    )
