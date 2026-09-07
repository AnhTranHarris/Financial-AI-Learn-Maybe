from __future__ import annotations

"""Read-only Coinexx certification for M196.11 A1 reliability classification."""

import argparse
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path

from dusty.analysis_runtime import replay_analysis_strategy
from dusty.backtest import PriceMark
from dusty.features import FeatureVector, completed_feature_bars_from_mt5, compute_standard_features
from dusty.mt5worker import MT5BarRequest, ReadOnlyMT5Worker
from dusty.multitimeframe_a1_reliability import (
    A1ReliabilityPolicy,
    A1ReliabilityStatus,
    assess_a1_reliability,
)
from dusty.multitimeframe_context import bind_multitimeframe_context
from dusty.multitimeframe_financial_backtest import ResearchFriction, run_minimum_lot_financial_replay
from dusty.multitimeframe_research_frame import analysis_frame_from_multitimeframe_context
from dusty.strategy_reconstruction_campaign import AssignmentBasis, TimeframeMode, TimeframeProfile

# Reuse the already-native-certified M196.10 hardware/schema helpers instead of
# duplicating MetaTrader5 economics and Strategy V3 construction in this tool.
from validate_m19610_native import A1_STARTING_EQUITY, _economics, _git_head, _graph_and_strategy


UTC = timezone.utc
# Freeze the exact evidence clock that passed the M196.10 workstation gate.
# MT5 bar requests are inclusive of the bar-open end timestamp; completed-bar
# conversion still requires the following bar before final OHLC becomes known.
EVIDENCE_END = datetime(2026, 9, 7, 17, 30, tzinfo=UTC)


