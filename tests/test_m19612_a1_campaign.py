from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from dusty.analysis_runtime import AnalysisReplay, AnalysisReplayTrade
from dusty.backtest import PriceMark
from dusty.experience import TradeSide
from dusty.markets import InstrumentEconomics
from dusty.multitimeframe_a1_campaign import (
    A1CampaignPolicy,
    A1CampaignStatus,
    A1WindowEvidence,
    assess_a1_chronological_campaign,
)
from dusty.multitimeframe_a1_reliability import A1ReliabilityPolicy, assess_a1_reliability
from dusty.multitimeframe_financial_backtest import run_minimum_lot_financial_replay
from dusty.walk_forward_lab import WalkForwardMode, WalkForwardPlan, WalkForwardWindow


UTC = timezone.utc
STRATEGY = "a" * 64
PARAMS = "b" * 64
DATASET = "c" * 64
BASE = datetime(2026, 1, 5, tzinfo=UTC)


def economics() -> InstrumentEconomics:
    return InstrumentEconomics(
        contract_size=100_000.0,
        tick_size=0.00001,
        tick_value=1.0,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
        margin_rate=0.01,
        point_size=0.00001,
    )


def a1_policy(**changes) -> A1ReliabilityPolicy:
    values = {
        "minimum_trades": 4,
        "minimum_distinct_weeks": 1,
        "minimum_net_pnl": 0.0,
        "minimum_mean_trade_pnl": 0.0,
        "minimum_bootstrap_lower_mean": 0.0,
        "minimum_deflated_signal_score": 0.0,
        "maximum_largest_winner_fraction": 1.0,
        "maximum_drawdown_fraction": 1.0,
        "minimum_positive_week_fraction": 0.0,
        "bootstrap_resamples": 500,
        "bootstrap_seed": 19612,
    }
    values.update(changes)
    return A1ReliabilityPolicy(**values)


def campaign_policy(**changes) -> A1CampaignPolicy:
    values = {
        "minimum_windows": 4,
        "minimum_total_trades": 16,
        "minimum_promising_window_fraction": 0.75,
        "minimum_positive_window_fraction": 0.75,
        "minimum_total_net_pnl": 0.0,
        "maximum_worst_drawdown_fraction": 0.10,
    }
    values.update(changes)
    return A1CampaignPolicy(**values)


def plan(count: int = 4) -> WalkForwardPlan:
    windows = []
    for fold in range(1, count + 1):
        test_start = BASE + timedelta(days=7 * fold)
        windows.append(
            WalkForwardWindow(
                fold=fold,
                train_start=BASE,
                train_end=test_start,
                test_start=test_start,
                test_end=test_start + timedelta(days=1),
            )
        )
    return WalkForwardPlan(STRATEGY, PARAMS, DATASET, WalkForwardMode.ANCHORED, tuple(windows))


def assessment_for(window: WalkForwardWindow, pnls: tuple[float, ...], *, policy=None):
    econ = economics()
    rows = []
    marks = []
    entry = 1.1000
    for index, pnl in enumerate(pnls):
        at = window.test_start + timedelta(hours=1, minutes=index * 10)
        exit_at = at + timedelta(minutes=5)
        move = pnl / (econ.tick_value * econ.volume_min) * econ.tick_size
        exit_price = entry + move
        rows.append(AnalysisReplayTrade(TradeSide.LONG, at, exit_at, entry, exit_price, 1.0, "exit"))
        marks.extend((PriceMark(at, "EURUSD", entry), PriceMark(exit_at, "EURUSD", exit_price)))
    semantic = AnalysisReplay(STRATEGY, "d" * 64, (), tuple(rows), None)
    financial = run_minimum_lot_financial_replay(
        semantic,
        tuple(marks),
        symbol="EURUSD",
        economics=econ,
        starting_equity=100_000.0,
    )
    return assess_a1_reliability(financial, econ, policy=policy or a1_policy(), trial_count=1)


def evidence_for(p: WalkForwardPlan, outcomes: tuple[tuple[float, ...], ...], *, policy=None):
    rows = []
    for window, pnls in zip(p.windows, outcomes, strict=True):
        assessment = assessment_for(window, pnls, policy=policy)
        rows.append(
            A1WindowEvidence(
                fold=window.fold,
                plan_fingerprint=p.fingerprint,
                window_fingerprint=window.fingerprint,
                observed_start=window.test_start + timedelta(hours=1),
                observed_end=window.test_start + timedelta(hours=2),
                assessment=assessment,
            )
        )
    return tuple(rows)


POS = (1.0, 1.1, 0.9, 1.2)
NEG = (-1.0, -0.5, 0.2, -0.4)


