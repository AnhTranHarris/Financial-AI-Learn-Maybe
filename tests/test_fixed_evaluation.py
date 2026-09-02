from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dusty.broker_cost_observation import observe_recent_costs
from dusty.features import completed_feature_bars_from_mt5, compute_standard_features
from dusty.investment_lab import LaboratoryConfig, run_laboratory_from_bars
from dusty.local_research import (
    LocalResearchRuntime, ResearchSettings, SelectedTerminalHistoryReader,
    _atomic_json, _request_payload, execute_research, read_research_result,
)
from dusty.research_evaluation import FixedEvaluationPlan, parse_fixed_end, run_fixed_evaluation
from dusty.reviewed_strategies import reviewed_research_packages
from test_connected_research import START, END, FixtureReader, HistoryMT5, fixture_bars, selection


def fixed_settings(**changes):
    return replace(ResearchSettings(history_days=7, fixed_end=END, holdout_days=2,
                                   cost_source="Synthetic test assumptions; not broker verified"), **changes)


class FullWindowReader(FixtureReader):
    cost_observation = {"status": "NO_MATCHING_EXECUTIONS", "execution_deals": 0,
                        "schedule_verified": False, "used_as_simulation_costs": False}

    def read(self, selected, start, end):
        _, economics = super().read(selected, start, end)
        return fixture_bars(start, count=int((end - start).total_seconds() / 900) + 1), economics


def fixed_fixture_worker(selected, settings, directory, start, end, repository):
    result = execute_research(selected, settings, directory, start, end, reader=FullWindowReader())
    _atomic_json(directory / "result.json", result)


class FixedSpawnContext:
    def Process(self, *, target, args, daemon):
        return multiprocessing.get_context("spawn").Process(target=fixed_fixture_worker, args=args, daemon=daemon)


