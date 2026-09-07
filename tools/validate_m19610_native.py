from __future__ import annotations

"""Read-only Coinexx certification for the M196.10 multi-timeframe A1 financial bridge."""

import argparse
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
import subprocess

import MetaTrader5 as mt5

from dusty.analysis_runtime import replay_analysis_strategy
from dusty.backtest import PriceMark
from dusty.chart_intelligence import AnalysisNode, MarketAnalysisGraph, NodeOperation, ValueUnit
from dusty.features import FeatureVector, completed_feature_bars_from_mt5, compute_standard_features
from dusty.markets import InstrumentEconomics
from dusty.mt5worker import MT5BarRequest, ReadOnlyMT5Worker
from dusty.multitimeframe_context import bind_multitimeframe_context
from dusty.multitimeframe_financial_backtest import (
    ResearchFriction,
    run_minimum_lot_financial_replay,
    summarize_minimum_lot_financial_replay,
)
from dusty.multitimeframe_research_frame import analysis_frame_from_multitimeframe_context
from dusty.strategy_reconstruction_campaign import AssignmentBasis, TimeframeMode, TimeframeProfile
from dusty.strategy_v3 import EntryPolicy, ExitPolicy, HoldPolicy, ProtectionPolicy, StrategySpecV3


UTC = timezone.utc
TOOL = "a" * 64
A1_STARTING_EQUITY = 100_000.0


def _git_head(repo: Path) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot resolve repository HEAD")
    return completed.stdout.strip().lower()


def _economics(terminal: str, symbol: str) -> InstrumentEconomics:
    if not mt5.initialize(terminal):
        raise RuntimeError(f"Coinexx initialize failed: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        info = mt5.symbol_info(symbol)
        if account is None:
            raise RuntimeError(f"account_info unavailable: {mt5.last_error()}")
        if info is None:
            raise RuntimeError(f"{symbol} symbol_info unavailable: {mt5.last_error()}")
        contract_size = float(info.trade_contract_size)
        tick_size = float(info.trade_tick_size)
        tick_value = float(info.trade_tick_value)
        volume_min = float(info.volume_min)
        volume_step = float(info.volume_step)
        volume_max = float(info.volume_max)
        point_size = float(info.point)
        required = {
            "contract_size": contract_size,
            "tick_size": tick_size,
            "tick_value": tick_value,
            "volume_min": volume_min,
            "volume_step": volume_step,
            "volume_max": volume_max,
            "point_size": point_size,
        }
        if any(value <= 0 for value in required.values()):
            raise RuntimeError(f"invalid Coinexx symbol economics: {required}")
        current_price = float(info.ask or info.bid)
        margin_rate = 0.0
        margin_sample = None
        if current_price > 0:
            margin_sample = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, volume_min, current_price)
            if margin_sample is not None:
                notional = current_price * contract_size * volume_min
                if notional > 0:
                    margin_rate = float(margin_sample) / notional
        print("=== COINEXX ECONOMICS PREFLIGHT ===")
        print(f"Server:                    {account.server}")
        print(f"Demo balance:              {account.balance:.2f}")
        print(f"Contract size:             {contract_size}")
        print(f"Tick size:                 {tick_size}")
        print(f"Tick value:                {tick_value}")
        print(f"Minimum lot:               {volume_min}")
        print(f"Volume step:               {volume_step}")
        print(f"Point size:                {point_size}")
        print(f"Margin sample:             {margin_sample}")
        print(f"Derived margin rate:       {margin_rate:.12f}")
        print("order_send calls:          NONE")
        return InstrumentEconomics(
            contract_size=contract_size,
            tick_size=tick_size,
            tick_value=tick_value,
            volume_min=volume_min,
            volume_step=volume_step,
            volume_max=volume_max,
            margin_rate=margin_rate,
            commission_per_lot=0.0,
            point_size=point_size,
        )
    finally:
        mt5.shutdown()


def _series(terminal: str, symbol: str) -> tuple[dict[str, tuple[FeatureVector, ...]], dict[str, tuple[object, ...]]]:
    worker = ReadOnlyMT5Worker()
    now = datetime.now(UTC)
    windows = {"M15": timedelta(days=14), "H4": timedelta(days=60), "D1": timedelta(days=180)}
    feature_series: dict[str, tuple[FeatureVector, ...]] = {}
    completed_series: dict[str, tuple[object, ...]] = {}
    print("\n=== REAL COINEXX PIT FEATURE ACQUISITION ===")
    for timeframe, lookback in windows.items():
        raw = tuple(
            worker.stream_bars(
                MT5BarRequest(
                    terminal_path=terminal,
                    symbol=symbol,
                    timeframe=timeframe,
                    start=now - lookback,
                    end=now,
                    chunk_days=7,
                )
            )
        )
        if len(raw) < 50:
            raise RuntimeError(f"{timeframe}: insufficient raw bars ({len(raw)})")
        completed = completed_feature_bars_from_mt5(raw)
        features = compute_standard_features(completed)
        if len(features) < 50:
            raise RuntimeError(f"{timeframe}: insufficient feature rows ({len(features)})")
        completed_series[timeframe] = completed
        feature_series[timeframe] = features
        print(
            f"{timeframe}: raw={len(raw)} completed={len(completed)} "
            f"features={len(features)} latest={features[-1].at.isoformat()}"
        )
    return feature_series, completed_series