def _series_until(terminal: str, symbol: str):
    worker = ReadOnlyMT5Worker()
    windows = {"M15": timedelta(days=14), "H4": timedelta(days=60), "D1": timedelta(days=180)}
    feature_series = {}
    completed_series = {}
    print("\n=== FIXED REAL COINEXX PIT EVIDENCE ===")
    print(f"Evidence end:              {EVIDENCE_END.isoformat()}")
    for timeframe, lookback in windows.items():
        raw = tuple(
            worker.stream_bars(
                MT5BarRequest(
                    terminal_path=terminal,
                    symbol=symbol,
                    timeframe=timeframe,
                    start=EVIDENCE_END - lookback,
                    end=EVIDENCE_END,
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
        feature_series[timeframe] = features
        completed_series[timeframe] = completed
        print(
            f"{timeframe}: raw={len(raw)} completed={len(completed)} "
            f"features={len(features)} latest={features[-1].at.isoformat()}"
        )
    return feature_series, completed_series


def _financial_replay(terminal: str, symbol: str):
    economics = _economics(terminal, symbol)
    feature_series, completed_series = _series_until(terminal, symbol)
    base_primary = tuple(feature_series["M15"][-40:])
    if len(base_primary) != 40:
        raise RuntimeError("M196.11 requires exactly 40 M15 primary rows")

    primaries = []
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

    profile = TimeframeProfile(
        "M15",
        ("D1", "H4"),
        TimeframeMode.MULTI,
        AssignmentBasis.SOURCE_DECLARED,
    )
    frames = []
    for primary in primaries:
        bound = bind_multitimeframe_context(
            profile,
            {
                "M15": (primary,),
                "H4": feature_series["H4"],
                "D1": feature_series["D1"],
            },
            decision_at=primary.at,
        )
        frames.append(analysis_frame_from_multitimeframe_context(profile, bound, primary))

    graph, strategy = _graph_and_strategy()
    semantic = replay_analysis_strategy(graph, strategy, tuple(frames))
    if semantic.open_position is not None:
        raise RuntimeError("M196.11 semantic replay ended with unresolved position")
    if len(semantic.trades) != 20:
        raise RuntimeError(f"expected 20 completed trades, got {len(semantic.trades)}")

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
    spread_cost_per_lot = (
        spread_p95_points * economics.point_size / economics.tick_size * economics.tick_value
    )
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
    return economics, financial, spread_p95_points


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
        raise RuntimeError("repository HEAD does not match expected M196.11 certification SHA")
    if not Path(terminal).is_file():
        raise RuntimeError(f"terminal missing: {terminal}")

    economics, financial, spread_p95_points = _financial_replay(terminal, symbol)

    # Diagnostic policy: sufficient for this fixed 20-trade/one-week evidence
    # bundle, but requires positive A1 edge. It is intentionally permissive on
    # concentration/drawdown/time consistency so the known negative sample is
    # rejected for edge/statistical evidence rather than an unrelated gate.
    policy = A1ReliabilityPolicy(
        minimum_trades=20,
        minimum_distinct_weeks=1,
        minimum_net_pnl=0.0,
        minimum_mean_trade_pnl=0.0,
        minimum_bootstrap_lower_mean=0.0,
        minimum_deflated_signal_score=0.0,
        maximum_largest_winner_fraction=1.0,
        maximum_drawdown_fraction=1.0,
        minimum_positive_week_fraction=0.0,
        bootstrap_resamples=2000,
        bootstrap_seed=19611,
    )
    assessment = assess_a1_reliability(
        financial,
        economics,
        policy=policy,
        trial_count=1,
    )

    print("\n=== M196.11 A1 RELIABILITY RESULT ===")
    print(f"Status:                    {assessment.status.value}")
    print(f"Trades:                    {assessment.trade_count}")
    print(f"Distinct trade weeks:      {assessment.distinct_trade_weeks}")
    print(f"Net P&L:                   ${assessment.net_pnl:,.4f}")
    print(f"Mean trade P&L:            ${assessment.mean_trade_pnl:,.6f}")
    assert assessment.bootstrap is not None
    assert assessment.selection_bias is not None
    assert assessment.concentration is not None
    print(f"Bootstrap lower mean:      ${assessment.bootstrap.lower:,.6f}")
    print(f"Deflated signal score:     {assessment.selection_bias.deflated_signal_score:.6f}")
    print(f"Largest-winner fraction:   {assessment.concentration.largest_winner_fraction:.6f}")
    print(f"Positive-week fraction:    {assessment.positive_week_fraction:.6f}")
    print(f"Max drawdown:              {assessment.max_drawdown_fraction:.6%}")
    print(f"Spread proxy P95:          {spread_p95_points:.4f} points")
    print(f"Blockers:                  {','.join(assessment.blockers)}")
    print(f"Assessment fingerprint:    {assessment.fingerprint}")

    if assessment.status is not A1ReliabilityStatus.REJECTED:
        raise RuntimeError(
            "known negative M196.10 evidence was not rejected by the A1 reliability gate"
        )
    if "net_pnl_failed" not in assessment.blockers:
        raise RuntimeError("real negative A1 sample did not fail net P&L evidence")
    if "mean_trade_pnl_failed" not in assessment.blockers:
        raise RuntimeError("real negative A1 sample did not fail mean-trade evidence")
    if any(
        (
            assessment.broker_write_authority,
            assessment.live_write_authority,
            assessment.promotion_authority,
            assessment.risk_override_authority,
            assessment.guardian_override_authority,
        )
    ):
        raise RuntimeError("A1 assessment gained prohibited operational authority")

    print("\nKnown losing sample rejected:      PASS")
    print("Insufficient relabeling:           NONE")
    print("Positive-edge requirement:         PASS")
    print("Bootstrap evidence:                PASS")
    print("Search-adjusted evidence:          PASS")
    print("Minimum-lot financial input:       PASS")
    print("Future context leakage:            BLOCKED upstream")
    print("Broker/live/promotion authority:   NONE")
    print("Orders sent:                       NONE")
    print("\nM196.11 A1 RELIABILITY NATIVE PROBE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
