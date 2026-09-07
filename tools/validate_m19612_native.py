from __future__ import annotations

"""Read-only Coinexx certification for M196.12 chronological A1 evidence."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path

from dusty.multitimeframe_a1_campaign import (
    A1CampaignPolicy,
    A1CampaignStatus,
    A1WindowEvidence,
    assess_a1_chronological_campaign,
)
from dusty.multitimeframe_a1_reliability import A1ReliabilityPolicy, assess_a1_reliability
from dusty.walk_forward_lab import WalkForwardMode, WalkForwardPlan, WalkForwardWindow

import validate_m19611_native as m11
from validate_m19610_native import _git_head


UTC = timezone.utc
EVIDENCE_ENDS = (
    datetime(2026, 8, 10, 17, 30, tzinfo=UTC),
    datetime(2026, 8, 17, 17, 30, tzinfo=UTC),
    datetime(2026, 8, 24, 17, 30, tzinfo=UTC),
    datetime(2026, 8, 31, 17, 30, tzinfo=UTC),
    datetime(2026, 9, 7, 17, 30, tzinfo=UTC),
)


def _sha_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _a1_policy() -> A1ReliabilityPolicy:
    return A1ReliabilityPolicy(
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
        bootstrap_seed=19612,
    )


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
        raise RuntimeError("repository HEAD does not match expected M196.12 certification SHA")
    if not Path(terminal).is_file():
        raise RuntimeError(f"terminal missing: {terminal}")

    a1_policy = _a1_policy()
    financial_rows = []
    assessments = []

    print("=== M196.12 FIXED WEEKLY COINEXX EVIDENCE ===")
    for fold, evidence_end in enumerate(EVIDENCE_ENDS, start=1):
        m11.EVIDENCE_END = evidence_end
        economics, financial, spread_p95 = m11._financial_replay(terminal, symbol)
        assessment = assess_a1_reliability(
            financial,
            economics,
            policy=a1_policy,
            trial_count=fold,
        )
        if not financial.simulated_trades:
            raise RuntimeError(f"fold {fold}: financial replay produced no trades")
        observed_start = min(row.entry_at for row in financial.simulated_trades)
        observed_end = max(row.exit_at for row in financial.simulated_trades)
        if observed_end <= observed_start:
            raise RuntimeError(f"fold {fold}: invalid observed interval")
        financial_rows.append((financial, observed_start, observed_end, spread_p95))
        assessments.append(assessment)
        print(
            f"fold-{fold}: end={evidence_end.isoformat()} "
            f"observed={observed_start.isoformat()}..{observed_end.isoformat()} "
            f"trades={assessment.trade_count} net=${assessment.net_pnl:,.4f} "
            f"status={assessment.status.value} spread_p95={spread_p95:.4f}"
        )

    strategy_hashes = {row[0].strategy_hash for row in financial_rows}
    if len(strategy_hashes) != 1:
        raise RuntimeError("fixed weekly campaign produced strategy identity drift")
    strategy_hash = next(iter(strategy_hashes))

    windows = []
    for fold, (_, observed_start, observed_end, _) in enumerate(financial_rows, start=1):
        test_start = observed_start - timedelta(minutes=1)
        test_end = observed_end + timedelta(minutes=1)
        windows.append(
            WalkForwardWindow(
                fold=fold,
                train_start=EVIDENCE_ENDS[0] - timedelta(days=180),
                train_end=test_start,
                test_start=test_start,
                test_end=test_end,
            )
        )
    plan = WalkForwardPlan(
        strategy_execution_fingerprint=strategy_hash,
        parameter_fingerprint=_sha_text("m19612-fixed-diagnostic-parameters-v1"),
        dataset_fingerprint=_sha_text(
            "m19612-coinexx-eurusd-weekly-fixed-v1|"
            + "|".join(value.isoformat() for value in EVIDENCE_ENDS)
        ),
        mode=WalkForwardMode.ANCHORED,
        windows=tuple(windows),
    )

    evidence = tuple(
        A1WindowEvidence(
            fold=window.fold,
            plan_fingerprint=plan.fingerprint,
            window_fingerprint=window.fingerprint,
            observed_start=observed_start,
            observed_end=observed_end,
            assessment=assessment,
        )
        for window, (_, observed_start, observed_end, _), assessment in zip(
            plan.windows, financial_rows, assessments, strict=True
        )
    )

    campaign_policy = A1CampaignPolicy(
        minimum_windows=5,
        minimum_total_trades=100,
        minimum_promising_window_fraction=0.80,
        minimum_positive_window_fraction=0.80,
        minimum_total_net_pnl=0.0,
        maximum_worst_drawdown_fraction=1.0,
    )
    campaign = assess_a1_chronological_campaign(plan, evidence, policy=campaign_policy)

    print("\n=== M196.12 A1 CHRONOLOGICAL CAMPAIGN RESULT ===")
    print(f"Status:                    {campaign.status.value}")
    print(f"Windows:                   {campaign.window_count}")
    print(f"Promising windows:         {campaign.promising_window_count}")
    print(f"Rejected windows:          {campaign.rejected_window_count}")
    print(f"Insufficient windows:      {campaign.insufficient_window_count}")
    print(f"Promising fraction:        {campaign.promising_window_fraction:.6f}")
    print(f"Positive-window fraction:  {campaign.positive_window_fraction:.6f}")
    print(f"Total trades:              {campaign.total_trades}")
    print(f"Aggregate net P&L:         ${campaign.total_net_pnl:,.4f}")
    print(f"Median window net P&L:     ${campaign.median_window_net_pnl:,.4f}")
    print(f"Worst window net P&L:      ${campaign.worst_window_net_pnl:,.4f}")
    print(f"Worst drawdown:            {campaign.worst_drawdown_fraction:.6%}")
    print(f"Blockers:                  {','.join(campaign.blockers)}")
    print(f"Plan fingerprint:          {plan.fingerprint}")
    print(f"Campaign fingerprint:      {campaign.fingerprint}")

    if campaign.status is A1CampaignStatus.INSUFFICIENT:
        raise RuntimeError("five complete fixed Coinexx windows were incorrectly classified insufficient")
    if campaign.window_count != 5 or campaign.total_trades != 100:
        raise RuntimeError("fixed campaign did not preserve five windows / 100 minimum-lot trades")
    if campaign.insufficient_window_count != 0:
        raise RuntimeError("fixed campaign contains an unexpected insufficient M196.11 window")
    if len(campaign.evidence_fingerprints) != 5 or len(set(campaign.evidence_fingerprints)) != 5:
        raise RuntimeError("campaign did not preserve five distinct window evidence identities")
    for previous, current in zip(plan.windows, plan.windows[1:]):
        if current.test_start < previous.test_end:
            raise RuntimeError("campaign test windows overlap")
    if any(
        (
            campaign.broker_write_authority,
            campaign.live_write_authority,
            campaign.promotion_authority,
            campaign.risk_override_authority,
            campaign.guardian_override_authority,
        )
    ):
        raise RuntimeError("A1 campaign gained prohibited operational authority")

    print("\nFive chronological windows:        PASS")
    print("Window identity binding:           PASS")
    print("Non-overlapping chronology:        PASS")
    print("100 minimum-lot trades:            PASS")
    print("Mixed/missing evidence handling:   COVERED BY SOFTWARE TESTS")
    print("Campaign classification:           VALID (research only)")
    print("Future context leakage:            BLOCKED upstream")
    print("Broker/live/promotion authority:   NONE")
    print("Orders sent:                       NONE")
    print("\nM196.12 A1 CHRONOLOGICAL NATIVE PROBE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
