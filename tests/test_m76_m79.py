from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dusty.analytical_tools import (
    AnalyticalToolSpec,
    SeriesRevision,
    SQLiteAnalyticalToolRegistry,
    TemporalBehavior,
    ToolBuffer,
    ToolDiagnostic,
    ToolKind,
    ToolLifecycle,
    ToolOrigin,
    ToolParameter,
    ToolRole,
    classify_temporal_behavior,
    discover_indicator_files,
    hash_artifact,
    recommend_lifecycle,
)
from dusty.chart_intelligence import (
    AnalysisNode,
    AnalysisSnapshot,
    ChartAnchor,
    ChartObjectKind,
    ChartObjectSpec,
    MarketAnalysisGraph,
    NodeOperation,
    ValueUnit,
)
from dusty.mt5_analysis import (
    IndicatorProbeRequest,
    NativeIndicator,
    buffer_map,
    parse_indicator_export,
    parse_chart_export,
    render_indicator_probe,
)
from dusty.price_structure import (
    MarketStructure,
    PivotKind,
    PriceBar,
    classify_market_structure,
    confirmed_pivots,
    pivot_trendline,
)


NOW = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)


def custom_tool() -> AnalyticalToolSpec:
    return AnalyticalToolSpec(
        "custom.supertrend",
        "1.0",
        ToolKind.CUSTOM_INDICATOR,
        (ToolRole.DIRECTION, ToolRole.HOLD),
        ToolOrigin.USER_INSTALLED,
        NOW,
        (ToolParameter("period", 14, "int"), ToolParameter("multiplier", 3.0, "float")),
        (ToolBuffer(0, "trend_line", ToolRole.HOLD), ToolBuffer(1, "direction", ToolRole.DIRECTION)),
        28,
        "MQL5/Indicators/SuperTrend.ex5",
        "a" * 64,
    )


class M76RegistryTests(unittest.TestCase):
    def test_custom_indicator_requires_known_buffer_semantics(self):
        with self.assertRaises(ValueError):
            AnalyticalToolSpec(
                "opaque",
                "1",
                ToolKind.CUSTOM_INDICATOR,
                (ToolRole.ENTRY,),
                ToolOrigin.USER_INSTALLED,
                NOW,
                artifact_path="opaque.ex5",
                artifact_hash="a" * 64,
            )

    def test_registry_is_append_only_and_terminal_states_cannot_reactivate(self):
        db = SQLiteAnalyticalToolRegistry()
        try:
            fingerprint = db.register(custom_tool(), at=NOW)
            db.transition(fingerprint, ToolLifecycle.QUARANTINED, at=NOW, reason="unknown custom binary")
            db.transition(fingerprint, ToolLifecycle.INVALID, at=NOW, reason="future dependent")
            self.assertEqual(db.state(fingerprint), ToolLifecycle.INVALID)
            with self.assertRaises(ValueError):
                db.transition(fingerprint, ToolLifecycle.CHALLENGER, at=NOW, reason="recent win")
            self.assertEqual(len(db.history(fingerprint)), 3)
            self.assertTrue(db.integrity_ok())
        finally:
            db.close()

    def test_artifact_hash_cannot_escape_approved_indicator_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "Indicators"
            root.mkdir()
            inside = root / "safe.ex5"
            inside.write_bytes(b"safe")
            outside = Path(folder) / "outside.ex5"
            outside.write_bytes(b"unsafe")
            self.assertEqual(len(hash_artifact(inside, allowed_root=root)), 64)
            with self.assertRaises(ValueError):
                hash_artifact(outside, allowed_root=root)

    def test_indicator_inventory_is_bounded_read_only_and_links_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "Custom").mkdir()
            (root / "Custom" / "Trend.mq5").write_text("source", encoding="utf-8")
            (root / "Custom" / "Trend.ex5").write_bytes(b"binary")
            (root / "ignore.txt").write_text("ignore", encoding="utf-8")
            rows = discover_indicator_files(root)
            self.assertEqual(tuple(row.relative_path for row in rows), ("Custom/Trend.ex5", "Custom/Trend.mq5"))
            self.assertTrue(all(row.source_available for row in rows))
            with self.assertRaises(ValueError):
                discover_indicator_files(root, maximum_files=1)


