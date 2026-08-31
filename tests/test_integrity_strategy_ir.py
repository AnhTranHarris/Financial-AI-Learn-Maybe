from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from dusty.core import CoherenceState, EvidenceItem, EvidenceSnapshot, check_coherence
from dusty.event_research import (
    LiquidityState,
    MarketReaction,
    TradingSession,
    assess_session_repricing,
)
from dusty.experience import (
    ActionKind,
    SourceGrade,
    SourceKind,
    TradeAction,
    TradeSide,
    reconstruct_episode,
)
from dusty.markets import InstrumentEconomics
from dusty.research import Clause, RuleOp, StrategySpec
from dusty.strategy_ir import (
    EligibilityStatus,
    ExecutionSensitivity,
    ExitPlan,
    RuleGroup,
    StrategySpecV2,
    assess_strategy_eligibility,
    migrate_v1,
)


T0 = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


class PointInTimeIntegrityTests(unittest.TestCase):
    def test_future_evidence_is_rejected_by_core(self) -> None:
        snapshot = EvidenceSnapshot.of(
            "future",
            (
                EvidenceItem(
                    key="trend",
                    value="up",
                    source="test",
                    observed_at=T0 + timedelta(seconds=1),
                ),
            ),
        )
        result = check_coherence(snapshot, at=T0)
        self.assertIs(result.state, CoherenceState.INCOHERENT)
        self.assertEqual(result.reasons, ("future_observation:trend",))

    def test_evidence_time_contract_is_strict(self) -> None:
        with self.assertRaises(ValueError):
            EvidenceItem("x", 1, "test", datetime(2026, 8, 31, 8, 0))
        with self.assertRaises(ValueError):
            EvidenceItem(
                "x",
                1,
                "test",
                T0,
                valid_until=T0 - timedelta(seconds=1),
            )
        with self.assertRaises(ValueError):
            EvidenceItem("x", 1, "test", T0, confidence=1.01)


class QuantityAwareEpisodeTests(unittest.TestCase):
    def test_scale_in_out_long_and_short_use_cash_flows(self) -> None:
        long = reconstruct_episode(
            "long-scale",
            "EURUSD",
            SourceKind.DUSTY,
            "dusty://long",
            SourceGrade.BACKTEST,
            (
                TradeAction(T0, ActionKind.ENTRY, TradeSide.LONG, 100.0, 1.0),
                TradeAction(T0 + timedelta(minutes=1), ActionKind.SCALE_IN, TradeSide.LONG, 110.0, 1.0),
                TradeAction(T0 + timedelta(minutes=2), ActionKind.SCALE_OUT, TradeSide.LONG, 120.0, 1.0),
                TradeAction(T0 + timedelta(minutes=3), ActionKind.EXIT, TradeSide.LONG, 130.0, 1.0),
            ),
        )
        self.assertAlmostEqual(long.pnl, 40.0)
        self.assertAlmostEqual(long.entry_notional, 210.0)
        self.assertAlmostEqual(long.return_fraction, 40.0 / 210.0)

        short = reconstruct_episode(
            "short-scale",
            "EURUSD",
            SourceKind.DUSTY,
            "dusty://short",
            SourceGrade.BACKTEST,
            (
                TradeAction(T0, ActionKind.ENTRY, TradeSide.SHORT, 100.0, 1.0),
                TradeAction(T0 + timedelta(minutes=1), ActionKind.SCALE_IN, TradeSide.SHORT, 90.0, 1.0),
                TradeAction(T0 + timedelta(minutes=2), ActionKind.SCALE_OUT, TradeSide.SHORT, 80.0, 1.0),
                TradeAction(T0 + timedelta(minutes=3), ActionKind.EXIT, TradeSide.SHORT, 70.0, 1.0),
            ),
        )
        self.assertAlmostEqual(short.pnl, 40.0)
        self.assertAlmostEqual(short.return_fraction, 40.0 / 190.0)

    def test_episode_cannot_over_exit_or_leave_quantity_open(self) -> None:
        with self.assertRaises(ValueError):
            reconstruct_episode(
                "over",
                "EURUSD",
                SourceKind.DUSTY,
                "dusty://over",
                SourceGrade.BACKTEST,
                (
                    TradeAction(T0, ActionKind.ENTRY, TradeSide.LONG, 1.10, 1.0),
                    TradeAction(T0 + timedelta(minutes=1), ActionKind.EXIT, TradeSide.LONG, 1.11, 2.0),
                ),
            )
        with self.assertRaises(ValueError):
            reconstruct_episode(
                "open",
                "EURUSD",
                SourceKind.DUSTY,
                "dusty://open",
                SourceGrade.BACKTEST,
                (
                    TradeAction(T0, ActionKind.ENTRY, TradeSide.LONG, 1.10, 2.0),
                    TradeAction(T0 + timedelta(minutes=1), ActionKind.EXIT, TradeSide.LONG, 1.11, 1.0),
                ),
            )


