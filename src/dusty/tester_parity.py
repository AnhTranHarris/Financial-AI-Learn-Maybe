from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Iterable, Sequence

from .experience import TradeSide
from .features import FeatureBar
from .runtime import RuntimeTrade


class ExpectedExitKind(StrEnum):
    STOP = "stop"
    TARGET = "target"
    TIME = "time"


@dataclass(frozen=True, slots=True)
class TesterDeal:
    strategy_hash: str
    position_id: int
    deal_id: int
    at: datetime
    deal_type: str
    entry_type: str
    volume: float
    price: float
    commission: float
    swap: float
    profit: float
    fee: float
    reason: str
    sl: float
    tp: float
    comment: str

    def __post_init__(self) -> None:
        if not self.strategy_hash.strip() or self.position_id <= 0 or self.deal_id <= 0:
            raise ValueError("tester deal identity is invalid")
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("tester deal timestamp must be timezone-aware")
        if self.deal_type not in {"buy", "sell"}:
            raise ValueError("tester deal type must be buy or sell")
        if self.entry_type not in {"in", "out", "inout", "out_by"}:
            raise ValueError("tester deal entry type is unsupported")
        if any(
            not math.isfinite(v)
            for v in (
                self.volume,
                self.price,
                self.commission,
                self.swap,
                self.profit,
                self.fee,
                self.sl,
                self.tp,
            )
        ):
            raise ValueError("tester deal economics must be finite")
        if self.volume <= 0 or self.price <= 0 or self.sl < 0 or self.tp < 0:
            raise ValueError("tester deal prices/volume are invalid")

    @property
    def cash_effect(self) -> float:
        return self.profit + self.commission + self.swap + self.fee


def parse_tester_deals_csv(text: str) -> tuple[TesterDeal, ...]:
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "strategy_hash",
        "position_id",
        "deal_id",
        "time_msc",
        "deal_type_name",
        "entry_type_name",
        "volume",
        "price",
        "commission",
        "swap",
        "profit",
        "fee",
        "reason_name",
        "sl",
        "tp",
        "comment",
    }
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise ValueError("tester deal CSV is missing semantic parity columns")
    rows = []
    for row in reader:
        rows.append(
            TesterDeal(
                strategy_hash=row["strategy_hash"].strip(),
                position_id=int(row["position_id"]),
                deal_id=int(row["deal_id"]),
                at=datetime.fromtimestamp(
                    int(row["time_msc"]) / 1000.0,
                    tz=timezone.utc,
                ),
                deal_type=row["deal_type_name"].strip().lower(),
                entry_type=row["entry_type_name"].strip().lower(),
                volume=float(row["volume"]),
                price=float(row["price"]),
                commission=float(row["commission"]),
                swap=float(row["swap"]),
                profit=float(row["profit"]),
                fee=float(row["fee"]),
                reason=row["reason_name"].strip().lower(),
                sl=float(row["sl"]),
                tp=float(row["tp"]),
                comment=row["comment"],
            )
        )
    return tuple(sorted(rows, key=lambda item: (item.at, item.deal_id)))


@dataclass(frozen=True, slots=True)
class TesterTrade:
    strategy_hash: str
    trade_id: str
    position_id: int
    side: TradeSide
    volume: float
    entry_at: datetime
    exit_at: datetime
    entry_price: float
    exit_price: float
    net_pnl: float
    exit_reason: str
    initial_sl: float
    initial_tp: float


def normalize_tester_trades(
    deals: Iterable[TesterDeal],
    *,
    volume_tolerance: float = 1e-9,
) -> tuple[TesterTrade, ...]:
    """Normalize the tester EA's deal ledger into one-in/one-out reference trades.

    ``net_pnl`` is the native cash effect: profit + commission + swap + fee across the complete
    position. The current reference laboratory is intentionally single-position and does not claim
    parity for partial fills, reversals, scaling, or close-by operations. Those observations fail
    loudly instead of being coerced into a simple trade.
    """
    if volume_tolerance < 0:
        raise ValueError("volume tolerance cannot be negative")
    grouped: dict[tuple[str, int], list[TesterDeal]] = {}
    for deal in deals:
        grouped.setdefault((deal.strategy_hash, deal.position_id), []).append(deal)
    normalized = []
    seen_trade_ids: set[str] = set()
    for (strategy_hash, position_id), group in sorted(grouped.items()):
        ordered = sorted(group, key=lambda item: (item.at, item.deal_id))
        entries = [item for item in ordered if item.entry_type == "in"]
        exits = [item for item in ordered if item.entry_type == "out"]
        unsupported = [item for item in ordered if item.entry_type not in {"in", "out"}]
        if unsupported or len(entries) != 1 or len(exits) != 1:
            raise ValueError(
                "tester reference parity requires exactly one entry and one exit deal per position"
            )
        entry, exit_deal = entries[0], exits[0]
        if abs(entry.volume - exit_deal.volume) > volume_tolerance:
            raise ValueError("tester reference parity does not support partial-volume exits")
        if exit_deal.at <= entry.at:
            raise ValueError("tester exit must follow entry")
        prefix = "DDT:"
        if not entry.comment.startswith(prefix) or not entry.comment[len(prefix):].strip():
            raise ValueError("tester entry comment does not contain Dusty trade identity")
        trade_id = entry.comment[len(prefix):].strip()
        if trade_id in seen_trade_ids:
            raise ValueError("tester trade ids must be unique")
        seen_trade_ids.add(trade_id)
        side = TradeSide.LONG if entry.deal_type == "buy" else TradeSide.SHORT
        normalized.append(
            TesterTrade(
                strategy_hash=strategy_hash,
                trade_id=trade_id,
                position_id=position_id,
                side=side,
                volume=entry.volume,
                entry_at=entry.at,
                exit_at=exit_deal.at,
                entry_price=entry.price,
                exit_price=exit_deal.price,
                net_pnl=sum(item.cash_effect for item in ordered),
                exit_reason=exit_deal.reason,
                initial_sl=entry.sl,
                initial_tp=entry.tp,
            )
        )
    return tuple(sorted(normalized, key=lambda item: (item.entry_at, item.trade_id)))


