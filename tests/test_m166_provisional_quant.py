from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from dusty.experience import TradeSide
from dusty.features import completed_feature_bars_from_mt5
from dusty.m166_provisional_quant import (
    ProvisionalQuantPolicy,
    build_runtime_bars,
    evaluate_window,
    run_m167,
    run_m171,
    run_m172,
)
from dusty.mt5worker import MT5Bar
from dusty.research import Clause, RuleOp
from dusty.research_sessions import SESSION_EVIDENCE_PROTOCOL
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.walk_forward_lab import build_walk_forward_plan

UTC = timezone.utc


def _bars(count: int = 200) -> tuple[MT5Bar, ...]:
    start = datetime(2025, 1, 1, tzinfo=UTC)
    rows = []
    price = 1.10
    for index in range(count):
        price += 0.0002 if (index // 20) % 2 == 0 else -0.0001
        rows.append(MT5Bar(start + timedelta(minutes=15 * index), price, price + 0.0005, price - 0.0005, price + 0.0001, 100 + index, 2, 0))
    return tuple(rows)


def _spec(*, session: bool = False, event: bool = False) -> StrategySpecV2:
    return StrategySpecV2(
        strategy_id="provisional-test",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((Clause("rsi", RuleOp.GT, 0.0),)),),
        exit_plan=ExitPlan("atr:1", "rr:1", max_hold_steps=2),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=30,
        session_filters=("LONDON",) if session else (),
        event_exclusion_minutes=15 if event else 0,
    )


class ProvisionalQuantTests(unittest.TestCase):
    def test_policy_identity_is_deterministic_and_explicitly_nonproduction(self) -> None:
        first = ProvisionalQuantPolicy()
        second = ProvisionalQuantPolicy()
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertFalse(first.payload["production_semantics"])
        self.assertEqual(first.payload["session_evidence_protocol"], SESSION_EVIDENCE_PROTOCOL)

    def test_runtime_conversion_is_point_in_time_and_drops_unproven_last_bar(self) -> None:
        rows = _bars(80)
        completed = completed_feature_bars_from_mt5(rows)
        runtime = build_runtime_bars(rows)
        self.assertEqual(len(runtime), len(rows) - 1)
        self.assertEqual(runtime[-1].at, rows[-1].at)
        self.assertEqual(len(completed), len(runtime))
        self.assertLess(completed[-1].source_open_at, completed[-1].at)

    def test_window_evaluation_is_deterministic(self) -> None:
        runtime = build_runtime_bars(_bars(300))
        spec = _spec()
        start = runtime[50].at
        end = runtime[250].at
        first = evaluate_window(spec, runtime, start=start, end=end)
        second = evaluate_window(spec, runtime, start=start, end=end)
        self.assertEqual(first, second)
        self.assertTrue(all(start <= row.entry_at < end for row in first.trades))
        self.assertTrue(all(row.exit_at < end for row in first.trades))

    def test_session_evidence_is_derived_but_event_evidence_fails_closed(self) -> None:
        runtime = build_runtime_bars(_bars(200))
        start, end = runtime[30].at, runtime[-1].at
        first = evaluate_window(_spec(session=True), runtime, start=start, end=end)
        second = evaluate_window(_spec(session=True), runtime, start=start, end=end)
        self.assertEqual(first, second)
        with self.assertRaises(ValueError):
            evaluate_window(_spec(event=True), runtime, start=start, end=end)

    def test_unknown_session_filter_fails_closed(self) -> None:
        runtime = build_runtime_bars(_bars(100))
        spec = StrategySpecV2(
            strategy_id="unknown-session-test",
            direction=TradeSide.LONG,
            entry_groups=(RuleGroup((Clause("rsi", RuleOp.GT, 0.0),)),),
            exit_plan=ExitPlan("atr:1", "rr:1", max_hold_steps=2),
            decision_timeframe_minutes=15,
            intended_horizon_minutes=30,
            session_filters=("MARS",),
        )
        with self.assertRaisesRegex(ValueError, "unsupported research session filters"):
            evaluate_window(spec, runtime, start=runtime[20].at, end=runtime[-1].at)

    def test_m167_purging_keeps_training_labels_before_test_boundary(self) -> None:
        runtime = build_runtime_bars(_bars(1000))
        plan = build_walk_forward_plan(
            strategy_execution_fingerprint="1" * 64,
            parameter_fingerprint="2" * 64,
            dataset_fingerprint="3" * 64,
            start=runtime[0].at,
            end=runtime[-1].at,
            train_days=5,
            test_days=2,
        )
        rows = run_m167(runtime, plan.windows, dataset_fingerprint="3" * 64, label_horizon_minutes=60)
        self.assertEqual(len(rows), len(plan.windows))
        self.assertTrue(all(int(row["test_count"]) > 0 for row in rows))
        self.assertTrue(all(int(row["purged_count"]) > 0 for row in rows))

    def test_m171_never_invents_forward_evidence(self) -> None:
        runtime = build_runtime_bars(_bars(300))
        result = evaluate_window(_spec(), runtime, start=runtime[50].at, end=runtime[-1].at)
        decay = run_m171(
            result.trades,
            strategy_fingerprint=_spec().strategy_hash,
            period_start=runtime[50].at,
            period_end=runtime[-1].at,
        )
        if decay is not None:
            self.assertEqual(decay.status.value, "missing_forward")
            self.assertIsNone(decay.retention_ratio)
            self.assertIsNone(decay.decay_fraction)

    def test_m172_reports_sparse_path_as_insufficient(self) -> None:
        runtime = build_runtime_bars(_bars(80))
        result = evaluate_window(_spec(), runtime, start=runtime[30].at, end=runtime[-1].at)
        report = run_m172(result.trades[:5])
        self.assertEqual(report.status.value, "insufficient")
        self.assertIsNone(report.max_drawdown)


if __name__ == "__main__":
    unittest.main()
