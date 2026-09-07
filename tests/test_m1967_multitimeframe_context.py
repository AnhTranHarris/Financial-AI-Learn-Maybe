from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.chart_intelligence import AnalysisNode, MarketAnalysisGraph, NodeOperation, ValueUnit
from dusty.features import FeatureVector
from dusty.multitimeframe_context import (
    bind_multitimeframe_context,
    bind_multitimeframe_history,
)
from dusty.strategy_reconstruction_campaign import (
    AssignmentBasis,
    TimeframeMode,
    TimeframeProfile,
)


UTC = timezone.utc
T0 = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


def fv(at: datetime, **values: float | bool) -> FeatureVector:
    return FeatureVector.of(at, values)


def profile() -> TimeframeProfile:
    return TimeframeProfile(
        "M15",
        ("D1", "H1"),
        TimeframeMode.MULTI,
        AssignmentBasis.SOURCE_DECLARED,
    )


class M1967MultiTimeframeContextTests(unittest.TestCase):
    def series(self):
        return {
            "M15": (
                fv(T0, close=1.1000, rsi=48.0, __execution_price__=1.1001),
                fv(T0 + timedelta(minutes=15), close=1.1010, rsi=52.0, __execution_price__=1.1011),
            ),
            "H1": (
                fv(T0 - timedelta(hours=1), close=1.0970, sma=1.0950),
                fv(T0 + timedelta(minutes=45), close=1.1020, sma=1.0980),
            ),
            "D1": (
                fv(T0 - timedelta(hours=12), close=1.0900, ema=1.0850),
                fv(T0 + timedelta(hours=12), close=1.1100, ema=1.0900),
            ),
        }

    def test_binds_latest_completed_context_as_of_primary_decision(self):
        bound = bind_multitimeframe_context(profile(), self.series(), decision_at=T0)
        values = dict(bound.snapshot.values)
        self.assertEqual(values["m15.close"], 1.1000)
        self.assertEqual(values["h1.close"], 1.0970)
        self.assertEqual(values["d1.close"], 1.0900)
        self.assertEqual(
            dict(bound.source_times),
            {"M15": T0, "D1": T0 - timedelta(hours=12), "H1": T0 - timedelta(hours=1)},
        )

    def test_future_context_is_never_selected(self):
        bound = bind_multitimeframe_context(profile(), self.series(), decision_at=T0)
        values = dict(bound.snapshot.values)
        self.assertNotEqual(values["h1.close"], 1.1020)
        self.assertNotEqual(values["d1.close"], 1.1100)
        self.assertTrue(all(at <= T0 for _, at in bound.source_times))

    def test_primary_decision_clock_cannot_be_backfilled(self):
        with self.assertRaisesRegex(ValueError, "cannot be silently backfilled"):
            bind_multitimeframe_context(
                profile(),
                self.series(),
                decision_at=T0 + timedelta(minutes=10),
            )

    def test_missing_required_context_fails_closed(self):
        rows = self.series()
        del rows["D1"]
        with self.assertRaisesRegex(ValueError, "missing required timeframe"):
            bind_multitimeframe_context(profile(), rows, decision_at=T0)

    def test_context_without_any_completed_row_fails_closed(self):
        rows = self.series()
        rows["H1"] = (fv(T0 + timedelta(hours=1), close=1.2),)
        with self.assertRaisesRegex(ValueError, "no completed H1 context"):
            bind_multitimeframe_context(profile(), rows, decision_at=T0)

    def test_nonchronological_or_duplicate_series_is_rejected(self):
        rows = self.series()
        rows["H1"] = (
            fv(T0, close=1.1),
            fv(T0, close=1.2),
        )
        with self.assertRaisesRegex(ValueError, "strictly chronological"):
            bind_multitimeframe_context(profile(), rows, decision_at=T0)

    def test_naive_feature_timestamp_is_rejected(self):
        rows = self.series()
        rows["H1"] = (fv(datetime(2026, 9, 7, 11, 0), close=1.1),)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            bind_multitimeframe_context(profile(), rows, decision_at=T0)

    def test_internal_execution_reference_never_enters_analytical_snapshot(self):
        bound = bind_multitimeframe_context(profile(), self.series(), decision_at=T0)
        keys = {key for key, _ in bound.snapshot.values}
        self.assertFalse(any("execution_price" in key for key in keys))
        self.assertFalse(any(key.startswith("__") for key in keys))

    def test_namespaced_features_drive_existing_analysis_graph_without_new_agent(self):
        bound = bind_multitimeframe_context(profile(), self.series(), decision_at=T0)
        graph = MarketAnalysisGraph(
            (
                AnalysisNode("d1_close", NodeOperation.INPUT, ValueUnit.PRICE, source_key="d1.close"),
                AnalysisNode("d1_ema", NodeOperation.INPUT, ValueUnit.PRICE, source_key="d1.ema"),
                AnalysisNode("h1_close", NodeOperation.INPUT, ValueUnit.PRICE, source_key="h1.close"),
                AnalysisNode("h1_sma", NodeOperation.INPUT, ValueUnit.PRICE, source_key="h1.sma"),
                AnalysisNode("m15_rsi", NodeOperation.INPUT, ValueUnit.OSCILLATOR, source_key="m15.rsi"),
                AnalysisNode("rsi_mid", NodeOperation.INPUT, ValueUnit.OSCILLATOR, source_key="m15.rsi_mid"),
                AnalysisNode("d1_up", NodeOperation.GREATER_THAN, ValueUnit.BOOLEAN, ("d1_close", "d1_ema")),
                AnalysisNode("h1_up", NodeOperation.GREATER_THAN, ValueUnit.BOOLEAN, ("h1_close", "h1_sma")),
                AnalysisNode("trigger", NodeOperation.GREATER_THAN, ValueUnit.BOOLEAN, ("m15_rsi", "rsi_mid")),
                AnalysisNode("entry_context", NodeOperation.ALL, ValueUnit.BOOLEAN, ("d1_up", "h1_up", "trigger")),
            ),
            (("entry_context", "entry_context"),),
        )
        values = dict(bound.snapshot.values)
        values["m15.rsi_mid"] = 45.0
        from dusty.chart_intelligence import AnalysisSnapshot

        snapshot = AnalysisSnapshot.of(T0, values, known_at=dict(bound.snapshot.known_at))
        self.assertTrue(graph.evaluate(snapshot)["entry_context"])

    def test_history_is_prefix_invariant_when_future_context_is_appended(self):
        initial = self.series()
        initial["M15"] = initial["M15"][:1]
        initial["H1"] = initial["H1"][:1]
        initial["D1"] = initial["D1"][:1]
        first = bind_multitimeframe_history(profile(), initial)
        expanded = self.series()
        second = bind_multitimeframe_history(profile(), expanded)
        self.assertEqual(first[0].fingerprint, second[0].fingerprint)
        self.assertEqual(first[0], second[0])

    def test_single_timeframe_profile_binds_only_primary(self):
        p = TimeframeProfile(
            "H1",
            (),
            TimeframeMode.SINGLE,
            AssignmentBasis.RESEARCH_EXPLORATION,
        )
        rows = {"H1": (fv(T0, close=1.1, rsi=50.0),)}
        bound = bind_multitimeframe_context(p, rows, decision_at=T0)
        self.assertEqual(dict(bound.source_times), {"H1": T0})
        self.assertEqual(set(dict(bound.snapshot.values)), {"h1.close", "h1.rsi"})

    def test_m1_context_is_analytical_only_and_has_no_authority(self):
        p = TimeframeProfile(
            "M5",
            ("M1",),
            TimeframeMode.MULTI,
            AssignmentBasis.SOURCE_DECLARED,
        )
        rows = {
            "M5": (fv(T0, close=1.1),),
            "M1": (fv(T0 - timedelta(minutes=1), close=1.099),),
        }
        bound = bind_multitimeframe_context(p, rows, decision_at=T0)
        self.assertIn("m1.close", dict(bound.snapshot.values))
        self.assertFalse(bound.broker_write_authority)
        self.assertFalse(bound.live_write_authority)
        self.assertFalse(bound.promotion_authority)
        self.assertFalse(bound.risk_override_authority)
        self.assertFalse(bound.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