@dataclass(frozen=True, slots=True)
class ExpectedExecutionEnvelope:
    strategy_hash: str
    trade_id: str
    side: TradeSide
    volume: float
    entry_signal_at: datetime
    entry_reference_price: float
    exit_not_before: datetime
    exit_not_after: datetime
    exit_kind: ExpectedExitKind
    exit_reference_price: float
    initial_sl: float
    initial_tp: float
    expected_net_pnl: float | None = None

    def __post_init__(self) -> None:
        times = (self.entry_signal_at, self.exit_not_before, self.exit_not_after)
        if any(item.tzinfo is None or item.utcoffset() is None for item in times):
            raise ValueError("parity envelope timestamps must be timezone-aware")
        if self.exit_not_after < self.exit_not_before or self.exit_not_after <= self.entry_signal_at:
            raise ValueError("parity exit window is invalid")
        if any(
            not math.isfinite(v) or v <= 0
            for v in (
                self.volume,
                self.entry_reference_price,
                self.exit_reference_price,
                self.initial_sl,
            )
        ):
            raise ValueError("parity envelope economics must be finite and positive")
        if not math.isfinite(self.initial_tp) or self.initial_tp < 0:
            raise ValueError("parity target must be finite and nonnegative")
        if self.expected_net_pnl is not None and not math.isfinite(self.expected_net_pnl):
            raise ValueError("expected native net PnL must be finite when supplied")


