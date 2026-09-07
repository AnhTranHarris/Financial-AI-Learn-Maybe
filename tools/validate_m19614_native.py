from __future__ import annotations

"""Read-only native certification for M196.14 bounded A1 refinement/retest."""

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
from dusty.estate_a1_refinement import materialize_plan_challengers, plan_a1_refinement
from dusty.multitimeframe_a1_campaign import (
    A1CampaignAssessment,
    A1CampaignPolicy,
    A1CampaignStatus,
    A1WindowEvidence,
    assess_a1_chronological_campaign,
)
from dusty.multitimeframe_a1_reliability import A1ReliabilityPolicy, assess_a1_reliability
from dusty.multitimeframe_context import bind_multitimeframe_history
from dusty.multitimeframe_financial_backtest import ResearchFriction, run_minimum_lot_financial_replay
from dusty.multitimeframe_research_frame import analysis_frames_from_multitimeframe_history
from dusty.walk_forward_lab import WalkForwardMode, WalkForwardPlan, WalkForwardWindow

from validate_m19610_native import A1_STARTING_EQUITY, _economics, _git_head
from validate_m19613_native import ENDPOINTS, _select_estate_candidate, _window_features


UTC = timezone.utc
CREATED_AT = datetime(2026, 9, 7, 19, 30, tzinfo=UTC)


def _dataset_identity(symbol: str, timeframe: str, strategy_hash: str) -> str:
    payload = "|".join((symbol, timeframe, strategy_hash, *(value.isoformat() for value in ENDPOINTS)))
    return sha256(payload.encode("utf-8")).hexdigest()


def _policies(seed: int):
    a1 = A1ReliabilityPolicy(
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
        bootstrap_seed=seed,
    )
    campaign = A1CampaignPolicy(
        minimum_windows=5,
        minimum_total_trades=1,
        minimum_promising_window_fraction=0.60,
        minimum_positive_window_fraction=0.60,
        minimum_total_net_pnl=0.0,
        maximum_worst_drawdown_fraction=1.0,
    )
    return a1, campaign


