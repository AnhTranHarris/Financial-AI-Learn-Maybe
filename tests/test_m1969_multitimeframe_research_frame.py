from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dusty.analysis_runtime import replay_analysis_strategy
from dusty.chart_intelligence import AnalysisNode, MarketAnalysisGraph, NodeOperation, ValueUnit
from dusty.features import FeatureVector
from dusty.multitimeframe_context import bind_multitimeframe_context, bind_multitimeframe_history
from dusty.multitimeframe_research_frame import (
    analysis_frame_from_multitimeframe_context,
    analysis_frames_from_multitimeframe_history,
)
from dusty.strategy_reconstruction_campaign import AssignmentBasis, TimeframeMode, TimeframeProfile
from dusty.strategy_v3 import EntryPolicy, ExitPolicy, HoldPolicy, ProtectionPolicy, StrategySpecV3


UTC = timezone.utc
T0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)
TOOL = "a" * 64


def fv(at: datetime, **values: float | bool) -> FeatureVector:
    return FeatureVector.of(at, values)


def profile() -> TimeframeProfile:
    return TimeframeProfile("M15", ("D1",), TimeframeMode.MULTI, AssignmentBasis.SOURCE_DECLARED)


def graph_and_strategy() -> tuple[MarketAnalysisGraph, StrategySpecV3]:
    graph = MarketAnalysisGraph(
        (
            AnalysisNode("long", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="d1.trend_ok"),
            AnalysisNode("short", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.short_entry"),
            AnalysisNode("hold_long", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.hold_long"),
            AnalysisNode("hold_short", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.hold_short"),
            AnalysisNode("exit_long", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.exit_long"),
            AnalysisNode("exit_short", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="m15.exit_short"),
        ),
        (
            ("long_entry", "long"),
            ("short_entry", "short"),
            ("hold_long", "hold_long"),
            ("hold_short", "hold_short"),
            ("exit_long", "exit_long"),
            ("exit_short", "exit_short"),
        ),
        (TOOL,),
    )
    strategy = StrategySpecV3(
        strategy_id="m1969-test",
        analysis_graph_hash=graph.fingerprint,
        tool_fingerprints=(TOOL,),
        entry=EntryPolicy("long_entry", "short_entry"),
        hold=HoldPolicy("hold_long", "hold_short", 8),
        exit=ExitPolicy("exit_long", "exit_short"),
        protection=ProtectionPolicy("atr:2"),
        source_reference="m1969-test",
        decision_timeframe="M15",
        intended_horizon_minutes=60,
    )
    return graph, strategy


class M1969MultiTimeframeResearchFrameTests(unittest.TestCase):
    def primary(self, at: datetime = T0, *, close: float = 1.1000) -> FeatureVector:
        return fv(
            at,
            close=close,
            atr=0.0010,
            rsi=55.0,
            short_entry=False,
            hold_long=True,
            hold_short=True,
            exit_long=False,
            exit_short=False,
            __execution_price__=1.1010,
        )

    def context(self) -> FeatureVector:
        return fv(T0 - timedelta(hours=12), close=1.0900, atr=9.0, trend_ok=True)

    def bound(self, primary: FeatureVector | None = None):
        primary = primary or self.primary()
        return bind_multitimeframe_context(
            profile(),
            {"M15": (primary,), "D1": (self.context(),)},
            decision_at=primary.at,
        )

    def test_primary_public_features_gain_legacy_aliases_context_does_not(self):
        primary = self.primary()
        frame = analysis_frame_from_multitimeframe_context(profile(), self.bound(primary), primary)
        values = dict(frame.snapshot.values)
        self.assertEqual(values["m15.atr"], 0.0010)
        self.assertEqual(values["atr"], 0.0010)
        self.assertEqual(values["d1.atr"], 9.0)
        self.assertNotIn("trend_ok", values)
        self.assertEqual(values["d1.trend_ok"], True)

    def test_execution_price_is_primary_reference_not_context(self):
        primary = self.primary()
        frame = analysis_frame_from_multitimeframe_context(profile(), self.bound(primary), primary)
        self.assertEqual(frame.execution_price, 1.1010)
        self.assertFalse(any("execution_price" in key for key, _ in frame.snapshot.values))

    def test_primary_value_mismatch_fails_closed(self):
        original = self.primary()
        bound = self.bound(original)
        altered = self.primary(close=1.2000)
        with self.assertRaisesRegex(ValueError, "disagrees with bound context"):
            analysis_frame_from_multitimeframe_context(profile(), bound, altered)

    def test_missing_primary_execution_reference_fails_closed(self):
        primary = fv(T0, close=1.1, atr=0.001)
        bound = self.bound(primary)
        with self.assertRaisesRegex(ValueError, "positive execution reference"):
            analysis_frame_from_multitimeframe_context(profile(), bound, primary)

    def test_profile_identity_mismatch_fails_closed(self):
        primary = self.primary()
        bound = self.bound(primary)
        other = TimeframeProfile("M15", ("H1",), TimeframeMode.MULTI, AssignmentBasis.SOURCE_DECLARED)
        with self.assertRaisesRegex(ValueError, "profile identity mismatch"):
            analysis_frame_from_multitimeframe_context(other, bound, primary)

    def test_namespaced_context_drives_strategy_and_atr_stop_uses_primary_alias(self):
        primary = self.primary()
        frame = analysis_frame_from_multitimeframe_context(profile(), self.bound(primary), primary)
        graph, strategy = graph_and_strategy()
        replay = replay_analysis_strategy(graph, strategy, (frame,))
        self.assertIsNotNone(replay.open_position)
        assert replay.open_position is not None
        self.assertAlmostEqual(replay.open_position.entry_price, 1.1010)
        self.assertAlmostEqual(replay.open_position.current_stop, 1.0990)
        self.assertEqual(dict(replay.traces[0].outputs)["long_entry"], True)

    def test_history_adapter_preserves_exact_primary_clock_and_prefix(self):
        p1 = self.primary(T0)
        p2 = fv(
            T0 + timedelta(minutes=15),
            close=1.101,
            atr=0.0011,
            short_entry=False,
            hold_long=True,
            hold_short=True,
            exit_long=False,
            exit_short=False,
            __execution_price__=1.102,
        )
        series = {"M15": (p1, p2), "D1": (self.context(),)}
        bounds = bind_multitimeframe_history(profile(), series)
        frames = analysis_frames_from_multitimeframe_history(profile(), bounds, (p1, p2))
        self.assertEqual(tuple(frame.snapshot.at for frame in frames), (T0, T0 + timedelta(minutes=15)))
        self.assertEqual(dict(frames[0].snapshot.values)["atr"], 0.0010)
        self.assertEqual(dict(frames[1].snapshot.values)["atr"], 0.0011)

    def test_history_missing_exact_primary_row_fails_closed(self):
        primary = self.primary()
        bound = self.bound(primary)
        with self.assertRaisesRegex(ValueError, "lacks exact primary feature row"):
            analysis_frames_from_multitimeframe_history(profile(), (bound,), ())

    def test_adapter_has_no_authority_surface(self):
        import dusty.multitimeframe_research_frame as module

        forbidden = ("order_send", "OrderSend", "broker_write_authority", "live_write_authority")
        source = Path(module.__file__).read_text(encoding="utf-8")
        for token in forbidden:
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