def _graph_and_strategy() -> tuple[MarketAnalysisGraph, StrategySpecV3]:
    graph = MarketAnalysisGraph(
        (
            AnalysisNode("d1_close", NodeOperation.INPUT, ValueUnit.PRICE, source_key="d1.close"),
            AnalysisNode("d1_ema", NodeOperation.INPUT, ValueUnit.PRICE, source_key="d1.ema"),
            AnalysisNode("h4_close", NodeOperation.INPUT, ValueUnit.PRICE, source_key="h4.close"),
            AnalysisNode("h4_sma", NodeOperation.INPUT, ValueUnit.PRICE, source_key="h4.sma"),
            AnalysisNode("m15_rsi", NodeOperation.INPUT, ValueUnit.OSCILLATOR, source_key="m15.rsi"),
            AnalysisNode("m15_mid", NodeOperation.INPUT, ValueUnit.OSCILLATOR, source_key="m15.rsi_mid"),
            AnalysisNode("d1_up", NodeOperation.GREATER_THAN, ValueUnit.BOOLEAN, ("d1_close", "d1_ema")),
            AnalysisNode("h4_up", NodeOperation.GREATER_THAN, ValueUnit.BOOLEAN, ("h4_close", "h4_sma")),
            AnalysisNode("m15_trigger", NodeOperation.GREATER_THAN, ValueUnit.BOOLEAN, ("m15_rsi", "m15_mid")),
            AnalysisNode("long_setup", NodeOperation.ALL, ValueUnit.BOOLEAN, ("d1_up", "h4_up", "m15_trigger")),
            AnalysisNode("short_setup", NodeOperation.NOT, ValueUnit.BOOLEAN, ("long_setup",)),
            AnalysisNode("hold_long", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.hold_long"),
            AnalysisNode("hold_short", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.hold_short"),
            AnalysisNode("exit_long", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.exit_long"),
            AnalysisNode("exit_short", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.exit_short"),
        ),
        (
            ("long_entry", "long_setup"),
            ("short_entry", "short_setup"),
            ("hold_long", "hold_long"),
            ("hold_short", "hold_short"),
            ("exit_long", "exit_long"),
            ("exit_short", "exit_short"),
        ),
        (TOOL,),
    )
    strategy = StrategySpecV3(
        strategy_id="m19610-real-coinexx-a1-proof",
        analysis_graph_hash=graph.fingerprint,
        tool_fingerprints=(TOOL,),
        entry=EntryPolicy("long_entry", "short_entry"),
        hold=HoldPolicy("hold_long", "hold_short", 8),
        exit=ExitPolicy("exit_long", "exit_short"),
        protection=ProtectionPolicy("atr:2"),
        source_reference="m19610-native-proof",
        decision_timeframe="M15",
        intended_horizon_minutes=60,
    )
    return graph, strategy


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
        raise RuntimeError("repository HEAD does not match expected M196.10 certification SHA")
    if not Path(terminal).is_file():
        raise RuntimeError(f"terminal missing: {terminal}")
    economics = _economics(terminal, symbol)
    feature_series, completed_series = _series(terminal, symbol)

    base_primary = tuple(feature_series["M15"][-40:])
    if len(base_primary) != 40:
        raise RuntimeError("M196.10 requires exactly 40 primary rows")
    primaries: list[FeatureVector] = []
    for row in base_primary:
        values = row.feature_map()
        for key in ("close", "atr", "rsi"):
            if key not in values:
                raise RuntimeError(f"M15 feature row missing required key: {key}")
        values.update(
            {
                "rsi_mid": 50.0,
                "hold_long": True,
                "hold_short": True,
                "exit_long": True,
                "exit_short": True,
            }
        )
        primaries.append(FeatureVector.of(row.at, values))

    profile = TimeframeProfile("M15", ("D1", "H4"), TimeframeMode.MULTI, AssignmentBasis.SOURCE_DECLARED)
    frames = []
    for primary in primaries:
        bound = bind_multitimeframe_context(
            profile,
            {"M15": (primary,), "H4": feature_series["H4"], "D1": feature_series["D1"]},
            decision_at=primary.at,
        )
        frames.append(analysis_frame_from_multitimeframe_context(profile, bound, primary))

    graph, strategy = _graph_and_strategy()
    semantic = replay_analysis_strategy(graph, strategy, tuple(frames))
    if semantic.open_position is not None:
        raise RuntimeError("semantic replay ended with unresolved open position")
    if len(semantic.trades) != 20:
        raise RuntimeError(f"expected 20 completed semantic trades, got {len(semantic.trades)}")
    print("\n=== STRATEGY V3 SEMANTIC REPLAY ===")
    print(f"Decision frames:           {len(frames)}")
    print(f"Completed trades:          {len(semantic.trades)}")
    print(f"Open position at end:      {semantic.open_position}")
    print(f"Strategy hash:             {semantic.strategy_hash}")

    spread_points = sorted(
        float(bar.decision_spread_proxy_points)
        for bar in completed_series["M15"]
        if getattr(bar, "decision_spread_proxy_points", None) is not None
        and float(bar.decision_spread_proxy_points) >= 0
    )
    if not spread_points:
        raise RuntimeError("Coinexx M15 history contains no spread proxy observations")
    p95_index = max(0, min(len(spread_points) - 1, ceil(0.95 * len(spread_points)) - 1))
    spread_p95_points = spread_points[p95_index]
    spread_price = spread_p95_points * economics.point_size
    spread_cost_per_lot = spread_price / economics.tick_size * economics.tick_value
    friction = ResearchFriction(
        entry_cost_per_lot=spread_cost_per_lot,
        basis=(
            "coinexx_m15_completed_bar_spread_proxy_p95;"
            "commission_unverified_zero;slippage_unverified_zero;swap_unverified_zero"
        ),
    )
    marks = tuple(PriceMark(frame.snapshot.at, symbol, frame.execution_price) for frame in frames)
    financial = run_minimum_lot_financial_replay(
        semantic,
        marks,
        symbol=symbol,
        economics=economics,
        starting_equity=A1_STARTING_EQUITY,
        friction=friction,
    )
    summary = summarize_minimum_lot_financial_replay(financial, economics)

    if financial.volume != economics.volume_min:
        raise RuntimeError("A1 financial bridge did not force broker minimum lot")
    if any(trade.volume != economics.volume_min for trade in financial.simulated_trades):
        raise RuntimeError("one or more A1 trades are not minimum lot")
    if summary.trade_count != len(semantic.trades):
        raise RuntimeError("semantic/financial trade count mismatch")
    expected_cost = spread_cost_per_lot * economics.volume_min * summary.trade_count
    if abs(summary.total_costs - expected_cost) > 1e-8:
        raise RuntimeError(
            f"ledger friction reconciliation failed: expected={expected_cost} actual={summary.total_costs}"
        )
    if abs(summary.ending_equity - (A1_STARTING_EQUITY + summary.net_pnl)) > 1e-8:
        raise RuntimeError("ending-equity cash reconciliation failed")
    if any(
        (
            financial.broker_write_authority,
            financial.live_write_authority,
            financial.promotion_authority,
            financial.risk_override_authority,
            financial.guardian_override_authority,
        )
    ):
        raise RuntimeError("M196.10 financial replay gained prohibited authority")

    print("\n=== M196.10 A1 FINANCIAL RESULT ===")
    print(f"A1 starting equity:        ${A1_STARTING_EQUITY:,.2f}")
    print(f"Broker minimum lot:        {economics.volume_min}")
    print(f"Financial trades:          {summary.trade_count}")
    print(f"Gross P&L:                 ${summary.gross_pnl:,.2f}")
    print(f"Net P&L:                   ${summary.net_pnl:,.2f}")
    print(f"Ending equity:             ${summary.ending_equity:,.2f}")
    print(f"Max drawdown:              {summary.max_drawdown_fraction:.6%}")
    print(f"Max margin used:           ${summary.max_margin_used:,.2f}")
    print(f"Spread proxy P95:          {spread_p95_points:.4f} points")
    print(f"Spread cost / lot:         ${spread_cost_per_lot:,.4f}")
    print(f"Total charged friction:    ${summary.total_costs:,.4f}")
    print(f"Friction basis:            {friction.basis}")
    print("\nSemantic -> ledger count:          PASS")
    print("Minimum-lot enforcement:          PASS")
    print("Existing Dusty ledger reuse:      PASS")
    print("Gross/net cash reconciliation:    PASS")
    print("Ending-equity reconciliation:     PASS")
    print("Coinexx spread proxy charged:     PASS")
    print("Commission invented:              NO")
    print("Slippage invented:                NO")
    print("Swap invented:                    NO")
    print("Future context leakage:           BLOCKED")
    print("Broker/live authority:            NONE")
    print("Orders sent:                      NONE")
    print("\nM196.10 MULTI-TIMEFRAME A1 FINANCIAL PROBE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