class M79TemporalTests(unittest.TestCase):
    def test_completed_history_is_prefix_invariant(self):
        revisions = (
            SeriesRevision(NOW, ((NOW - timedelta(minutes=15), 1.0), (NOW, 2.0))),
            SeriesRevision(NOW + timedelta(minutes=15), ((NOW - timedelta(minutes=15), 1.0), (NOW, 2.0))),
        )
        self.assertEqual(classify_temporal_behavior(revisions), TemporalBehavior.CAUSAL_COMPLETED_BAR)

    def test_changed_historical_value_is_repainting(self):
        revisions = (
            SeriesRevision(NOW, ((NOW - timedelta(minutes=15), 1.0),)),
            SeriesRevision(NOW + timedelta(minutes=15), ((NOW - timedelta(minutes=15), 9.0),)),
        )
        self.assertEqual(classify_temporal_behavior(revisions), TemporalBehavior.REPAINTING)

    def test_future_timestamp_is_invalid(self):
        revisions = (
            SeriesRevision(NOW, ((NOW + timedelta(minutes=15), 1.0),)),
            SeriesRevision(NOW + timedelta(minutes=15), ((NOW + timedelta(minutes=15), 1.0),)),
        )
        self.assertEqual(classify_temporal_behavior(revisions), TemporalBehavior.FUTURE_DEPENDENT)

    def test_invalid_tool_is_not_rescued_by_positive_performance(self):
        diagnostic = ToolDiagnostic(
            TemporalBehavior.REPAINTING, True, True, True, True, 500, 10.0
        )
        result = recommend_lifecycle(diagnostic)
        self.assertEqual(result.target, ToolLifecycle.INVALID)
        self.assertFalse(result.modification_warranted)

    def test_regime_specific_value_is_restricted_not_discarded(self):
        diagnostic = ToolDiagnostic(
            TemporalBehavior.CAUSAL_COMPLETED_BAR,
            True,
            True,
            True,
            True,
            200,
            -0.1,
            regime_expectancy=(("trend", 0.4), ("range", -0.5)),
            repair_hypothesis="gate with ADX",
        )
        result = recommend_lifecycle(diagnostic)
        self.assertEqual(result.target, ToolLifecycle.REGIME_RESTRICTED)
        self.assertTrue(result.modification_warranted)