class FixedPlanTests(unittest.TestCase):
    def test_fixed_bounds_do_not_slide_when_clock_changes(self):
        settings = fixed_settings()
        self.assertEqual(settings.bounds(END + timedelta(hours=1)), (START, END))
        self.assertEqual(settings.bounds(END + timedelta(days=2)), (START, END))
        self.assertEqual(settings.evaluation_plan(START, END).holdout_start, END - timedelta(days=2))

    def test_rolling_mode_remains_explicitly_exploratory(self):
        settings = ResearchSettings()
        now = END + timedelta(minutes=37, seconds=5)
        _, end = settings.bounds(now)
        self.assertEqual(end, END + timedelta(minutes=30))
        self.assertIsNone(settings.evaluation_plan(end - timedelta(days=7), end))

    def test_future_unaligned_naive_or_invalid_windows_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            fixed_settings().bounds(END - timedelta(seconds=1))
        for kwargs in ({"fixed_end": END.replace(tzinfo=None)}, {"fixed_end": END + timedelta(minutes=1)},
                       {"fixed_end": END + timedelta(seconds=1)}, {"holdout_days": True},
                       {"holdout_days": 7}, {"holdout_days": -1}, {"fixed_end": None}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                fixed_settings(**kwargs)
        with self.assertRaises(ValueError):
            fixed_settings().evaluation_plan(START, END + timedelta(minutes=15))

    def test_end_parser_uses_explicit_UTC(self):
        self.assertIsNone(parse_fixed_end(""))
        self.assertEqual(parse_fixed_end("2026-08-31 00:00"), END)
        for value in ("today", "2026-08-31 00:01", "2026-02-30 00:00"):
            with self.assertRaises(ValueError):
                parse_fixed_end(value)

    def test_cost_notes_cannot_create_verified_cost_authority(self):
        settings = fixed_settings(cost_source="VERIFIED zero commission! https://example.invalid")
        evidence = settings.cost_provenance()
        self.assertEqual(evidence["status"], "USER_ASSUMPTIONS_NOT_VERIFIED")
        self.assertFalse(evidence["verified_broker_schedule"])
        self.assertFalse(evidence["fees_and_swaps_complete"])
        self.assertTrue(evidence["zero_commission_assumed"])
        for note in ("x" * 401, "line\nbreak", 123):
            with self.assertRaises(ValueError):
                fixed_settings(cost_source=note)

    def test_plan_fingerprint_binds_dates_and_does_not_claim_unseen_data(self):
        plan = fixed_settings().evaluation_plan(START, END)
        self.assertNotEqual(plan.fingerprint, replace(plan, holdout_start=plan.holdout_start + timedelta(days=1)).fingerprint)
        self.assertFalse(plan.payload()["unseen_data_verified"])
        self.assertFalse(plan.payload()["promotion_eligible"])


class EvaluationEngineTests(unittest.TestCase):
    def setUp(self):
        self.package = reviewed_research_packages()[0]
        raw, self.economics = FullWindowReader().read(selection(), START, END)
        self.bars = completed_feature_bars_from_mt5(raw)
        self.config = LaboratoryConfig(growth_starting_equity=20000)
        self.plan = fixed_settings().evaluation_plan(START, END)

    def run_plan(self, bars=None):
        return run_fixed_evaluation(self.package.compiled, self.bars if bars is None else bars,
            symbol="EURUSD", economics=self.economics, config=self.config, plan=self.plan)

    def test_flat_resets_no_boundary_crossings_and_no_tail_entries(self):
        holdout, details = self.run_plan()
        development = details["development_laboratory"]
        self.assertEqual(holdout.growth_backtest.starting_equity, 20000)
        self.assertEqual(development["growth_backtest"]["starting_equity"], 20000)
        self.assertNotEqual(development["growth_backtest"]["ending_equity"], 20000)
        for name, trades in (("development", development["potential_trades"]), ("holdout", asdict(holdout)["potential_trades"])):
            segment = details["segments"][name]
            start, end = datetime.fromisoformat(segment["start"]), datetime.fromisoformat(segment["end"])
            cutoff = datetime.fromisoformat(segment["entry_cutoff_exclusive"])
            for trade in trades:
                self.assertTrue(start <= trade["entry_at"] < cutoff)
                self.assertTrue(trade["entry_at"] < trade["exit_at"] < end)
        self.assertEqual(details["verdict"], "RESEARCH_ONLY_NOT_QUALIFIED")

    def test_future_holdout_prices_cannot_change_development_results(self):
        _, before = self.run_plan()
        altered = tuple(replace(bar, open=bar.open+10, high=bar.high+10, low=bar.low+10,
                                close=bar.close+10, execution_price=bar.market_price_at_availability+10)
                        if bar.at >= self.plan.holdout_start else bar for bar in self.bars)
        _, after = self.run_plan(altered)
        self.assertEqual(before["development_laboratory"], after["development_laboratory"])
        self.assertEqual(before["segments"]["development"], after["segments"]["development"])

    def test_holdout_warmup_is_past_only_and_not_in_ledger(self):
        holdout, details = self.run_plan()
        self.assertGreater(details["segments"]["holdout"]["past_warmup_bars"], 0)
        self.assertTrue(all(bar.at >= self.plan.holdout_start for bar in holdout.feature_bars))
        self.assertTrue(all(row.at >= self.plan.holdout_start for row in holdout.cognition))
        self.assertTrue(all(row.at >= self.plan.holdout_start for row in holdout.growth_backtest.ledger))
        self.assertNotIn("missing:rsi", holdout.cognition[0].coherence.reasons)
        full = {row.at: row for row in compute_standard_features(self.bars)}
        warmup = tuple(b for b in self.bars if b.at < self.plan.holdout_start)
        actual = compute_standard_features(warmup + holdout.feature_bars)[len(warmup)]
        self.assertEqual(actual, full[holdout.feature_bars[0].at])

    def test_missing_or_sparse_segment_never_expands_or_shifts_the_plan(self):
        with self.assertRaisesRegex(ValueError, "holdout_has_insufficient"):
            self.run_plan(tuple(bar for bar in self.bars if bar.at < self.plan.holdout_start))
        with self.assertRaisesRegex(ValueError, "development_has_insufficient"):
            self.run_plan(tuple(bar for bar in self.bars if bar.at >= self.plan.holdout_start))

    def test_future_warmup_and_unbounded_holding_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "warmup"):
            run_laboratory_from_bars(self.package.compiled, self.bars[:100], symbol="EURUSD",
                economics=self.economics, feature_warmup_bars=self.bars[100:])
        with self.assertRaisesRegex(ValueError, "finite_max_hold"):
            run_fixed_evaluation(SimpleNamespace(spec=SimpleNamespace(exit_plan=SimpleNamespace(max_hold_steps=None))),
                self.bars, symbol="EURUSD", economics=self.economics, config=self.config, plan=self.plan)

    def test_duplicate_or_reversed_bars_rejected(self):
        for rows in (self.bars[::-1], self.bars[:1] + self.bars):
            with self.assertRaises(ValueError):
                self.run_plan(rows)


