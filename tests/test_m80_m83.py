from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dusty.core import HealthState
from dusty.analysis_runtime import AnalysisFrame, replay_analysis_strategy
from dusty.chart_intelligence import AnalysisNode, AnalysisSnapshot, MarketAnalysisGraph, NodeOperation, ValueUnit
from dusty.experience import TradeSide
from dusty.strategy_v3 import (
    EntryPolicy,
    ExitPolicy,
    FrozenStrategyDeployment,
    HoldPolicy,
    OrderStyle,
    PositionView,
    ProtectionPolicy,
    SourceStrategyClaim,
    StrategySpecV3,
    TradeDirective,
    TradeLifecycleRequest,
    reason_trade_lifecycle,
    translate_source_claim,
)
from dusty.tool_evaluation import (
    ContributionDecision,
    PerformanceWindow,
    ToolAblationEvidence,
    TournamentCandidate,
    assess_tool_contribution,
    assess_tournament_candidate,
)


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def strategy() -> StrategySpecV3:
    return StrategySpecV3(
        "structure-rsi-v1",
        "a" * 64,
        ("b" * 64, "c" * 64),
        EntryPolicy("entry_long", "entry_short", OrderStyle.LIMIT, 2),
        HoldPolicy("hold_long", "hold_short", 20, "tighten_long", "tighten_short"),
        ExitPolicy("exit_long", "exit_short", "partial_long", "partial_short", 0.5),
        ProtectionPolicy("atr:2", "rr:2", "atr:1", "rr:1"),
        "https://example.test/strategy",
        "M15",
        240,
    )


def outputs(**overrides: bool) -> dict[str, bool]:
    values = {
        "entry_long": False,
        "entry_short": False,
        "hold_long": True,
        "hold_short": True,
        "tighten_long": False,
        "tighten_short": False,
        "exit_long": False,
        "exit_short": False,
        "partial_long": False,
        "partial_short": False,
    }
    values.update(overrides)
    return values