class M19612A1CampaignTests(unittest.TestCase):
    def test_repeated_positive_windows_are_promising(self):
        p = plan()
        result = assess_a1_chronological_campaign(
            p,
            evidence_for(p, (POS, POS, POS, POS)),
            policy=campaign_policy(),
        )
        self.assertEqual(result.status, A1CampaignStatus.PROMISING)
        self.assertEqual(result.promising_window_count, 4)
        self.assertEqual(result.total_trades, 16)
        self.assertGreater(result.total_net_pnl, 0.0)
        self.assertEqual(result.blockers, ())

    def test_repeated_negative_windows_are_rejected(self):
        p = plan()
        result = assess_a1_chronological_campaign(
            p,
            evidence_for(p, (NEG, NEG, NEG, NEG)),
            policy=campaign_policy(),
        )
        self.assertEqual(result.status, A1CampaignStatus.REJECTED)
        self.assertIn("promising_window_fraction_failed", result.blockers)
        self.assertIn("positive_window_fraction_failed", result.blockers)
        self.assertIn("aggregate_net_pnl_failed", result.blockers)

    def test_one_bad_window_can_pass_explicit_fraction_policy(self):
        p = plan()
        result = assess_a1_chronological_campaign(
            p,
            evidence_for(p, (POS, POS, NEG, POS)),
            policy=campaign_policy(),
        )
        self.assertEqual(result.status, A1CampaignStatus.PROMISING)
        self.assertEqual(result.promising_window_fraction, 0.75)

    def test_missing_planned_window_is_insufficient_not_rejected(self):
        p = plan()
        rows = evidence_for(p, (POS, POS, POS, POS))[:-1]
        result = assess_a1_chronological_campaign(p, rows, policy=campaign_policy(minimum_total_trades=12))
        self.assertEqual(result.status, A1CampaignStatus.INSUFFICIENT)
        self.assertIn("missing_planned_windows", result.blockers)
        self.assertIn("insufficient_window_count", result.blockers)

    def test_underlying_insufficient_window_makes_campaign_insufficient(self):
        p = plan()
        strict = a1_policy(minimum_trades=5)
        rows = evidence_for(p, (POS, POS, POS, POS), policy=strict)
        result = assess_a1_chronological_campaign(p, rows, policy=campaign_policy())
        self.assertEqual(result.status, A1CampaignStatus.INSUFFICIENT)
        self.assertIn("underlying_window_insufficient", result.blockers)

    def test_mixed_strategy_identity_fails_closed(self):
        p = plan()
        rows = list(evidence_for(p, (POS, POS, POS, POS)))
        other_plan = WalkForwardPlan("e" * 64, PARAMS, DATASET, WalkForwardMode.ANCHORED, p.windows)
        other_assessment = assessment_for(p.windows[0], POS)
        # A plan/evidence mismatch is rejected before any aggregation claim.
        rows[0] = A1WindowEvidence(
            1,
            other_plan.fingerprint,
            p.windows[0].fingerprint,
            p.windows[0].test_start + timedelta(hours=1),
            p.windows[0].test_start + timedelta(hours=2),
            other_assessment,
        )
        with self.assertRaises(ValueError):
            assess_a1_chronological_campaign(p, rows, policy=campaign_policy())

    def test_mixed_m19611_policy_identity_fails_closed(self):
        p = plan()
        rows = list(evidence_for(p, (POS, POS, POS, POS)))
        changed = assessment_for(p.windows[0], POS, policy=a1_policy(maximum_drawdown_fraction=0.5))
        rows[0] = A1WindowEvidence(
            1,
            p.fingerprint,
            p.windows[0].fingerprint,
            p.windows[0].test_start + timedelta(hours=1),
            p.windows[0].test_start + timedelta(hours=2),
            changed,
        )
        with self.assertRaises(ValueError):
            assess_a1_chronological_campaign(p, rows, policy=campaign_policy())

    def test_observation_outside_frozen_test_window_fails_closed(self):
        p = plan()
        rows = list(evidence_for(p, (POS, POS, POS, POS)))
        row = rows[0]
        rows[0] = A1WindowEvidence(
            row.fold,
            row.plan_fingerprint,
            row.window_fingerprint,
            p.windows[0].test_start - timedelta(minutes=1),
            p.windows[0].test_start + timedelta(hours=1),
            row.assessment,
        )
        with self.assertRaises(ValueError):
            assess_a1_chronological_campaign(p, rows, policy=campaign_policy())

    def test_campaign_has_no_operational_authority(self):
        p = plan()
        result = assess_a1_chronological_campaign(
            p,
            evidence_for(p, (POS, POS, POS, POS)),
            policy=campaign_policy(),
        )
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_campaign_fingerprint_is_deterministic_and_policy_bound(self):
        p = plan()
        rows = evidence_for(p, (POS, POS, POS, POS))
        first = assess_a1_chronological_campaign(p, rows, policy=campaign_policy())
        second = assess_a1_chronological_campaign(p, rows, policy=campaign_policy())
        changed = assess_a1_chronological_campaign(
            p,
            rows,
            policy=campaign_policy(minimum_positive_window_fraction=1.0),
        )
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.fingerprint, changed.fingerprint)

    def test_policy_rejects_invalid_thresholds(self):
        with self.assertRaises(ValueError):
            campaign_policy(minimum_windows=1)
        with self.assertRaises(ValueError):
            campaign_policy(minimum_total_trades=0)
        with self.assertRaises(ValueError):
            campaign_policy(minimum_promising_window_fraction=1.1)


if __name__ == "__main__":
    unittest.main()
