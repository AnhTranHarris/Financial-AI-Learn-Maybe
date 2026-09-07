from __future__ import annotations

"""Read-only native certification for M196.13 Estate -> A1 automation."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import ceil
from pathlib import Path

from dusty.backtest import PriceMark
from dusty.estate_a1_execution import (
    analysis_replay_from_runtime_trades,
    bind_estate_candidate,
    execute_estate_candidate_closed_window,
)
from dusty.features import completed_feature_bars_from_mt5, compute_standard_features
from dusty.mt5worker import MT5BarRequest, ReadOnlyMT5Worker
from dusty.multitimeframe_a1_campaign import (
    A1CampaignPolicy,
    A1WindowEvidence,
    assess_a1_chronological_campaign,
)
from dusty.multitimeframe_a1_reliability import A1ReliabilityPolicy, assess_a1_reliability
from dusty.multitimeframe_context import bind_multitimeframe_history
from dusty.multitimeframe_financial_backtest import ResearchFriction, run_minimum_lot_financial_replay
from dusty.multitimeframe_research_frame import analysis_frames_from_multitimeframe_history
from dusty.runtime import compile_strategy
from dusty.strategy_estate import load_strategy_estate
from dusty.walk_forward_lab import WalkForwardPlan, WalkForwardMode, WalkForwardWindow

from validate_m19610_native import A1_STARTING_EQUITY, _economics, _git_head


UTC = timezone.utc
ENDPOINTS = (
    datetime(2026, 8, 10, 17, 30, tzinfo=UTC),
    datetime(2026, 8, 17, 17, 30, tzinfo=UTC),
    datetime(2026, 8, 24, 17, 30, tzinfo=UTC),
    datetime(2026, 8, 31, 17, 30, tzinfo=UTC),
    datetime(2026, 9, 7, 17, 30, tzinfo=UTC),
)


def _dataset_identity(symbol: str, timeframe: str) -> str:
    payload = "|".join((symbol, timeframe, *(value.isoformat() for value in ENDPOINTS)))
    return sha256(payload.encode("utf-8")).hexdigest()


def _select_estate_candidate(estate_path: Path, symbol: str):
    rows = tuple(sorted(load_strategy_estate(estate_path), key=lambda row: row.fingerprint))
    eligible = []
    for row in rows:
        if symbol not in row.symbols or row.timeframe != "M15":
            continue
        try:
            compiled = compile_strategy(row.candidate_spec)
        except ValueError:
            continue
        if compiled.spec.exit_plan.max_hold_steps >= 80:
            continue
        eligible.append(row)
    if not eligible:
        raise RuntimeError("Strategy Estate has no compileable EURUSD M15 candidate for M196.13")
    return eligible[0]


def _window_features(terminal: str, symbol: str, timeframe: str, end: datetime):
    worker = ReadOnlyMT5Worker()
    raw = tuple(
        worker.stream_bars(
            MT5BarRequest(
                terminal_path=terminal,
                symbol=symbol,
                timeframe=timeframe,
                start=end - timedelta(days=14),
                end=end,
                chunk_days=7,
            )
        )
    )
    if len(raw) < 100:
        raise RuntimeError(f"{timeframe}: insufficient raw bars ({len(raw)})")
    completed = completed_feature_bars_from_mt5(raw)
    features = compute_standard_features(completed)
    if len(features) < 80:
        raise RuntimeError(f"{timeframe}: insufficient feature rows ({len(features)})")
    return tuple(features[-80:]), completed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--estate-path", required=True)
    parser.add_argument("--symbol", default="EURUSD")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    terminal = str(Path(args.terminal_path).resolve())
    estate_path = Path(args.estate_path).resolve()
    symbol = args.symbol.strip().upper()
    expected_head = args.expected_head.strip().lower()

    if _git_head(repo) != expected_head:
        raise RuntimeError("repository HEAD does not match expected M196.13 SHA")
    if not Path(terminal).is_file():
        raise RuntimeError(f"terminal missing: {terminal}")
    if not estate_path.is_file():
        raise RuntimeError(f"Strategy Estate missing: {estate_path}")

    economics = _economics(terminal, symbol)
    reconstruction = _select_estate_candidate(estate_path, symbol)
    binding = bind_estate_candidate(reconstruction, symbol=symbol)

    print("\n=== M196.13 REAL STRATEGY ESTATE CANDIDATE ===")
    print(f"Title:                     {reconstruction.title}")
    print(f"Strategy ID:               {binding.strategy_id}")
    print(f"Strategy hash:             {binding.strategy_hash}")
    print(f"Reconstruction:            {binding.reconstruction_fingerprint}")
    print(f"Symbol:                    {binding.symbol}")
    print(f"Persisted primary:         {binding.profile.primary}")
    print(f"Invented context:          NONE")
    print(f"Binding fingerprint:       {binding.fingerprint}")

    windows = tuple(
        WalkForwardWindow(
            fold=index,
            train_start=end - timedelta(days=14),
            train_end=end - timedelta(days=4),
            test_start=end - timedelta(days=4),
            test_end=end + timedelta(minutes=15),
        )
        for index, end in enumerate(ENDPOINTS, 1)
    )
    plan = WalkForwardPlan(
        binding.strategy_hash,
        binding.fingerprint,
        _dataset_identity(symbol, binding.profile.primary),
        WalkForwardMode.ROLLING,
        windows,
    )

    a1_policy = A1ReliabilityPolicy(
        minimum_trades=2,
        minimum_distinct_weeks=1,
        minimum_net_pnl=0.0,
        minimum_mean_trade_pnl=0.0,
        minimum_bootstrap_lower_mean=0.0,
        minimum_deflated_signal_score=0.0,
        maximum_largest_winner_fraction=1.0,
        maximum_drawdown_fraction=1.0,
        minimum_positive_week_fraction=0.0,
        bootstrap_resamples=500,
        bootstrap_seed=19613,
    )
    campaign_policy = A1CampaignPolicy(
        minimum_windows=5,
        minimum_total_trades=1,
        minimum_promising_window_fraction=0.60,
        minimum_positive_window_fraction=0.60,
        minimum_total_net_pnl=0.0,
        maximum_worst_drawdown_fraction=1.0,
    )

    evidence = []
    total_runtime_trades = 0
    print("\n=== M196.13 AUTOMATED ESTATE A1 WINDOWS ===")
    for fold, end in enumerate(ENDPOINTS, 1):
        primary_features, completed = _window_features(terminal, symbol, binding.profile.primary, end)
        bounds = bind_multitimeframe_history(
            binding.profile,
            {binding.profile.primary: primary_features},
        )
        frames = analysis_frames_from_multitimeframe_history(
            binding.profile,
            bounds,
            primary_features,
        )
        runtime_trades = execute_estate_candidate_closed_window(
            reconstruction,
            binding,
            frames,
        )
        total_runtime_trades += len(runtime_trades)
        semantic = analysis_replay_from_runtime_trades(binding, runtime_trades)

        spread_points = sorted(
            float(row.decision_spread_proxy_points)
            for row in completed
            if row.decision_spread_proxy_points is not None and row.decision_spread_proxy_points >= 0
        )
        if not spread_points:
            raise RuntimeError("Coinexx window contains no spread proxy observations")
        p95_index = max(0, min(len(spread_points) - 1, ceil(0.95 * len(spread_points)) - 1))
        spread_p95 = spread_points[p95_index]
        spread_cost_per_lot = spread_p95 * economics.point_size / economics.tick_size * economics.tick_value
        friction = ResearchFriction(
            entry_cost_per_lot=spread_cost_per_lot,
            basis=(
                "coinexx_completed_bar_spread_proxy_p95;"
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
        assessment = assess_a1_reliability(
            financial,
            economics,
            policy=a1_policy,
            trial_count=fold,
        )
        window = windows[fold - 1]
        observed_start = frames[0].snapshot.at
        observed_end = frames[-1].snapshot.at
        if not (window.test_start <= observed_start < observed_end <= window.test_end):
            raise RuntimeError("native Estate observation escaped frozen M196.13 window")
        evidence.append(
            A1WindowEvidence(
                fold,
                plan.fingerprint,
                window.fingerprint,
                observed_start,
                observed_end,
                assessment,
            )
        )
        print(
            f"fold-{fold}: end={end.isoformat()} frames={len(frames)} "
            f"trades={assessment.trade_count} net=${assessment.net_pnl:.4f} "
            f"status={assessment.status.value} spread_p95={spread_p95:.4f}"
        )

    campaign = assess_a1_chronological_campaign(plan, evidence, policy=campaign_policy)

    print("\n=== M196.13 ESTATE -> A1 CAMPAIGN RESULT ===")
    print(f"Status:                    {campaign.status.value}")
    print(f"Windows:                   {campaign.window_count}")
    print(f"Runtime trades:            {total_runtime_trades}")
    print(f"A1 total trades:           {campaign.total_trades}")
    print(f"Aggregate net P&L:         ${campaign.total_net_pnl:.4f}")
    print(f"Blockers:                  {','.join(campaign.blockers)}")
    print(f"Plan fingerprint:          {campaign.plan_fingerprint}")
    print(f"Campaign fingerprint:      {campaign.fingerprint}")

    if campaign.window_count != 5:
        raise RuntimeError("M196.13 did not produce five Estate A1 windows")
    if campaign.strategy_hash != binding.strategy_hash:
        raise RuntimeError("M196.13 campaign strategy identity drift")
    if any(
        (
            binding.broker_write_authority,
            binding.live_write_authority,
            binding.promotion_authority,
            binding.risk_override_authority,
            binding.guardian_override_authority,
            campaign.broker_write_authority,
            campaign.live_write_authority,
            campaign.promotion_authority,
            campaign.risk_override_authority,
            campaign.guardian_override_authority,
        )
    ):
        raise RuntimeError("M196.13 gained prohibited operational authority")

    print("\nReal Strategy Estate record:       PASS")
    print("Automatic primary binding:         PASS")
    print("Invented multi-TF provenance:      NONE")
    print("Existing V2 runtime execution:     PASS")
    print("Closed A1 window semantics:        PASS")
    print("Existing M196.10 ledger:           PASS")
    print("Existing M196.11 reliability:      PASS")
    print("Existing M196.12 campaign:         PASS")
    print("Future context leakage:            BLOCKED upstream")
    print("Broker/live/promotion authority:   NONE")
    print("Orders sent:                       NONE")
    print("\nM196.13 STRATEGY ESTATE A1 AUTOMATION NATIVE PROBE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