def expected_execution_envelopes(
    trades: Sequence[RuntimeTrade],
    bars: Sequence[FeatureBar],
    *,
    strategy_hash: str,
    trade_ids: Sequence[str],
    volumes: Sequence[float],
    expected_net_pnls: Sequence[float] | None = None,
) -> tuple[ExpectedExecutionEnvelope, ...]:
    if not (len(trades) == len(trade_ids) == len(volumes)):
        raise ValueError("parity expectation inputs must align")
    if expected_net_pnls is not None and len(expected_net_pnls) != len(trades):
        raise ValueError("expected net PnL inputs must align with parity trades")
    pnl_rows: Sequence[float | None]
    if expected_net_pnls is None:
        pnl_rows = (None,) * len(trades)
    else:
        pnl_rows = tuple(float(item) for item in expected_net_pnls)
    by_available = {bar.at: bar for bar in bars}
    result = []
    for trade, trade_id, volume, expected_pnl in zip(
        trades,
        trade_ids,
        volumes,
        pnl_rows,
        strict=True,
    ):
        if trade.strategy_hash != strategy_hash:
            raise ValueError("runtime trade belongs to another strategy")
        exit_bar = by_available.get(trade.exit_at)
        if exit_bar is None:
            raise ValueError("runtime exit has no completed-bar provenance")
        if trade.exit_reason == "stop":
            kind = ExpectedExitKind.STOP
            start = exit_bar.source_open_at
        elif trade.exit_reason == "target":
            kind = ExpectedExitKind.TARGET
            start = exit_bar.source_open_at
        elif trade.exit_reason == "max_hold":
            kind = ExpectedExitKind.TIME
            start = trade.exit_at
        else:
            raise ValueError(
                f"unsupported runtime exit reason for tester parity: {trade.exit_reason}"
            )
        if start is None:
            raise ValueError("MT5 parity requires source-open provenance for exit window")
        result.append(
            ExpectedExecutionEnvelope(
                strategy_hash=strategy_hash,
                trade_id=trade_id,
                side=trade.side,
                volume=float(volume),
                entry_signal_at=trade.entry_at,
                entry_reference_price=trade.entry_price,
                exit_not_before=start,
                exit_not_after=trade.exit_at,
                exit_kind=kind,
                exit_reference_price=trade.exit_price,
                initial_sl=trade.stop_price,
                initial_tp=trade.target_price or 0.0,
                expected_net_pnl=expected_pnl,
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class ExecutionParityAssessment:
    passed: bool
    matched: int
    reasons: tuple[str, ...]


def reconcile_execution_envelopes(
    expected: Iterable[ExpectedExecutionEnvelope],
    observed: Iterable[TesterTrade],
    *,
    max_entry_delay_seconds: float,
    max_entry_price_gap: float,
    max_exit_price_gap: float,
    max_volume_gap: float = 1e-9,
    max_time_exit_delay_seconds: float = 60.0,
    max_net_pnl_gap: float | None = None,
) -> ExecutionParityAssessment:
    """Compare execution semantics, timing, prices and—when requested—native cash economics.

    Price/timing tolerances acknowledge real tester execution rather than fictitious exact bar-close
    fills. Cash parity is opt-in here so low-level callers can inspect execution-only behavior, but the
    M75 native trust qualifier requires it. Its tolerance must be declared before observing the result.
    """
    limits = (
        max_entry_delay_seconds,
        max_entry_price_gap,
        max_exit_price_gap,
        max_volume_gap,
        max_time_exit_delay_seconds,
    )
    if any(not math.isfinite(value) or value < 0 for value in limits):
        raise ValueError("parity tolerances must be finite and nonnegative")
    if max_net_pnl_gap is not None and (
        not math.isfinite(max_net_pnl_gap) or max_net_pnl_gap < 0
    ):
        raise ValueError("net PnL parity tolerance must be finite and nonnegative")
    expected_rows = tuple(expected)
    observed_rows = tuple(observed)
    if len({item.trade_id for item in expected_rows}) != len(expected_rows):
        raise ValueError("expected parity trade ids must be unique")
    if len({item.trade_id for item in observed_rows}) != len(observed_rows):
        raise ValueError("observed parity trade ids must be unique")
    left = {item.trade_id: item for item in expected_rows}
    right = {item.trade_id: item for item in observed_rows}
    reasons: list[str] = []
    matched = 0
    if set(left) != set(right):
        missing = sorted(set(left) - set(right))
        extra = sorted(set(right) - set(left))
        reasons.extend(f"missing_trade:{item}" for item in missing)
        reasons.extend(f"unexpected_trade:{item}" for item in extra)
    for trade_id in sorted(set(left) & set(right)):
        want, got = left[trade_id], right[trade_id]
        prefix = f"trade:{trade_id}:"
        before = len(reasons)
        if want.strategy_hash != got.strategy_hash:
            reasons.append(prefix + "strategy_mismatch")
        if want.side is not got.side:
            reasons.append(prefix + "side_mismatch")
        if abs(want.volume - got.volume) > max_volume_gap:
            reasons.append(prefix + "volume_mismatch")
        if got.entry_at < want.entry_signal_at:
            reasons.append(prefix + "entry_before_signal")
        if got.entry_at > want.entry_signal_at + timedelta(seconds=max_entry_delay_seconds):
            reasons.append(prefix + "entry_delay_exceeded")
        if abs(got.entry_price - want.entry_reference_price) > max_entry_price_gap:
            reasons.append(prefix + "entry_price_gap")
        if abs(got.initial_sl - want.initial_sl) > max_exit_price_gap:
            reasons.append(prefix + "initial_stop_mismatch")
        if want.initial_tp == 0.0:
            if got.initial_tp > max_exit_price_gap:
                reasons.append(prefix + "unexpected_target")
        elif abs(got.initial_tp - want.initial_tp) > max_exit_price_gap:
            reasons.append(prefix + "initial_target_mismatch")

        if want.exit_kind is ExpectedExitKind.STOP:
            if got.exit_reason != "sl":
                reasons.append(prefix + "exit_reason_not_sl")
            if not want.exit_not_before <= got.exit_at <= want.exit_not_after:
                reasons.append(prefix + "stop_exit_outside_bar_window")
        elif want.exit_kind is ExpectedExitKind.TARGET:
            if got.exit_reason != "tp":
                reasons.append(prefix + "exit_reason_not_tp")
            if not want.exit_not_before <= got.exit_at <= want.exit_not_after:
                reasons.append(prefix + "target_exit_outside_bar_window")
        else:
            if got.exit_reason != "expert":
                reasons.append(prefix + "time_exit_not_expert")
            if (
                got.exit_at < want.exit_not_before
                or got.exit_at
                > want.exit_not_after + timedelta(seconds=max_time_exit_delay_seconds)
            ):
                reasons.append(prefix + "time_exit_delay")
        if abs(got.exit_price - want.exit_reference_price) > max_exit_price_gap:
            reasons.append(prefix + "exit_price_gap")

        if max_net_pnl_gap is not None:
            if want.expected_net_pnl is None:
                reasons.append(prefix + "expected_net_pnl_missing")
            elif abs(got.net_pnl - want.expected_net_pnl) > max_net_pnl_gap:
                reasons.append(prefix + "net_pnl_gap")

        if len(reasons) == before:
            matched += 1
    return ExecutionParityAssessment(not reasons, matched, tuple(reasons))
