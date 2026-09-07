from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from dusty.analysis_runtime import AnalysisFrame
from dusty.chart_intelligence import AnalysisSnapshot
from dusty.experience import TradeSide
from dusty.estate_a1_execution import (
    analysis_replay_from_runtime_trades,
    bind_estate_candidate,
    execute_estate_candidate,
    runtime_bars_from_research_frames,
)
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_reconstruction_campaign import AssignmentBasis, TimeframeMode, TimeframeProfile
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)


UTC = timezone.utc
T0 = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def reconstruction() -> StrategyReconstruction:
    spec = StrategySpecV2(
        strategy_id="estate-a1-test",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((Clause("close", RuleOp.GT, 1.0),)),),
        exit_plan=ExitPlan(stop_rule="pct:0.01", max_hold_steps=1),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
    )
    return StrategyReconstruction(
        "a" * 64,
        "test-source",
        "https://example.com/strategy",
        "b" * 64,
        "c" * 64,
        "Estate A1 Test",
        ("EURUSD",),
        "M15",
        spec,
        (ReconstructionRule("entry", "close > 1.0", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),),
        (),
        ReconstructionActor.DUSTY_RESEARCH,
        "d" * 64,
        T0,
    )


def frame(at: datetime, price: float) -> AnalysisFrame:
    values = {
        "open": price,
        "high": price + 0.01,
        "low": price - 0.01,
        "close": price,
        "atr": 0.005,
        "m15.close": price,
    }
    return AnalysisFrame(AnalysisSnapshot.of(at, values), price)


class M19613EstateA1ExecutionTests(unittest.TestCase):
    def test_default_binding_uses_persisted_primary_without_inventing_context(self):
        row = reconstruction()
        binding = bind_estate_candidate(row, symbol="eurusd")
        self.assertEqual(binding.profile.primary, "M15")
        self.assertEqual(binding.profile.context, ())
        self.assertEqual(binding.profile.mode, TimeframeMode.SINGLE)
        self.assertEqual(binding.strategy_hash, row.candidate_spec.strategy_hash)

    def test_explicit_multitimeframe_profile_must_bind_estate_primary(self):
        row = reconstruction()
        profile = TimeframeProfile("M15", ("H4", "D1"), TimeframeMode.MULTI, AssignmentBasis.SOURCE_DECLARED)
        binding = bind_estate_candidate(row, symbol="EURUSD", profile=profile)
        self.assertEqual(binding.profile.fingerprint, profile.fingerprint)
        with self.assertRaises(ValueError):
            bind_estate_candidate(
                row,
                symbol="EURUSD",
                profile=TimeframeProfile("M5", ("H1",), TimeframeMode.MULTI, AssignmentBasis.SOURCE_DECLARED),
            )

    def test_symbol_outside_estate_scope_fails_closed(self):
        with self.assertRaises(ValueError):
            bind_estate_candidate(reconstruction(), symbol="XAUUSD")

    def test_estate_candidate_executes_existing_v2_runtime_semantics(self):
        row = reconstruction()
        binding = bind_estate_candidate(row, symbol="EURUSD")
        frames = (frame(T0, 1.10), frame(T0 + timedelta(minutes=15), 1.11))
        trades = execute_estate_candidate(row, binding, frames)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].strategy_hash, row.candidate_spec.strategy_hash)
        self.assertEqual(trades[0].entry_price, 1.10)
        self.assertEqual(trades[0].exit_price, 1.11)
        self.assertEqual(trades[0].exit_reason, "max_hold")

    def test_runtime_frames_require_primary_ohlc_and_strict_chronology(self):
        row = reconstruction()
        binding = bind_estate_candidate(row, symbol="EURUSD")
        with self.assertRaises(ValueError):
            runtime_bars_from_research_frames(binding, (frame(T0, 1.1), frame(T0, 1.1)))
        bad = AnalysisFrame(AnalysisSnapshot.of(T0, {"close": 1.1}), 1.1)
        with self.assertRaises(ValueError):
            runtime_bars_from_research_frames(binding, (bad,))

    def test_runtime_trade_adapter_preserves_identity_and_reference_volume(self):
        row = reconstruction()
        binding = bind_estate_candidate(row, symbol="EURUSD")
        trades = execute_estate_candidate(
            row,
            binding,
            (frame(T0, 1.10), frame(T0 + timedelta(minutes=15), 1.11)),
        )
        replay = analysis_replay_from_runtime_trades(binding, trades)
        self.assertEqual(replay.strategy_hash, binding.strategy_hash)
        self.assertEqual(replay.graph_hash, binding.fingerprint)
        self.assertEqual(len(replay.trades), 1)
        self.assertEqual(replay.trades[0].initial_volume, 1.0)
        self.assertIsNone(replay.open_position)

    def test_binding_has_no_operational_authority(self):
        binding = bind_estate_candidate(reconstruction(), symbol="EURUSD")
        self.assertFalse(binding.broker_write_authority)
        self.assertFalse(binding.live_write_authority)
        self.assertFalse(binding.promotion_authority)
        self.assertFalse(binding.risk_override_authority)
        self.assertFalse(binding.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
