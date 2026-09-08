from __future__ import annotations

import argparse
from datetime import timedelta
import json
from pathlib import Path

from dusty.broker_calibration import BrokerCalibrationPolicy, TradeSide, calibrate_broker_economics
from dusty.m165_native_observations import broker_profile_fingerprint, build_observation, utc_from_millis
from dusty.m194_native_demo_preflight import capture_native_demo_snapshot


def _field(row: object, name: str, default: object = 0) -> object:
    """Read named fields from MT5 NumPy structured rows, dicts, or test doubles."""
    if isinstance(row, dict):
        return row.get(name, default)
    try:
        return row[name]  # type: ignore[index]
    except (KeyError, IndexError, TypeError, ValueError):
        return getattr(row, name, default)


def _nearest_tick(rows: object, target_msc: int) -> object:
    # MetaTrader5.copy_ticks_range returns a NumPy structured ndarray.  It cannot
    # be truth-tested when multi-row, and its named fields are not guaranteed to
    # be accessible as attributes.  Keep only complete, non-crossed quote rows.
    if rows is None:
        values: list[object] = []
    else:
        values = list(rows)
    quotes = []
    for row in values:
        try:
            bid = float(_field(row, "bid", 0.0) or 0.0)
            ask = float(_field(row, "ask", 0.0) or 0.0)
            time_msc = int(_field(row, "time_msc", 0) or 0)
        except (TypeError, ValueError):
            continue
        if time_msc > 0 and bid > 0 and ask > 0 and ask >= bid:
            quotes.append(row)
    if not quotes:
        raise RuntimeError("no complete historical bid/ask ticks available around execution")
    return min(quotes, key=lambda row: abs(int(_field(row, "time_msc", 0) or 0) - target_msc))


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M165 observation extraction from preserved receipt and exact broker forensics")
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--forensics", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    receipt = json.loads(Path(args.receipt).read_text(encoding="utf-8"))
    forensic = json.loads(Path(args.forensics).read_text(encoding="utf-8"))
    if forensic.get("assessment") != "broker_execution_evidence_found":
        raise RuntimeError("forensics do not prove broker execution")

    order_ticket = int(forensic["order_ticket"])
    order_rows = forensic["queries"]["history_orders_by_ticket"]["rows"]
    if len(order_rows) != 1:
        raise RuntimeError("exact entry order evidence must contain one row")
    position_id = int(order_rows[0]["position_id"])
    deal_rows = forensic["queries"].get(f"history_deals_by_position_{position_id}", {}).get("rows", [])
    if len(deal_rows) < 2:
        raise RuntimeError("complete entry/exit deal evidence required")
    entry_deals = [row for row in deal_rows if int(row.get("entry", -1)) == 0 and int(row.get("order", 0)) == order_ticket]
    exit_deals = [row for row in deal_rows if int(row.get("entry", -1)) in (1, 2, 3)]
    if len(entry_deals) != 1 or not exit_deals:
        raise RuntimeError("unambiguous entry and exit deals required")
    entry = entry_deals[0]
    exit_row = exit_deals[-1]

    import MetaTrader5 as mt5
    snapshot = capture_native_demo_snapshot(mt5, terminal_path=args.terminal_path, symbol=str(forensic["symbol"]))
    if snapshot.account_mode.value != "demo":
        raise PermissionError("DEMO account required")
    if not mt5.initialize(path=args.terminal_path):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        spec = mt5.symbol_info(snapshot.symbol)
        if spec is None:
            raise RuntimeError("symbol_info unavailable")
        point = float(getattr(spec, "point", 0.0) or 0.0)
        if point <= 0:
            raise RuntimeError("invalid symbol point size")

        exit_time = utc_from_millis(int(exit_row["time_msc"]))
        ticks = mt5.copy_ticks_range(snapshot.symbol, exit_time - timedelta(seconds=2), exit_time + timedelta(seconds=2), mt5.COPY_TICKS_ALL)
        tick = _nearest_tick(ticks, int(exit_row["time_msc"]))
        exit_bid = float(_field(tick, "bid", 0.0) or 0.0)
        exit_ask = float(_field(tick, "ask", 0.0) or 0.0)
        if exit_bid <= 0 or exit_ask <= 0 or exit_ask < exit_bid:
            raise RuntimeError("historical exit quote is incomplete or crossed")
    finally:
        mt5.shutdown()

    broker = broker_profile_fingerprint(
        server=snapshot.server,
        currency=snapshot.account_currency,
        leverage=snapshot.leverage,
        symbol=snapshot.symbol,
        symbol_spec_fingerprint=snapshot.symbol_spec_fingerprint,
    )
    preflight = receipt.get("entry_preflight", {})
    entry_obs = build_observation(
        broker_profile=broker,
        symbol=snapshot.symbol,
        side=TradeSide.BUY,
        time_msc=int(entry["time_msc"]),
        point_size=point,
        bid=float(preflight["bid"]),
        ask=float(preflight["ask"]),
        requested_price=float(preflight["request_price"]),
        deal=entry,
        evidence={"forensic_fingerprint": forensic["forensic_fingerprint"], "deal": entry},
    )
    exit_order_rows = forensic["queries"].get(f"history_orders_by_position_{position_id}", {}).get("rows", [])
    exit_order = next((row for row in exit_order_rows if int(row.get("ticket", 0)) == int(exit_row["order"])), None)
    if exit_order is None:
        raise RuntimeError("protective exit order evidence missing")
    exit_requested = float(exit_order.get("price_open", 0.0) or exit_order.get("price_current", 0.0) or 0.0)
    exit_obs = build_observation(
        broker_profile=broker,
        symbol=snapshot.symbol,
        side=TradeSide.SELL,
        time_msc=int(exit_row["time_msc"]),
        point_size=point,
        bid=exit_bid,
        ask=exit_ask,
        requested_price=exit_requested,
        deal=exit_row,
        evidence={"forensic_fingerprint": forensic["forensic_fingerprint"], "deal": exit_row, "exit_order": exit_order},
    )

    calibration = calibrate_broker_economics(
        (entry_obs, exit_obs),
        broker_profile_fingerprint=broker,
        symbol=snapshot.symbol,
        policy=BrokerCalibrationPolicy(),
    )
    payload = {
        "protocol": "dusty-m165-native-observation-extraction-v1",
        "broker_profile_fingerprint": broker,
        "symbol": snapshot.symbol,
        "observations": [
            {"fingerprint": row.fingerprint, "side": row.side.value, "observed_at": row.observed_at.isoformat(), "point_size": row.point_size, "bid": row.bid, "ask": row.ask, "requested_price": row.requested_price, "fill_price": row.fill_price, "volume_lots": row.volume_lots, "commission": row.commission, "fee": row.fee, "swap": row.swap, "spread_points": row.spread_points, "adverse_slippage_points": row.adverse_slippage_points, "commission_fee_per_lot": row.commission_fee_per_lot}
            for row in (entry_obs, exit_obs)
        ],
        "calibration": calibration.payload,
        "authority": {"broker_write": False, "live_write": False, "retry": False, "promotion": False},
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
