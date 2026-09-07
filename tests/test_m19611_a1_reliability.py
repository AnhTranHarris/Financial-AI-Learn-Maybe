from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from dusty.analysis_runtime import AnalysisReplay, AnalysisReplayTrade
from dusty.backtest import PriceMark
from dusty.experience import TradeSide
from dusty.markets import InstrumentEconomics
from dusty.multitimeframe_a1_reliability import (
    A1ReliabilityPolicy,
    A1ReliabilityStatus,
    assess_a1_reliability,
)
from dusty.multitimeframe_financial_backtest import run_minimum_lot_financial_replay


UTC = timezone.utc
T0 = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


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


def financial_from_pnls(pnls: tuple[float, ...], *, week_spacing: int = 1):
    """Create closed minimum-lot trades whose cash PnL equals the requested values."""
    rows = []
    marks = []
    econ = economics()
    entry = 1.1000
    for index, pnl in enumerate(pnls):
        at = T0 + timedelta(days=7 * week_spacing * index)
        exit_at = at + timedelta(hours=1)
        price_move = pnl / (econ.tick_value * econ.volume_min) * econ.tick_size
        exit_price = entry + price_move
        rows.append(
            AnalysisReplayTrade(
                TradeSide.LONG,
                at,
                exit_at,
                entry,
                exit_price,
                1.0,
                "test_exit",
            )
        )
        marks.extend((PriceMark(at, "EURUSD", entry), PriceMark(exit_at, "EURUSD", exit_price)))
    replay = AnalysisReplay("a" * 64, "b" * 64, (), tuple(rows), None)
    return run_minimum_lot_financial_replay(
        replay,
        tuple(marks),
        symbol="EURUSD",
        economics=econ,
        starting_equity=100_000.0,
    )


def permissive_policy(**changes) -> A1ReliabilityPolicy:
    values = {
        "minimum_trades": 4,
        "minimum_distinct_weeks": 4,
        "minimum_net_pnl": 0.0,
        "minimum_mean_trade_pnl": 0.0,
        "minimum_bootstrap_lower_mean": 0.0,
        "minimum_deflated_signal_score": 0.0,
        "maximum_largest_winner_fraction": 0.50,
        "maximum_drawdown_fraction": 0.10,
        "minimum_positive_week_fraction": 0.60,
        "bootstrap_resamples": 500,
        "bootstrap_seed": 19611,
    }
    values.update(changes)
    return A1ReliabilityPolicy(**values)


