from __future__ import annotations

"""Read-only M165 native broker-history discovery.

This module answers one question only: can existing MT5 Demo account history
support construction of genuine M165 BrokerExecutionObservation evidence?
It never creates calibration evidence, never sends/changes orders, and never
changes terminal permissions.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Any, Iterable


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _utc_from_millis(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class NativeHistorySymbolDiscovery:
    symbol: str
    execution_deals: int
    distinct_days: int
    buy_deals: int
    sell_deals: int
    matching_orders: int
    complete_cost_fields: int
    requested_fill_pairs: int
    historical_tick_windows_available: int
    historical_tick_windows_checked: int
    history_fingerprint: str

    @property
    def both_sides(self) -> bool:
        return self.buy_deals > 0 and self.sell_deals > 0

    @property
    def payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "execution_deals": self.execution_deals,
            "distinct_days": self.distinct_days,
            "buy_deals": self.buy_deals,
            "sell_deals": self.sell_deals,
            "both_sides": self.both_sides,
            "matching_orders": self.matching_orders,
            "complete_cost_fields": self.complete_cost_fields,
            "requested_fill_pairs": self.requested_fill_pairs,
            "historical_tick_windows_available": self.historical_tick_windows_available,
            "historical_tick_windows_checked": self.historical_tick_windows_checked,
            "history_fingerprint": self.history_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class NativeHistoryDiscoveryReport:
    captured_at: datetime
    window_start: datetime
    window_end: datetime
    demo_account: bool
    connected: bool
    symbols: tuple[NativeHistorySymbolDiscovery, ...]
    read_only: bool = True

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m165-native-history-discovery-v1",
            "captured_at": self.captured_at.isoformat(),
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "demo_account": self.demo_account,
            "connected": self.connected,
            "read_only": self.read_only,
            "authority": {"broker_write": False, "live_write": False, "promotion": False},
            "symbols": [row.payload for row in self.symbols],
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


broker_write_authority = False
live_write_authority = False
promotion_authority = False
risk_override_authority = False


def discover_native_history(
    mt5: Any,
    *,
    symbols: Iterable[str],
    captured_at: datetime,
    lookback_days: int = 90,
    max_rows: int = 10_000,
    tick_windows_per_symbol: int = 50,
) -> NativeHistoryDiscoveryReport:
    """Inspect bounded MT5 history without producing trading/calibration authority."""
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at must be timezone-aware")
    if not 1 <= lookback_days <= 3650:
        raise ValueError("lookback_days out of range")
    if not 1 <= max_rows <= 100_000:
        raise ValueError("max_rows out of range")
    if not 0 <= tick_windows_per_symbol <= 500:
        raise ValueError("tick_windows_per_symbol out of range")

    captured = captured_at.astimezone(timezone.utc)
    start = captured - timedelta(days=lookback_days)
    terminal = mt5.terminal_info()
    account = mt5.account_info()
    if terminal is None or account is None:
        raise RuntimeError("native MT5 terminal/account information unavailable")

    connected = bool(getattr(terminal, "connected", False))
    trade_mode = getattr(account, "trade_mode", None)
    demo_value = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0)
    demo_account = trade_mode == demo_value

    output: list[NativeHistorySymbolDiscovery] = []
    for raw_symbol in sorted({str(value).strip().upper() for value in symbols if str(value).strip()}):
        deals = mt5.history_deals_get(start, captured, group=raw_symbol)
        orders = mt5.history_orders_get(start, captured, group=raw_symbol)
        if deals is None or orders is None:
            raise RuntimeError(f"native history unavailable for {raw_symbol}")
        if len(deals) > max_rows or len(orders) > max_rows:
            raise RuntimeError(f"native history bound exceeded for {raw_symbol}")

        execution = [row for row in deals if getattr(row, "symbol", "").upper() == raw_symbol and getattr(row, "type", None) in (0, 1)]
        order_by_ticket = {
            int(getattr(row, "ticket")): row
            for row in orders
            if isinstance(getattr(row, "ticket", None), int) and not isinstance(getattr(row, "ticket", None), bool)
        }

        days: set[str] = set()
        buy = sell = matching = costs = pairs = 0
        fingerprints: list[str] = []
        tick_candidates: list[datetime] = []

        for row in execution:
            side = getattr(row, "type", None)
            buy += side == 0
            sell += side == 1
            event_time = _utc_from_millis(getattr(row, "time_msc", None))
            if event_time is None:
                seconds = getattr(row, "time", None)
                if _finite_number(seconds):
                    event_time = datetime.fromtimestamp(float(seconds), tz=timezone.utc)
            if event_time is not None:
                days.add(event_time.date().isoformat())
                if len(tick_candidates) < tick_windows_per_symbol:
                    tick_candidates.append(event_time)

            ticket = getattr(row, "order", None)
            order = order_by_ticket.get(int(ticket)) if isinstance(ticket, int) and not isinstance(ticket, bool) else None
            if order is not None:
                matching += 1
            fill = getattr(row, "price", None)
            requested = getattr(order, "price_open", None) if order is not None else None
            if _finite_number(fill) and float(fill) > 0 and _finite_number(requested) and float(requested) > 0:
                pairs += 1
            cost_values = tuple(getattr(row, name, None) for name in ("commission", "fee", "swap"))
            if all(_finite_number(value) for value in cost_values):
                costs += 1
            fingerprints.append(_digest({
                "symbol": raw_symbol,
                "side": side,
                "time_msc": getattr(row, "time_msc", None),
                "volume": getattr(row, "volume", None),
                "fill_price": fill,
                "requested_price": requested,
                "commission": getattr(row, "commission", None),
                "fee": getattr(row, "fee", None),
                "swap": getattr(row, "swap", None),
                "reason": getattr(row, "reason", None),
            }))

        tick_available = 0
        for event_time in tick_candidates:
            tick_from = event_time - timedelta(seconds=2)
            tick_to = event_time + timedelta(seconds=2)
            ticks = mt5.copy_ticks_range(raw_symbol, tick_from, tick_to, getattr(mt5, "COPY_TICKS_ALL", -1))
            if ticks is not None and len(ticks) > 0:
                tick_available += 1

        output.append(NativeHistorySymbolDiscovery(
            raw_symbol,
            len(execution),
            len(days),
            buy,
            sell,
            matching,
            costs,
            pairs,
            tick_available,
            len(tick_candidates),
            _digest(tuple(sorted(fingerprints))),
        ))

    return NativeHistoryDiscoveryReport(captured, start, captured, demo_account, connected, tuple(output))
