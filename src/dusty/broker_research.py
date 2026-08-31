from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

from .experience import TradeSide
from .runtime import RuntimeTrade


@dataclass(frozen=True, slots=True)
class BrokerEstimateRequest:
    side: TradeSide
    symbol: str
    volume: float
    entry_price: float
    stop_price: float

    def __post_init__(self) -> None:
        if not self.symbol.strip() or any(
            not math.isfinite(value) or value <= 0
            for value in (self.volume, self.entry_price, self.stop_price)
        ):
            raise ValueError("broker estimate requires symbol and finite positive economics")
        if self.entry_price == self.stop_price:
            raise ValueError("stop must differ from entry")


@dataclass(frozen=True, slots=True)
class BrokerEstimate:
    loss_at_stop: float
    required_margin: float


class MT5ResearchCalculator:
    """Read/preflight-only MetaTrader calculator. It exposes no order_send method."""

    def __init__(self, terminal_path: str, module: Any) -> None:
        if not terminal_path.strip():
            raise ValueError("terminal path is required")
        self.terminal_path = terminal_path
        self._mt5 = module

    @property
    def broker_write_authorized(self) -> bool:
        return False

    def estimate(self, request: BrokerEstimateRequest) -> BrokerEstimate:
        if not self._mt5.initialize(self.terminal_path):
            raise RuntimeError(f"MT5 initialize failed: {self._last_error()}")
        try:
            action = self._mt5.ORDER_TYPE_BUY if request.side is TradeSide.LONG else self._mt5.ORDER_TYPE_SELL
            profit = self._mt5.order_calc_profit(
                action,
                request.symbol,
                request.volume,
                request.entry_price,
                request.stop_price,
            )
            margin = self._mt5.order_calc_margin(
                action,
                request.symbol,
                request.volume,
                request.entry_price,
            )
            if profit is None or margin is None:
                raise RuntimeError(f"MT5 broker calculation failed: {self._last_error()}")
            loss = max(0.0, -float(profit))
            required = float(margin)
            if not math.isfinite(loss) or not math.isfinite(required) or required < 0:
                raise ValueError("MT5 returned invalid broker economics")
            return BrokerEstimate(loss, required)
        finally:
            self._mt5.shutdown()

    def _last_error(self) -> object:
        return self._mt5.last_error() if hasattr(self._mt5, "last_error") else "unknown"


@dataclass(frozen=True, slots=True)
class ResearchManifestRow:
    trade_id: str
    entry_at: datetime
    exit_at: datetime
    side: TradeSide
    volume: float
    stop_price: float
    target_price: float

    def __post_init__(self) -> None:
        if not self.trade_id.strip():
            raise ValueError("research manifest trade id is required")
        if self.entry_at.tzinfo is None or self.entry_at.utcoffset() is None:
            raise ValueError("research manifest entry timestamp must be timezone-aware")
        if self.exit_at.tzinfo is None or self.exit_at.utcoffset() is None:
            raise ValueError("research manifest exit timestamp must be timezone-aware")
        if self.exit_at <= self.entry_at:
            raise ValueError("research manifest exit must follow entry")
        if self.volume <= 0 or self.stop_price <= 0 or self.target_price < 0:
            raise ValueError("research manifest economics are invalid")
        if any(not math.isfinite(value) for value in (self.volume, self.stop_price, self.target_price)):
            raise ValueError("research manifest economics must be finite")


def manifest_rows(
    trades: Iterable[RuntimeTrade],
    *,
    volume: float,
) -> tuple[ResearchManifestRow, ...]:
    """Translate the single Python strategy runtime into a tester execution manifest.

    The Strategy Tester EA consumes only these already-decided actions. It does not
    re-implement indicators or strategy clauses, preventing semantic drift between
    Python research and MQL5 execution mechanics.
    """
    if not math.isfinite(volume) or volume <= 0:
        raise ValueError("research manifest volume must be finite and positive")
    rows = []
    for index, trade in enumerate(trades):
        rows.append(
            ResearchManifestRow(
                trade_id=f"{trade.strategy_hash[:12]}-{index:06d}",
                entry_at=trade.entry_at,
                exit_at=trade.exit_at,
                side=trade.side,
                volume=volume,
                stop_price=trade.stop_price,
                target_price=trade.target_price or 0.0,
            )
        )
    return tuple(rows)


def render_research_manifest(rows: Iterable[ResearchManifestRow]) -> str:
    """Render the tester-only EA manifest in deterministic UTC order."""
    collected = tuple(rows)
    if tuple(sorted(collected, key=lambda row: (row.entry_at, row.trade_id))) != collected:
        raise ValueError("research manifest must be chronological")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("trade_id", "entry_time", "exit_time", "side", "volume", "stop_price", "target_price"))
    for row in collected:
        entry = row.entry_at.astimezone(timezone.utc).strftime("%Y.%m.%d %H:%M:%S")
        exit_at = row.exit_at.astimezone(timezone.utc).strftime("%Y.%m.%d %H:%M:%S")
        writer.writerow(
            (
                row.trade_id,
                entry,
                exit_at,
                row.side.value,
                f"{row.volume:.12g}",
                f"{row.stop_price:.17g}",
                f"{row.target_price:.17g}",
            )
        )
    return buffer.getvalue()