class M19611A1ReliabilityTests(unittest.TestCase):
    def test_repeatable_positive_minimum_lot_sample_is_promising(self):
        financial = financial_from_pnls((1.0, 1.1, 0.9, 1.2, 0.8, 1.05))
        result = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(),
            trial_count=1,
        )
        self.assertEqual(result.status, A1ReliabilityStatus.PROMISING)
        self.assertEqual(result.blockers, ())
        self.assertGreater(result.net_pnl, 0.0)
        self.assertIsNotNone(result.bootstrap)
        self.assertIsNotNone(result.selection_bias)

    def test_negative_sample_is_rejected_not_relabelled_reliable(self):
        financial = financial_from_pnls((-1.0, -0.5, 0.2, -0.4, 0.1, -0.3))
        result = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(
                maximum_largest_winner_fraction=1.0,
                minimum_positive_week_fraction=0.0,
            ),
            trial_count=1,
        )
        self.assertEqual(result.status, A1ReliabilityStatus.REJECTED)
        self.assertIn("net_pnl_failed", result.blockers)
        self.assertIn("mean_trade_pnl_failed", result.blockers)

    def test_too_few_trades_returns_insufficient_without_statistical_claim(self):
        financial = financial_from_pnls((1.0, 1.1))
        result = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(minimum_trades=4, minimum_distinct_weeks=1),
            trial_count=1,
        )
        self.assertEqual(result.status, A1ReliabilityStatus.INSUFFICIENT)
        self.assertIn("insufficient_trade_count", result.blockers)
        self.assertIsNone(result.bootstrap)
        self.assertIsNone(result.selection_bias)

    def test_insufficient_time_coverage_fails_closed(self):
        financial = financial_from_pnls((1.0, 1.1, 0.9, 1.2), week_spacing=0)
        result = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(minimum_distinct_weeks=2),
            trial_count=1,
        )
        self.assertEqual(result.status, A1ReliabilityStatus.INSUFFICIENT)
        self.assertIn("insufficient_time_coverage", result.blockers)

    def test_bootstrap_lower_bound_is_an_explicit_gate(self):
        financial = financial_from_pnls((1.0, 1.1, 0.9, 1.2, 0.8, 1.05))
        result = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(minimum_bootstrap_lower_mean=2.0),
            trial_count=1,
        )
        self.assertEqual(result.status, A1ReliabilityStatus.REJECTED)
        self.assertIn("bootstrap_lower_mean_failed", result.blockers)

    def test_search_burden_rises_with_trial_count(self):
        financial = financial_from_pnls((1.0, -0.5, 1.0, -0.5, 1.0, -0.5))
        policy = permissive_policy(
            minimum_bootstrap_lower_mean=-100.0,
            maximum_largest_winner_fraction=1.0,
            minimum_positive_week_fraction=0.0,
        )
        first = assess_a1_reliability(financial, economics(), policy=policy, trial_count=1)
        searched = assess_a1_reliability(financial, economics(), policy=policy, trial_count=100)
        self.assertIsNotNone(first.selection_bias)
        self.assertIsNotNone(searched.selection_bias)
        assert first.selection_bias is not None and searched.selection_bias is not None
        self.assertGreater(first.selection_bias.deflated_signal_score, searched.selection_bias.deflated_signal_score)
        self.assertNotIn("search_adjusted_signal_failed", first.blockers)
        self.assertIn("search_adjusted_signal_failed", searched.blockers)

    def test_profit_concentration_is_an_explicit_blocker(self):
        financial = financial_from_pnls((10.0, 0.2, 0.2, 0.2, 0.2, 0.2))
        result = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(
                minimum_bootstrap_lower_mean=-100.0,
                minimum_deflated_signal_score=-100.0,
                maximum_largest_winner_fraction=0.50,
            ),
            trial_count=1,
        )
        self.assertEqual(result.status, A1ReliabilityStatus.REJECTED)
        self.assertIn("profit_concentration_failed", result.blockers)

    def test_drawdown_policy_can_reject_positive_overall_sample(self):
        financial = financial_from_pnls((-5.0, 3.0, 3.0, 3.0, 3.0, 3.0))
        result = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(
                minimum_bootstrap_lower_mean=-100.0,
                minimum_deflated_signal_score=-100.0,
                maximum_largest_winner_fraction=1.0,
                minimum_positive_week_fraction=0.0,
                maximum_drawdown_fraction=0.0,
            ),
            trial_count=1,
        )
        self.assertEqual(result.status, A1ReliabilityStatus.REJECTED)
        self.assertIn("drawdown_failed", result.blockers)

    def test_time_consistency_is_an_explicit_blocker(self):
        financial = financial_from_pnls((3.0, 3.0, -1.0, -1.0, -1.0, -1.0))
        result = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(
                minimum_bootstrap_lower_mean=-100.0,
                minimum_deflated_signal_score=-100.0,
                maximum_largest_winner_fraction=1.0,
                minimum_positive_week_fraction=0.50,
            ),
            trial_count=1,
        )
        self.assertEqual(result.status, A1ReliabilityStatus.REJECTED)
        self.assertIn("time_consistency_failed", result.blockers)

    def test_fingerprint_is_deterministic_and_policy_bound(self):
        financial = financial_from_pnls((1.0, 1.1, 0.9, 1.2, 0.8, 1.05))
        policy = permissive_policy()
        first = assess_a1_reliability(financial, economics(), policy=policy, trial_count=1)
        second = assess_a1_reliability(financial, economics(), policy=policy, trial_count=1)
        self.assertEqual(first.fingerprint, second.fingerprint)
        changed = assess_a1_reliability(
            financial,
            economics(),
            policy=permissive_policy(maximum_drawdown_fraction=0.20),
            trial_count=1,
        )
        self.assertNotEqual(first.fingerprint, changed.fingerprint)

    def test_a1_assessment_has_no_operational_authority(self):
        financial = financial_from_pnls((1.0, 1.1, 0.9, 1.2, 0.8, 1.05))
        result = assess_a1_reliability(financial, economics(), policy=permissive_policy(), trial_count=1)
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_policy_rejects_invalid_thresholds(self):
        with self.assertRaises(ValueError):
            permissive_policy(minimum_trades=1)
        with self.assertRaises(ValueError):
            permissive_policy(maximum_drawdown_fraction=1.1)
        with self.assertRaises(ValueError):
            permissive_policy(bootstrap_resamples=99)


if __name__ == "__main__":
    unittest.main()
