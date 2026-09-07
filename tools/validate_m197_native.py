from __future__ import annotations

"""Read-only Coinexx certification for M197 capital compression."""

import argparse
from dataclasses import replace
from pathlib import Path

import MetaTrader5 as mt5

from dusty.experience import TradeSide
from dusty.risk import RiskConstitution
from dusty.small_account_feasibility import (
    CapitalBacktestEvidence,
    CapitalCompressionStatus,
    FeasibilityDecision,
    SmallAccountFeasibilityRequest,
    assess_small_account_feasibility,
    capital_compression_equities,
    run_capital_compression_campaign,
)
from validate_m19610_native import _economics, _git_head


STARTING_EQUITY = 100_000.0
REDUCTION_FRACTION = 0.25
TARGET_EQUITY = 100.0
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
    base = SmallAccountFeasibilityRequest(
        equity=STARTING_EQUITY,
        side=side,
        entry_price=entry,
        strategy_stop_price=stop,
        economics=economics,
        spread_price=spread_price,
        expected_slippage_price=0.0,
        commission_per_lot=0.0,
        margin_for_minimum_lot=margin,
    )

    schedule = capital_compression_equities(
        starting_equity=STARTING_EQUITY,
        reduction_fraction=REDUCTION_FRACTION,
        target_equity=TARGET_EQUITY,
    )
    feasibility_rows = []

    def evaluate(equity: float) -> CapitalBacktestEvidence:
        row = assess_small_account_feasibility(replace(base, equity=equity))
        feasibility_rows.append(row)
        return CapitalBacktestEvidence(
            equity=equity,
            passed=row.decision is FeasibilityDecision.TRADEABLE,
            evidence_fingerprint=row.fingerprint,
            reasons=row.reasons,
        )

    campaign = run_capital_compression_campaign(
        evaluate,
        starting_equity=STARTING_EQUITY,
        reduction_fraction=REDUCTION_FRACTION,
        target_equity=TARGET_EQUITY,
    )

    if campaign.status is CapitalCompressionStatus.REFERENCE_FAILED:
        raise RuntimeError(f"M197 $100,000 {side.value} reference is not broker/risk feasible")
    if campaign.status is not CapitalCompressionStatus.MINIMUM_BOUND_FOUND:
        raise RuntimeError("M197 real Coinexx probe expected to locate a minimum-capital bound")
    if campaign.lowest_passing_equity is None or campaign.first_failing_equity is None:
        raise RuntimeError("M197 did not preserve both sides of the measured capital bound")
    if campaign.first_failing_equity >= campaign.lowest_passing_equity:
        raise RuntimeError("M197 capital bound is not strictly descending")
    if tuple(row.equity for row in feasibility_rows) != schedule[: len(feasibility_rows)]:
        raise RuntimeError("M197 native campaign did not follow the exact 25% compression schedule")

    first_failed_row = feasibility_rows[-1]
    previous_row = feasibility_rows[-2]
    if first_failed_row.decision is not FeasibilityDecision.NO_TRADE:
        raise RuntimeError("M197 campaign did not stop at the first infeasible capital rerun")
    if previous_row.decision is not FeasibilityDecision.TRADEABLE:
        raise RuntimeError("M197 campaign lost the last successful capital rerun")
    if first_failed_row.approved_volume != 0.0:
        raise RuntimeError("M197 manufactured broker minimum volume below the compliant capital floor")
    if previous_row.equity + 1e-12 < previous_row.minimum_compliant_capital:
        raise RuntimeError("M197 last successful rerun is below exact compliant capital")
    if first_failed_row.equity + 1e-12 >= first_failed_row.minimum_compliant_capital:
        raise RuntimeError("M197 first failed rerun does not actually cross the measured capital floor")

    # Important: test each Boolean value, not the row tuple itself.  A non-empty
    # tuple is truthy even when all contained flags are False.
    if any(
        any((
            row.broker_write_authority,
            row.live_write_authority,
            row.promotion_authority,
            row.risk_override_authority,
            row.guardian_override_authority,
        ))
        for row in feasibility_rows
    ):
        raise RuntimeError("M197 feasibility result gained prohibited authority")
    if any((
        campaign.broker_write_authority,
        campaign.live_write_authority,
        campaign.promotion_authority,
        campaign.risk_override_authority,
        campaign.guardian_override_authority,
    )):
        raise RuntimeError("M197 compression campaign gained prohibited authority")

    return campaign, tuple(feasibility_rows)


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
    print("\n=== M197 REAL COINEXX CAPITAL-COMPRESSION INPUTS ===")
    print(f"Symbol:                    {symbol}")
    print(f"Bid / Ask:                 {bid} / {ask}")
    print(f"Spread price:              {spread:.10f}")
    print(f"Broker minimum lot:        {economics.volume_min}")
    print(f"Volume step:               {economics.volume_step}")
    print(f"Broker stop level:         {economics.stop_level_points} points")
    print(f"Test strategy stop:        {STOP_POINTS} points")
    print(f"Reference capital:         ${STARTING_EQUITY:,.2f}")
    print(f"Reduction each rerun:      {REDUCTION_FRACTION:.0%}")
    print(f"Target proof level:        below ${TARGET_EQUITY:,.2f}")
    print(f"Normal trade risk:         {constitution.normal_trade_risk:.4%}")
    print(f"Hard risk ceiling:         {constitution.hard_max_trade_risk:.4%}")
    print(f"Hard margin fraction:      {constitution.margin_hard:.2%}")
    print(f"Native min-lot BUY margin: ${buy_margin:.4f}")
    print(f"Native min-lot SELL margin:${sell_margin:.4f}")

    for side, entry, margin in (
        (TradeSide.LONG, ask, buy_margin),
        (TradeSide.SHORT, bid, sell_margin),
    ):
        campaign, rows = _probe_side(side, entry=entry, margin=margin, economics=economics, spread_price=spread)
        print(f"\n=== {side.value.upper()} 25% CAPITAL COMPRESSION ===")
        for index, row in enumerate(rows, start=1):
            print(
                f"run={index:02d} equity=${row.equity:,.2f} decision={row.decision.value:<9} "
                f"volume={row.approved_volume:g} exact_min=${row.minimum_compliant_capital:,.2f} "
                f"reasons={','.join(row.reasons) or 'NONE'}"
            )
        print(f"Compression status:        {campaign.status.value}")
        print(f"Lowest passing rerun:      ${campaign.lowest_passing_equity:,.2f}")
        print(f"First failing rerun:       ${campaign.first_failing_equity:,.2f}")
        print(f"Exact compliant floor:     ${rows[-1].minimum_compliant_capital:,.2f}")
        print(f"Campaign fingerprint:      {campaign.fingerprint}")

    print("\n$100k reference feasibility:              PASS")
    print("25% sequential capital reduction:         PASS")
    print("Stop at first capital/risk infeasibility: PASS")
    print("Exact compliant floor preserved:          PASS")
    print("Broker minimum-volume rounding upward:    BLOCKED")
    print("Broker stop floor included before sizing: PASS")
    print("Native minimum-lot margin included:       PASS")
    print("Risk Constitution relaxation:             NONE")
    print("Broker/live/promotion authority:          NONE")
    print("Orders sent:                              NONE")
    print("\nRESULT: M197 CAPITAL-COMPRESSION NATIVE GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