class EventAccountingTests(unittest.TestCase):
    def test_timestamped_reaction_must_match_elapsed_minutes(self) -> None:
        with self.assertRaises(ValueError):
            MarketReaction(
                "event",
                "WTI",
                10,
                0.01,
                2.0,
                10.0,
                TradingSession.ASIA,
                LiquidityState.LOW,
                event_at=T0,
                observed_at=T0 + timedelta(minutes=11),
            )

    def test_overlapping_explicit_intervals_are_rejected(self) -> None:
        rows = (
            MarketReaction(
                "event",
                "WTI",
                60,
                0.01,
                2.0,
                10.0,
                TradingSession.ASIA,
                LiquidityState.LOW,
                interval_start_minute=0,
            ),
            MarketReaction(
                "event",
                "WTI",
                90,
                0.01,
                2.0,
                10.0,
                TradingSession.ASIA,
                LiquidityState.LOW,
                interval_start_minute=30,
            ),
            MarketReaction(
                "event",
                "WTI",
                180,
                0.01,
                1.0,
                20.0,
                TradingSession.LONDON,
                LiquidityState.HIGH,
                interval_start_minute=90,
            ),
        )
        with self.assertRaises(ValueError):
            assess_session_repricing(rows)


class InstrumentEconomicsTests(unittest.TestCase):
    def test_volume_normalization_only_rounds_down(self) -> None:
        economics = InstrumentEconomics(
            contract_size=100_000,
            tick_size=0.00001,
            tick_value=1.0,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100.0,
        )
        self.assertEqual(economics.normalize_volume_down(0.009), 0.0)
        self.assertEqual(economics.normalize_volume_down(0.056), 0.05)
        self.assertLessEqual(economics.normalize_volume_down(0.056), 0.056)


class StrategyIRTests(unittest.TestCase):
    def _spec(self, **overrides: object) -> StrategySpecV2:
        values = {
            "strategy_id": "trend-v2",
            "direction": TradeSide.LONG,
            "entry_groups": (RuleGroup((Clause("trend", RuleOp.EQ, "up"),)),),
            "exit_plan": ExitPlan("swing_low", max_hold_steps=12),
            "decision_timeframe_minutes": 5,
            "intended_horizon_minutes": 30,
        }
        values.update(overrides)
        return StrategySpecV2(**values)

    def test_hash_is_canonical_and_entry_logic_is_declarative(self) -> None:
        first = self._spec()
        second = self._spec()
        self.assertEqual(first.strategy_hash, second.strategy_hash)
        self.assertTrue(first.entry_matches({"trend": "up"}))
        self.assertFalse(first.entry_matches({"trend": "down"}))

    def test_constitution_prohibits_scalping_hft_martingale_and_latency_critical(self) -> None:
        cases = (
            self._spec(is_scalping=True),
            self._spec(is_hft=True),
            self._spec(martingale=True),
            self._spec(loss_recovery_sizing=True),
            self._spec(unbounded_averaging=True),
            self._spec(execution_sensitivity=ExecutionSensitivity.LATENCY_CRITICAL),
            self._spec(decision_timeframe_minutes=1),
            self._spec(intended_horizon_minutes=5),
        )
        for spec in cases:
            with self.subTest(spec=spec):
                self.assertIs(
                    assess_strategy_eligibility(spec).status,
                    EligibilityStatus.PROHIBITED,
                )

    def test_high_execution_sensitivity_is_research_only(self) -> None:
        result = assess_strategy_eligibility(
            self._spec(execution_sensitivity=ExecutionSensitivity.HIGH)
        )
        self.assertIs(result.status, EligibilityStatus.RESEARCH_ONLY)
        self.assertFalse(result.promotable)

    def test_v1_migration_preserves_entry_and_cost_semantics(self) -> None:
        old = StrategySpec(
            "legacy",
            TradeSide.LONG,
            (Clause("trend", RuleOp.EQ, "up"),),
            horizon_steps=4,
            cost_bps=3.5,
        )
        new = migrate_v1(
            old,
            decision_timeframe_minutes=15,
            intended_horizon_minutes=60,
            stop_rule="atr:1.5",
        )
        self.assertTrue(new.entry_matches({"trend": "up"}))
        self.assertEqual(new.cost_bps, 3.5)
        self.assertEqual(new.exit_plan.max_hold_steps, 4)
        self.assertIs(assess_strategy_eligibility(new).status, EligibilityStatus.ALLOWED)

    def test_stop_is_mandatory(self) -> None:
        with self.assertRaises(ValueError):
            ExitPlan("   ")


if __name__ == "__main__":
    unittest.main()