class ChartIntelligenceTests(unittest.TestCase):
    def test_chart_object_rejects_future_anchor(self):
        with self.assertRaises(ValueError):
            ChartObjectSpec(
                "future-line",
                ChartObjectKind.TREND_LINE,
                "EURUSD",
                "M15",
                (ChartAnchor(NOW, 1.1), ChartAnchor(NOW + timedelta(minutes=15), 1.2)),
                NOW,
                ToolOrigin.DUSTY_GENERATED,
            )

    def test_trend_line_has_reproducible_price_geometry(self):
        line = ChartObjectSpec(
            "trend-1",
            ChartObjectKind.TREND_LINE,
            "EURUSD",
            "M15",
            (ChartAnchor(NOW - timedelta(minutes=30), 1.0), ChartAnchor(NOW, 1.2)),
            NOW,
            ToolOrigin.DUSTY_GENERATED,
        )
        self.assertAlmostEqual(line.value_at(NOW + timedelta(minutes=15)), 1.3)

    def test_graph_combines_structure_and_indicator_without_hidden_code(self):
        nodes = (
            AnalysisNode("price", NodeOperation.INPUT, ValueUnit.PRICE, source_key="price"),
            AnalysisNode("support", NodeOperation.INPUT, ValueUnit.PRICE, source_key="support"),
            AnalysisNode("rsi", NodeOperation.INPUT, ValueUnit.OSCILLATOR, source_key="rsi"),
            AnalysisNode("rsi_mid", NodeOperation.INPUT, ValueUnit.OSCILLATOR, source_key="rsi_mid"),
            AnalysisNode("above_support", NodeOperation.GREATER_THAN, ValueUnit.BOOLEAN, ("price", "support")),
            AnalysisNode("rsi_cross", NodeOperation.CROSS_ABOVE, ValueUnit.BOOLEAN, ("rsi", "rsi_mid")),
            AnalysisNode("entry_long", NodeOperation.ALL, ValueUnit.BOOLEAN, ("above_support", "rsi_cross")),
        )
        graph = MarketAnalysisGraph(nodes, (("entry_long", "entry_long"),), ("a" * 64,))
        previous = AnalysisSnapshot.of(NOW - timedelta(minutes=15), {"price": 1.09, "support": 1.08, "rsi": 49.0, "rsi_mid": 50.0})
        current = AnalysisSnapshot.of(NOW, {"price": 1.10, "support": 1.08, "rsi": 51.0, "rsi_mid": 50.0})
        self.assertEqual(graph.evaluate(current, previous=previous), {"entry_long": True})

    def test_graph_rejects_cycle_or_forward_reference(self):
        nodes = (
            AnalysisNode("late", NodeOperation.NOT, ValueUnit.BOOLEAN, ("future",)),
            AnalysisNode("future", NodeOperation.INPUT, ValueUnit.BOOLEAN, source_key="future"),
        )
        with self.assertRaises(ValueError):
            MarketAnalysisGraph(nodes, (("result", "late"),))

    def test_graph_rejects_price_to_oscillator_comparison(self):
        nodes = (
            AnalysisNode("price", NodeOperation.INPUT, ValueUnit.PRICE, source_key="price"),
            AnalysisNode("rsi", NodeOperation.INPUT, ValueUnit.OSCILLATOR, source_key="rsi"),
            AnalysisNode("invalid", NodeOperation.GREATER_THAN, ValueUnit.BOOLEAN, ("price", "rsi")),
        )
        with self.assertRaises(ValueError):
            MarketAnalysisGraph(nodes, (("invalid", "invalid"),))

    def test_graph_rejects_dimensionally_invalid_multiplication(self):
        nodes = (
            AnalysisNode("left", NodeOperation.INPUT, ValueUnit.PRICE, source_key="left"),
            AnalysisNode("right", NodeOperation.INPUT, ValueUnit.PRICE, source_key="right"),
            AnalysisNode("invalid", NodeOperation.MULTIPLY, ValueUnit.PRICE, ("left", "right")),
        )
        with self.assertRaises(ValueError):
            MarketAnalysisGraph(nodes, (("invalid", "invalid"),))

    def test_graph_allows_price_normalization_to_scalar(self):
        nodes = (
            AnalysisNode("price", NodeOperation.INPUT, ValueUnit.PRICE, source_key="price"),
            AnalysisNode("basis", NodeOperation.INPUT, ValueUnit.PRICE, source_key="basis"),
            AnalysisNode("ratio", NodeOperation.DIVIDE, ValueUnit.SCALAR, ("price", "basis")),
        )
        graph = MarketAnalysisGraph(nodes, (("ratio", "ratio"),))
        self.assertEqual(graph.evaluate(AnalysisSnapshot.of(NOW, {"price": 1.2, "basis": 1.0})), {"ratio": 1.2})

    def test_crossover_uses_previous_derived_value_not_current_value(self):
        nodes = (
            AnalysisNode("fast", NodeOperation.INPUT, ValueUnit.PRICE, source_key="fast"),
            AnalysisNode("offset", NodeOperation.INPUT, ValueUnit.PRICE, source_key="offset"),
            AnalysisNode("slow", NodeOperation.INPUT, ValueUnit.PRICE, source_key="slow"),
            AnalysisNode("adjusted", NodeOperation.SUBTRACT, ValueUnit.PRICE, ("fast", "offset")),
            AnalysisNode("cross", NodeOperation.CROSS_ABOVE, ValueUnit.BOOLEAN, ("adjusted", "slow")),
        )
        graph = MarketAnalysisGraph(nodes, (("cross", "cross"),))
        previous = AnalysisSnapshot.of(NOW - timedelta(minutes=15), {"fast": 1.1, "offset": 0.2, "slow": 1.0})
        current = AnalysisSnapshot.of(NOW, {"fast": 1.3, "offset": 0.1, "slow": 1.0})
        self.assertTrue(graph.evaluate(current, previous=previous)["cross"])