def _run_campaign(
    reconstruction,
    *,
    terminal: str,
    symbol: str,
    economics,
    seed: int,
    label: str,
) -> tuple[A1CampaignAssessment, int]:
    binding = bind_estate_candidate(reconstruction, symbol=symbol)
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
        _dataset_identity(symbol, binding.profile.primary, binding.strategy_hash),
        WalkForwardMode.ROLLING,
        windows,
    )
    a1_policy, campaign_policy = _policies(seed)
    evidence = []
    total_runtime_trades = 0
    print(f"\n=== {label} FIVE-WINDOW A1 RETEST ===")
    for fold, end in enumerate(ENDPOINTS, 1):
        primary_features, completed = _window_features(terminal, symbol, binding.profile.primary, end)
        bounds = bind_multitimeframe_history(binding.profile, {binding.profile.primary: primary_features})
        frames = analysis_frames_from_multitimeframe_history(binding.profile, bounds, primary_features)
        runtime_trades = execute_estate_candidate_closed_window(reconstruction, binding, frames)
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
            raise RuntimeError("M196.14 observation escaped frozen test window")
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
    print(f"{label} campaign status:      {campaign.status.value}")
    print(f"{label} total trades:         {campaign.total_trades}")
    print(f"{label} aggregate net P&L:    ${campaign.total_net_pnl:.4f}")
    print(f"{label} blockers:             {','.join(campaign.blockers)}")
    print(f"{label} campaign fingerprint: {campaign.fingerprint}")
    return campaign, total_runtime_trades


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
        raise RuntimeError("repository HEAD does not match expected M196.14 SHA")
    if not Path(terminal).is_file():
        raise RuntimeError(f"terminal missing: {terminal}")
    if not estate_path.is_file():
        raise RuntimeError(f"Strategy Estate missing: {estate_path}")

    economics = _economics(terminal, symbol)
    parent = _select_estate_candidate(estate_path, symbol)
    print("\n=== M196.14 PARENT ESTATE CANDIDATE ===")
    print(f"Title:                     {parent.title}")
    print(f"Strategy ID:               {parent.candidate_spec.strategy_id}")
    print(f"Strategy hash:             {parent.candidate_spec.strategy_hash}")
    print(f"Reconstruction:            {parent.fingerprint}")

    parent_campaign, parent_runtime_trades = _run_campaign(
        parent,
        terminal=terminal,
        symbol=symbol,
        economics=economics,
        seed=19614,
        label="PARENT",
    )
    if parent_campaign.status is A1CampaignStatus.PROMISING:
        raise RuntimeError("M196.14 native fixture unexpectedly passed A1; bounded failure-refinement path not exercised")

    plan = plan_a1_refinement(parent, parent_campaign, maximum_challengers=1)
    if plan.evolution.action.value != "create_challenger" or len(plan.evolution.challengers) != 1:
        raise RuntimeError("M196.14 parent failure did not produce exactly one bounded native challenger")
    child = materialize_plan_challengers(parent, plan, created_at=CREATED_AT)[0]
    if child.candidate_spec.strategy_hash == parent.candidate_spec.strategy_hash:
        raise RuntimeError("M196.14 child did not change executable strategy identity")

    parent_source = tuple(
        (row.name, row.value)
        for row in parent.rules
        if row.basis.value == "source_declared"
    )
    child_source = tuple(
        (row.name, row.value)
        for row in child.rules
        if row.basis.value == "source_declared"
    )
    if parent_source != child_source:
        raise RuntimeError("M196.14 altered source-declared rules")

    instruction = plan.evolution.challengers[0].instructions[0]
    print("\n=== M196.14 BOUNDED REFINEMENT ===")
    print(f"Evolution action:          {plan.evolution.action.value}")
    print(f"Mutation count:            {len(plan.evolution.challengers[0].instructions)}")
    print(f"Mutation source:           {instruction.source_key}")
    print(f"Mutation value:            {instruction.new_value}")
    print(f"Parent strategy hash:      {parent.candidate_spec.strategy_hash}")
    print(f"Child strategy hash:       {child.candidate_spec.strategy_hash}")
    print(f"Child reconstruction:      {child.fingerprint}")
    print(f"Refinement fingerprint:    {plan.fingerprint}")

    child_campaign, child_runtime_trades = _run_campaign(
        child,
        terminal=terminal,
        symbol=symbol,
        economics=economics,
        seed=1961401,
        label="CHILD",
    )
    if child_campaign.strategy_hash != child.candidate_spec.strategy_hash:
        raise RuntimeError("M196.14 child retest campaign identity drift")
    if child_campaign.window_count != 5:
        raise RuntimeError("M196.14 child was not retested across all five windows")
    if any(
        (
            plan.broker_write_authority,
            plan.live_write_authority,
            plan.promotion_authority,
            plan.risk_override_authority,
            plan.guardian_override_authority,
            child_campaign.broker_write_authority,
            child_campaign.live_write_authority,
            child_campaign.promotion_authority,
            child_campaign.risk_override_authority,
            child_campaign.guardian_override_authority,
        )
    ):
        raise RuntimeError("M196.14 refinement/retest gained prohibited authority")

    print("\n=== M196.14 NATIVE LOOP RESULT ===")
    print(f"Parent status:             {parent_campaign.status.value}")
    print(f"Parent runtime trades:     {parent_runtime_trades}")
    print(f"Child status:              {child_campaign.status.value}")
    print(f"Child runtime trades:      {child_runtime_trades}")
    print(f"Parent net P&L:            ${parent_campaign.total_net_pnl:.4f}")
    print(f"Child net P&L:             ${child_campaign.total_net_pnl:.4f}")
    print("Real Estate parent:                PASS")
    print("A1 failure -> research outcome:    PASS")
    print("Source-declared rules locked:      PASS")
    print("M158 one-variable Challenger:      PASS")
    print("Distinct executable child:         PASS")
    print("Five-window child retest:          PASS")
    print("M196.10/11/12 reuse:               PASS")
    print("Ollama used for mutation:          NO")
    print("Broker/live/promotion authority:   NONE")
    print("Orders sent:                       NONE")
    print("\nM196.14 BOUNDED A1 REFINEMENT/RETEST NATIVE PROBE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
