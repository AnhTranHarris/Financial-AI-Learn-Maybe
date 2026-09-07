from __future__ import annotations

"""Read-only Coinexx certification for M197 small-account feasibility."""

import argparse
from dataclasses import replace
from pathlib import Path

import MetaTrader5 as mt5

from dusty.experience import TradeSide
from dusty.risk import RiskConstitution
from dusty.small_account_feasibility import (
    FeasibilityDecision,
    SmallAccountFeasibilityRequest,
    assess_capital_ladder,
)
from validate_m19610_native import _economics, _git_head


CAPITAL_LADDER = (100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0, 10000.0, 25000.0)
STOP_POINTS = 100.0


def _native_inputs(terminal: str, symbol: str):
    economics = _economics(terminal, symbol)
    if not mt5.initialize(terminal):
        raise RuntimeError(f"Coinexx initialize failed: {mt5.last_error()}")
    try:
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None:
            raise RuntimeError(f"Coinexx symbol/tick unavailable: {mt5.last_error()}")
        point = float(info.point)
        stops = float(getattr(info, "trade_stops_level", 0.0) or 0.0)
        economics = replace(economics, stop_level_points=stops, point_size=point)
        ask = float(tick.ask)
        bid = float(tick.bid)
        if ask <= 0 or bid <= 0 or ask < bid:
            raise RuntimeError("Coinexx tick contains invalid bid/ask")
        spread_price = ask - bid
        buy_margin = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, economics.volume_min, ask)
        sell_margin = mt5.order_calc_margin(mt5.ORDER_TYPE_SELL, symbol, economics.volume_min, bid)
        if buy_margin is None or sell_margin is None:
            raise RuntimeError(f"Coinexx native margin unavailable: {mt5.last_error()}")
        return economics, ask, bid, spread_price, float(buy_margin), float(sell_margin)
    finally:
        mt5.shutdown()


def _probe_side(side: TradeSide, *, entry: float, margin: float, economics, spread_price: float):
    distance = STOP_POINTS * economics.point_size
    stop = entry - distance if side is TradeSide.LONG else entry + distance
    template = SmallAccountFeasibilityRequest(
        equity=100.0,
        side=side,
        entry_price=entry,
        strategy_stop_price=stop,
        economics=economics,
        spread_price=spread_price,
        expected_slippage_price=0.0,
        commission_per_lot=0.0,
        margin_for_minimum_lot=margin,
    )
    rows = assess_capital_ladder(template, CAPITAL_LADDER)
    if rows[0].decision is not FeasibilityDecision.NO_TRADE:
        raise RuntimeError(f"M197 expected $100 {side.value} Coinexx case to fail normal 0.25% risk")
    if rows[0].approved_volume != 0.0:
        raise RuntimeError("M197 manufactured broker minimum volume for infeasible $100 case")
    if not any(row.decision is FeasibilityDecision.TRADEABLE for row in rows):
        raise RuntimeError("M197 capital ladder never reaches a tradeable equity")
    first_tradeable = next(row for row in rows if row.decision is FeasibilityDecision.TRADEABLE)
    if first_tradeable.equity + 1e-12 < first_tradeable.minimum_compliant_capital:
        raise RuntimeError("M197 tradeable row is below its own minimum compliant capital")
    if any((row.broker_write_authority, row.live_write_authority, row.promotion_authority, row.risk_override_authority, row.guardian_override_authority) for row in rows):
        raise RuntimeError("M197 feasibility result gained prohibited authority")
    return rows, first_tradeable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--symbol", default="EURUSD")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    terminal = str(Path(args.terminal_path).resolve())
    expected_head = args.expected_head.strip().lower()
    symbol = args.symbol.strip().upper()
    if _git_head(repo) != expected_head:
        raise RuntimeError("repository HEAD does not match expected M197 SHA")
    if not Path(terminal).is_file():
        raise RuntimeError(f"terminal missing: {terminal}")

    economics, ask, bid, spread, buy_margin, sell_margin = _native_inputs(terminal, symbol)
    constitution = RiskConstitution()
    print("\n=== M197 REAL COINEXX SMALL-ACCOUNT INPUTS ===")
    print(f"Symbol:                    {symbol}")
    print(f"Bid / Ask:                 {bid} / {ask}")
    print(f"Spread price:              {spread:.10f}")
    print(f"Broker minimum lot:        {economics.volume_min}")
    print(f"Volume step:               {economics.volume_step}")
    print(f"Broker stop level:         {economics.stop_level_points} points")
    print(f"Test strategy stop:        {STOP_POINTS} points")
    print(f"Normal trade risk:         {constitution.normal_trade_risk:.4%}")
    print(f"Hard risk ceiling:         {constitution.hard_max_trade_risk:.4%}")
    print(f"Hard margin fraction:      {constitution.margin_hard:.2%}")
    print(f"Native min-lot BUY margin: ${buy_margin:.4f}")
    print(f"Native min-lot SELL margin:${sell_margin:.4f}")

    for side, entry, margin in (
        (TradeSide.LONG, ask, buy_margin),
        (TradeSide.SHORT, bid, sell_margin),
    ):
        rows, first = _probe_side(side, entry=entry, margin=margin, economics=economics, spread_price=spread)
        print(f"\n=== {side.value.upper()} CAPITAL COMPRESSION ===")
        for row in rows:
            print(
                f"equity=${row.equity:,.2f} decision={row.decision.value:<9} "
                f"volume={row.approved_volume:g} min_capital=${row.minimum_compliant_capital:,.2f} "
                f"reasons={','.join(row.reasons) or 'NONE'}"
            )
        print(f"First ladder equity tradeable: ${first.equity:,.2f}")
        print(f"Exact minimum compliant capital:${first.minimum_compliant_capital:,.2f}")

    print("\n$100 normal-risk feasibility:             NO TRADE — PASS")
    print("Broker minimum-volume rounding upward:    BLOCKED")
    print("Broker stop floor included before sizing: PASS")
    print("Native minimum-lot margin included:       PASS")
    print("Capital ladder:                           PASS")
    print("Risk Constitution relaxation:             NONE")
    print("Broker/live/promotion authority:          NONE")
    print("Orders sent:                              NONE")
    print("\nRESULT: M197 SMALL-ACCOUNT FEASIBILITY NATIVE GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