class MT5AnalysisBridgeTests(unittest.TestCase):
    def test_probe_rejects_unsupported_mt5_timeframe(self):
        with self.assertRaises(ValueError):
            IndicatorProbeRequest(custom_tool(), "EURUSD", "M7")

    def test_custom_probe_is_tester_only_and_binds_literal_dependency(self):
        source = render_indicator_probe(IndicatorProbeRequest(custom_tool(), "EURUSD", "M15"))
        self.assertIn("MQLInfoInteger(MQL_TESTER)", source)
        self.assertIn('#property tester_indicator "SuperTrend.ex5"', source)
        self.assertIn('iCustom(_Symbol,_Period,"SuperTrend",14,3.0)', source)
        self.assertNotIn("order_send", source.lower())
        self.assertNotIn("OrderSend", source)

    def test_native_probe_uses_declared_parameters_not_free_form_code(self):
        tool = AnalyticalToolSpec(
            "native.rsi",
            "mt5",
            ToolKind.NATIVE_INDICATOR,
            (ToolRole.CONFIRMATION,),
            ToolOrigin.MT5_NATIVE,
            NOW,
            (ToolParameter("period", 14, "int"),),
            (ToolBuffer(0, "rsi", ToolRole.CONFIRMATION),),
            14,
        )
        source = render_indicator_probe(
            IndicatorProbeRequest(tool, "EURUSD", "H1", NativeIndicator.RSI)
        )
        self.assertIn("iRSI(_Symbol,_Period,14,PRICE_CLOSE)", source)

    def test_native_export_is_bound_to_environment_tool_and_availability(self):
        text = (
            "schema,terminal_build,symbol,timeframe,tool_fingerprint,source_open_epoch,available_epoch,buffer_index,value\n"
            f"dusty-indicator-v1,5000,EURUSD,PERIOD_M15,{'a' * 64},1788256800,1788257700,0,51.25\n"
        )
        rows = parse_indicator_export(text)
        self.assertEqual(buffer_map(rows), {(rows[0].source_open, 0): 51.25})

    def test_chart_probe_has_no_broker_write_surface(self):
        source = Path("mt5/DustyChartProbe.mq5").read_text(encoding="utf-8")
        self.assertIn("ObjectsTotal", source)
        self.assertIn("ChartIndicatorsTotal", source)
        self.assertNotIn("OrderSend", source)
        self.assertNotIn("order_send", source)

    def test_chart_export_parser_preserves_native_object_coordinates(self):
        text = (
            "schema,terminal_build,chart_id,symbol,timeframe,object_name,object_type,row_type,row_index,time_epoch,price_or_level\n"
            "dusty-chart-v1,5000,1,EURUSD,PERIOD_M15,support,OBJ_HLINE,anchor,0,1788256800,1.0825\n"
        )
        rows = parse_chart_export(text)
        self.assertEqual(rows[0].object_name, "support")
        self.assertEqual(rows[0].price_or_level, 1.0825)


class PriceStructureTests(unittest.TestCase):
    def bars(self):
        highs = (1.0, 1.2, 1.0, 1.3, 1.1, 1.4, 1.2)
        lows = (0.8, 0.9, 0.7, 1.0, 0.8, 1.1, 0.9)
        return tuple(
            PriceBar(
                NOW + timedelta(minutes=15 * index),
                NOW + timedelta(minutes=15 * (index + 1)),
                lows[index] + 0.05,
                highs[index],
                lows[index],
                highs[index] - 0.05,
            )
            for index in range(len(highs))
        )

    def test_pivots_are_known_only_after_right_hand_confirmation(self):
        pivots = confirmed_pivots(self.bars(), left_bars=1, right_bars=1)
        first_high = next(row for row in pivots if row.kind is PivotKind.HIGH)
        self.assertGreater(first_high.known_at, first_high.source_open)
        prefix = confirmed_pivots(self.bars()[:5], left_bars=1, right_bars=1)
        self.assertEqual(prefix, tuple(row for row in pivots if row.known_at <= self.bars()[4].available_at))

    def test_market_structure_and_trendline_use_confirmed_pivots(self):
        pivots = confirmed_pivots(self.bars(), left_bars=1, right_bars=1)
        self.assertEqual(classify_market_structure(pivots), MarketStructure.UPTREND)
        line = pivot_trendline(pivots, kind=PivotKind.LOW, symbol="EURUSD", timeframe="M15", object_id="support-trend")
        self.assertLessEqual(max(anchor.at for anchor in line.anchors), line.known_at)


if __name__ == "__main__":
    unittest.main()