class BrokerCostObservationTests(unittest.TestCase):
    def deal(self, **changes):
        fields = dict(symbol="EURUSD", type=0, commission=-2.0, fee=-.1, swap=0.0)
        fields.update(changes)
        return SimpleNamespace(**fields)

    def observe(self, rows):
        return observe_recent_costs(SimpleNamespace(history_deals_get=lambda *_: rows), "EURUSD", END)

    def test_deposits_other_symbols_and_empty_history_do_not_prove_zero_costs(self):
        for rows in ([], [self.deal(type=2)], [self.deal(symbol="EURUSD.a")], [self.deal(type=True)]):
            evidence = self.observe(rows)
            self.assertEqual(evidence["status"], "NO_MATCHING_EXECUTIONS")
            self.assertIsNone(evidence["commission_cash"])
            self.assertFalse(evidence["schedule_verified"])

    def test_observed_signed_costs_remain_separate_and_not_a_roundtrip_rate(self):
        evidence = self.observe([self.deal(), self.deal(type=1, commission=-3.0, fee=-.2, swap=-.5)])
        self.assertEqual(evidence["status"], "OBSERVED_NOT_VERIFIED")
        self.assertEqual(evidence["commission_cash"], -5.0)
        self.assertAlmostEqual(evidence["fee_cash"], -.3)
        self.assertEqual(evidence["swap_cash"], -.5)
        self.assertFalse(evidence["used_as_simulation_costs"])
        self.assertNotIn("roundtrip_per_lot", evidence)

    def test_absent_or_nonfinite_fee_fields_are_unknown_not_free(self):
        for value in (None, float("nan"), float("inf"), True):
            evidence = self.observe([self.deal(fee=value)])
            self.assertEqual(evidence["status"], "INCOMPLETE_COST_FIELDS")
            self.assertIsNone(evidence["fee_cash"])
        row = self.deal()
        del row.fee
        self.assertEqual(self.observe([row])["incomplete_cost_rows"], 1)

    def test_unavailable_and_bounded_failure_do_not_invent_costs(self):
        self.assertEqual(self.observe(None)["status"], "UNAVAILABLE")
        self.assertEqual(self.observe([self.deal()] * 10001)["status"], "BOUNDED_READ_LIMIT_EXCEEDED")
        self.assertEqual(observe_recent_costs(SimpleNamespace(), "EURUSD", END)["status"], "UNAVAILABLE")

    def test_native_reader_keeps_account_check_after_cost_history_read(self):
        module = HistoryMT5()
        selected = selection(module)
        def changed(*_):
            module.account.login += 10000
            return []
        module.history_deals_get = changed
        with self.assertRaisesRegex(ValueError, "selected_account_changed"):
            SelectedTerminalHistoryReader(module).read(selected, START, END)


class FixedEvidenceTests(unittest.TestCase):
    def test_full_artifacts_freeze_plan_costs_and_holdout_primary_report(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            selected, settings = selection(), fixed_settings(commission_per_lot=2, slippage_points=1)
            request = _request_payload(selected, settings, START, END)
            _atomic_json(directory / "request.json", request)
            result = execute_research(selected, settings, directory, START, END, reader=FullWindowReader())
            _atomic_json(directory / "result.json", result)
            self.assertEqual(read_research_result(directory)["state"], "COMPLETED")
            report = json.loads((directory / "report.json").read_text())
            self.assertEqual(report["evaluation"]["plan"], request["evaluation_plan"])
            self.assertEqual(report["evaluation"]["plan_fingerprint"], request["evaluation_plan_fingerprint"])
            self.assertEqual(report["cost_provenance"], request["cost_provenance"])
            self.assertEqual(report["config"]["commission_per_lot"], 2)
            self.assertEqual(report["config"]["expected_slippage_price"], .00001)
            self.assertEqual(report["laboratory"]["bar_count"], report["evaluation"]["segments"]["holdout"]["observed_bars"])
            self.assertEqual(report["capital_summary"]["candidates"], report["evaluation"]["segments"]["holdout"]["minimum_lot_trades"])
            self.assertFalse(result["promotion_eligible"])
            self.assertIn("HISTORICAL HOLDOUT COMPLETED", result["message"])
            self.assertIn("not proof of untouched", result["message"])

    def test_changed_costs_dates_or_snapshot_rejected_before_acquisition(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            selected, settings = selection(), fixed_settings()
            _atomic_json(directory / "request.json", _request_payload(selected, settings, START, END))
            reader = SimpleNamespace(read=lambda *_: self.fail("must reject before acquisition"))
            for changed in (replace(settings, commission_per_lot=1), replace(settings, cost_source="changed"),
                            replace(settings, holdout_days=1)):
                with self.assertRaisesRegex(ValueError, "frozen_request"):
                    execute_research(selected, changed, directory, START, END, reader=reader)

    def test_real_spawn_fixed_evaluation_delivers_scoped_capital_label(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"evidence",
                settings=fixed_settings(), context=FixedSpawnContext(), code_checker=lambda *_: True)
            self.assertTrue(runtime.start(selection()).accepted)
            deadline = time.monotonic() + 30
            try:
                while runtime.active and time.monotonic() < deadline:
                    time.sleep(.02)
                job = runtime.poll()
                self.assertEqual(job.state, "COMPLETED", job.message)
                self.assertEqual(job.capital_label, "HISTORICAL HOLDOUT ONLY")
                self.assertIsNotNone(job.capital_summary)
            finally:
                runtime.emergency_halt()
                if runtime._process:
                    runtime._process.join(timeout=5)
                    runtime.poll()


if __name__ == "__main__":
    unittest.main()