@dataclass(frozen=True, slots=True)
class DealParityRecord:
    strategy_hash: str
    position_id: int
    deal_id: int
    time_msc: int
    deal_type: int
    entry_type: int
    volume: float
    price: float
    commission: float
    swap: float
    profit: float
    reason: int
    comment: str

    def __post_init__(self) -> None:
        if not self.strategy_hash.strip() or self.position_id < 0 or self.deal_id <= 0 or self.time_msc <= 0:
            raise ValueError("deal parity identity is invalid")
        values = (self.volume, self.price, self.commission, self.swap, self.profit)
        if any(not math.isfinite(value) for value in values) or self.volume <= 0 or self.price <= 0:
            raise ValueError("deal parity economics are invalid")


def parse_deal_parity_csv(text: str) -> tuple[DealParityRecord, ...]:
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "strategy_hash",
        "position_id",
        "deal_id",
        "time_msc",
        "deal_type",
        "entry_type",
        "volume",
        "price",
        "commission",
        "swap",
        "profit",
        "reason",
        "comment",
    }
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("MT5 deal parity CSV is missing required columns")
    rows = []
    for row in reader:
        rows.append(
            DealParityRecord(
                strategy_hash=row["strategy_hash"],
                position_id=int(row["position_id"]),
                deal_id=int(row["deal_id"]),
                time_msc=int(row["time_msc"]),
                deal_type=int(row["deal_type"]),
                entry_type=int(row["entry_type"]),
                volume=float(row["volume"]),
                price=float(row["price"]),
                commission=float(row["commission"]),
                swap=float(row["swap"]),
                profit=float(row["profit"]),
                reason=int(row["reason"]),
                comment=row["comment"],
            )
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True)
class TradeParityRecord:
    strategy_hash: str
    trade_id: str
    entry_at: datetime
    exit_at: datetime
    side: TradeSide
    volume: float
    entry_price: float
    exit_price: float
    pnl: float

    def __post_init__(self) -> None:
        if not self.strategy_hash.strip() or not self.trade_id.strip():
            raise ValueError("trade parity records require strategy and trade identity")
        if self.entry_at.tzinfo is None or self.entry_at.utcoffset() is None or self.exit_at.tzinfo is None or self.exit_at.utcoffset() is None:
            raise ValueError("trade parity timestamps must be timezone-aware")
        if self.exit_at <= self.entry_at:
            raise ValueError("trade parity exit must follow entry")
        values = (self.volume, self.entry_price, self.exit_price, self.pnl)
        if any(not math.isfinite(value) for value in values) or self.volume <= 0 or self.entry_price <= 0 or self.exit_price <= 0:
            raise ValueError("trade parity economics must be finite and prices/volume positive")


def parse_trade_parity_csv(text: str) -> tuple[TradeParityRecord, ...]:
    """Parse Dusty's normalized MT5 tester export contract, not broker-specific HTML."""
    reader = csv.DictReader(io.StringIO(text))
    required = {"strategy_hash", "trade_id", "entry_at", "exit_at", "side", "volume", "entry_price", "exit_price", "pnl"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("MT5 parity CSV is missing required columns")
    result = []
    for row in reader:
        result.append(
            TradeParityRecord(
                strategy_hash=row["strategy_hash"],
                trade_id=row["trade_id"],
                entry_at=datetime.fromisoformat(row["entry_at"].replace("Z", "+00:00")),
                exit_at=datetime.fromisoformat(row["exit_at"].replace("Z", "+00:00")),
                side=TradeSide(row["side"].lower()),
                volume=float(row["volume"]),
                entry_price=float(row["entry_price"]),
                exit_price=float(row["exit_price"]),
                pnl=float(row["pnl"]),
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class TradeParityAssessment:
    passed: bool
    matched: int
    reasons: tuple[str, ...]


def reconcile_trade_parity(
    expected: Iterable[TradeParityRecord],
    observed: Iterable[TradeParityRecord],
    *,
    max_time_gap_seconds: float = 1.0,
    max_price_gap: float = 1e-6,
    max_volume_gap: float = 1e-9,
    max_pnl_gap: float = 0.01,
) -> TradeParityAssessment:
    left = tuple(expected)
    right = tuple(observed)
    reasons: list[str] = []
    if len(left) != len(right):
        reasons.append("trade_count_mismatch")
    matched = 0
    for index, (want, got) in enumerate(zip(left, right)):
        prefix = f"trade:{index}:"
        if want.strategy_hash != got.strategy_hash or want.trade_id != got.trade_id:
            reasons.append(prefix + "identity_mismatch")
            continue
        if want.side is not got.side:
            reasons.append(prefix + "side_mismatch")
        if abs((want.entry_at - got.entry_at).total_seconds()) > max_time_gap_seconds or abs((want.exit_at - got.exit_at).total_seconds()) > max_time_gap_seconds:
            reasons.append(prefix + "time_mismatch")
        if abs(want.entry_price - got.entry_price) > max_price_gap or abs(want.exit_price - got.exit_price) > max_price_gap:
            reasons.append(prefix + "price_mismatch")
        if abs(want.volume - got.volume) > max_volume_gap:
            reasons.append(prefix + "volume_mismatch")
        if abs(want.pnl - got.pnl) > max_pnl_gap:
            reasons.append(prefix + "pnl_mismatch")
        if not any(reason.startswith(prefix) for reason in reasons):
            matched += 1
    return TradeParityAssessment(not reasons, matched, tuple(reasons))