class StrategyV3Tests(unittest.TestCase):
    def test_graph_can_authorize_governed_long_and_short_entries(self):
        long_decision = reason_trade_lifecycle(
            strategy(), TradeLifecycleRequest.of(NOW, outputs(entry_long=True), HealthState.HEALTHY)
        )
        short_decision = reason_trade_lifecycle(
            strategy(), TradeLifecycleRequest.of(NOW, outputs(entry_short=True), HealthState.HEALTHY)
        )
        self.assertEqual((long_decision.directive, long_decision.order_style), (TradeDirective.ENTER_LONG, OrderStyle.LIMIT))
        self.assertEqual(short_decision.directive, TradeDirective.ENTER_SHORT)

    def test_conflicting_long_short_signals_fail_closed(self):
        decision = reason_trade_lifecycle(
            strategy(), TradeLifecycleRequest.of(NOW, outputs(entry_long=True, entry_short=True), HealthState.HEALTHY)
        )
        self.assertEqual(decision.directive, TradeDirective.WAIT)
        self.assertIn("conflicting_entry_signals", decision.reasons)

    def test_hold_exit_partial_and_protection_are_separate_decisions(self):
        position = PositionView(TradeSide.LONG, 1.1, 1.0, 0.1, 3)
        partial = reason_trade_lifecycle(
            strategy(),
            TradeLifecycleRequest.of(NOW, outputs(partial_long=True), HealthState.HEALTHY, position=position),
        )
        tighten = reason_trade_lifecycle(
            strategy(),
            TradeLifecycleRequest.of(NOW, outputs(tighten_long=True), HealthState.HEALTHY, position=position),
        )
        exit_decision = reason_trade_lifecycle(
            strategy(),
            TradeLifecycleRequest.of(NOW, outputs(hold_long=False), HealthState.HEALTHY, position=position),
        )
        self.assertEqual((partial.directive, partial.partial_fraction), (TradeDirective.PARTIAL_EXIT, 0.5))
        self.assertEqual(tighten.directive, TradeDirective.TIGHTEN_PROTECTION)
        self.assertEqual(exit_decision.directive, TradeDirective.EXIT)

    def test_invalid_tool_exits_position_and_blocks_new_entry(self):
        position = PositionView(TradeSide.SHORT, 1.1, 1.2, 0.1, 2)
        close = reason_trade_lifecycle(
            strategy(),
            TradeLifecycleRequest.of(NOW, outputs(), HealthState.HEALTHY, position=position, tools_valid=False),
        )
        blocked = reason_trade_lifecycle(
            strategy(),
            TradeLifecycleRequest.of(NOW, outputs(entry_long=True), HealthState.HEALTHY, tools_valid=False),
        )
        self.assertEqual(close.directive, TradeDirective.EXIT)
        self.assertEqual(blocked.directive, TradeDirective.WAIT)

    def test_online_executable_code_is_quarantined_and_ambiguity_blocks_translation(self):
        claim = SourceStrategyClaim(
            "claim-1",
            "https://example.test/post",
            NOW,
            (("entry", "third touch"),),
            ("what counts as a touch",),
            True,
        )
        result = translate_source_claim(claim)
        self.assertFalse(result.research_ready)
        self.assertFalse(result.executable_source_accepted)
        self.assertTrue(any(reason.startswith("unresolved:") for reason in result.reasons))

    def test_frozen_champion_detects_any_semantic_drift(self):
        deployed = FrozenStrategyDeployment("a" * 64, "b" * 64, ("c" * 64,), "generation-1")
        valid, reasons = deployed.verify(strategy_hash="a" * 64, graph_hash="b" * 64, tool_fingerprints=("d" * 64,))
        self.assertFalse(valid)
        self.assertEqual(reasons, ("analytical_tool_drift",))

    def test_graph_to_long_entry_hold_exit_semantics_replay(self):
        nodes = tuple(
            AnalysisNode(name, NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key=name)
            for name in outputs()
        )
        graph = MarketAnalysisGraph(
            nodes,
            tuple((name, name) for name in outputs()),
            ("b" * 64, "c" * 64),
        )
        spec = StrategySpecV3(
            "replay",
            graph.fingerprint,
            graph.tool_fingerprints,
            EntryPolicy("entry_long", "entry_short"),
            HoldPolicy("hold_long", "hold_short", 20, "tighten_long", "tighten_short"),
            ExitPolicy("exit_long", "exit_short", "partial_long", "partial_short", 0.5),
            ProtectionPolicy("pct:0.01"),
            "https://example.test/replay",
            "M15",
            60,
        )
        first = AnalysisSnapshot.of(NOW, outputs(entry_long=True))
        second = AnalysisSnapshot.of(NOW.replace(minute=15), outputs())
        third = AnalysisSnapshot.of(NOW.replace(minute=30), outputs(exit_long=True))
        replay = replay_analysis_strategy(
            graph,
            spec,
            (AnalysisFrame(first, 1.10), AnalysisFrame(second, 1.11), AnalysisFrame(third, 1.12)),
            initial_volume=0.1,
        )
        self.assertEqual(tuple(trace.decision.directive for trace in replay.traces), (TradeDirective.ENTER_LONG, TradeDirective.HOLD, TradeDirective.EXIT))
        self.assertEqual(len(replay.trades), 1)
        self.assertIsNone(replay.open_position)


def window(name: str, *, trades: int = 100, pnl: float = 100.0, drawdown: float = 0.1, violations: int = 0, concentration: float = 0.2) -> PerformanceWindow:
    return PerformanceWindow(name, trades, pnl, 200.0, -100.0, drawdown, 20.0, concentration, violations)


class ToolEvaluationTests(unittest.TestCase):
    def test_ablation_retires_tool_with_no_incremental_value(self):
        evidence = ToolAblationEvidence("a" * 64, window("full", pnl=80), window("without", pnl=100))
        self.assertEqual(assess_tool_contribution(evidence).decision, ContributionDecision.RETIRE)

    def test_failure_mechanism_creates_modification_candidate_not_hot_patch(self):
        evidence = ToolAblationEvidence(
            "a" * 64,
            window("full", pnl=80),
            window("without", pnl=100),
            repair_hypothesis="require two-bar persistence",
        )
        self.assertEqual(assess_tool_contribution(evidence).decision, ContributionDecision.MODIFY)

    def test_regime_specific_tool_is_restricted(self):
        evidence = ToolAblationEvidence(
            "a" * 64,
            window("full", pnl=80),
            window("without", pnl=100),
            (("trend", 0.4), ("range", -0.7)),
        )
        self.assertEqual(assess_tool_contribution(evidence).decision, ContributionDecision.RESTRICT)

    def test_profitable_candidate_still_fails_native_parity_or_rules(self):
        candidate = TournamentCandidate(
            "a" * 64,
            "b" * 64,
            window("validation", trades=150, pnl=150),
            window("test", trades=150, pnl=200, violations=1),
            True,
            False,
            True,
            3,
            50,
        )
        result = assess_tournament_candidate(candidate)
        self.assertFalse(result.eligible)
        self.assertIn("native_execution_parity_failed", result.reasons)
        self.assertIn("trading_rule_violation", result.reasons)


if __name__ == "__main__":
    unittest.main()
