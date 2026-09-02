from dataclasses import asdict, replace
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dusty.basic_ui import DustyBasicUI
from dusty.core import Decision, HealthState
from dusty.experience import TradeSide
from dusty.features import FeatureVector, completed_feature_bars_from_mt5, compute_standard_features
from dusty.investment_lab import LaboratoryConfig, run_laboratory_from_bars
from dusty.local_research import LocalResearchRuntime, ResearchSettings, _atomic_json, _request_payload, execute_research, read_research_result
from dusty.research_comparison import comparison_contract, run_research_comparison, _fingerprint, _screen
from dusty.research_eligibility import EntryPolicy, entry_eligibility
from dusty.research_evaluation import FixedEvaluationPlan, run_fixed_evaluation
from dusty.reviewed_strategies import reviewed_research_packages
from dusty.strategy_catalog import OperatingMode
import test_connected_research as connected_fixtures
from test_connected_research import START, END, selection
from test_fixed_evaluation import FullWindowReader, FixedSpawnContext, fixed_settings


def comparison_settings(**changes):
    return replace(fixed_settings(comparison=True), **changes)


def setup_data():
    raw, economics = FullWindowReader().read(selection(), START, END)
    return completed_feature_bars_from_mt5(raw), economics


class EntryPolicyTests(unittest.TestCase):
    def vector(self, **changes):
        values = {"close": 102.0, "sma_20": 100.0, "ema_20": 101.0}
        values.update(changes)
        return FeatureVector(START, tuple(values.items()))

    def test_long_and_short_alignment_are_symmetric(self):
        self.assertTrue(entry_eligibility(self.vector(), TradeSide.LONG, EntryPolicy.TREND).allowed)
        self.assertFalse(entry_eligibility(self.vector(), TradeSide.SHORT, EntryPolicy.TREND).allowed)
        low = self.vector(close=98, ema_20=99)
        self.assertTrue(entry_eligibility(low, TradeSide.SHORT, EntryPolicy.TREND).allowed)
        self.assertFalse(entry_eligibility(low, TradeSide.LONG, EntryPolicy.TREND).allowed)

    def test_equal_or_conflicting_values_veto(self):
        for changes in ({"close": 100}, {"ema_20": 100}, {"ema_20": 99}):
            self.assertFalse(entry_eligibility(self.vector(**changes), TradeSide.LONG, EntryPolicy.TREND).allowed)

    def test_missing_nonfinite_boolean_and_nonpositive_inputs_fail_closed(self):
        for value in (None, True, "101", math.nan, math.inf, -math.inf, 0, -1):
            with self.subTest(value=value):
                decision = entry_eligibility(self.vector(ema_20=value), TradeSide.LONG, EntryPolicy.TREND)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, "trend_inputs_missing_or_invalid")
        self.assertFalse(entry_eligibility(FeatureVector(START, ()), TradeSide.LONG, EntryPolicy.TREND).allowed)

    def test_unknown_policy_or_side_is_never_treated_as_seed(self):
        for side, policy in (("long", EntryPolicy.TREND), (TradeSide.LONG, "seed-v1"), (TradeSide.LONG, None)):
            with self.assertRaises(ValueError):
                entry_eligibility(self.vector(), side, policy)

    def test_control_vetoes_even_when_all_trend_inputs_align(self):
        self.assertFalse(entry_eligibility(self.vector(), TradeSide.LONG, EntryPolicy.NO_TRADE).allowed)
        self.assertTrue(entry_eligibility(self.vector(), TradeSide.LONG, EntryPolicy.SEED).allowed)

    def test_feature_prefix_does_not_change_when_future_prices_change(self):
        bars, _ = setup_data()
        changed = bars[:300] + tuple(replace(b, open=b.open*2, high=b.high*2, low=b.low*2,
                                           close=b.close*2, execution_price=b.execution_price*2) for b in bars[300:])
        original = compute_standard_features(bars)
        mutated = compute_standard_features(changed)
        self.assertEqual(original[:300], mutated[:300])
        for side in TradeSide:
            self.assertEqual([entry_eligibility(v, side, EntryPolicy.TREND) for v in original[:300]],
                             [entry_eligibility(v, side, EntryPolicy.TREND) for v in mutated[:300]])


class LaboratoryVetoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.economics = setup_data()
        cls.package = reviewed_research_packages()[0]
        cls.config = LaboratoryConfig(growth_starting_equity=100000, commission_per_lot=0)

    def run_lab(self, policy=EntryPolicy.SEED, **kwargs):
        return run_laboratory_from_bars(self.package.compiled, self.bars, symbol="EURUSD",
            economics=self.economics, config=self.config, entry_policy=policy, **kwargs)

    def test_default_seed_path_is_exactly_unchanged(self):
        implicit = run_laboratory_from_bars(self.package.compiled, self.bars, symbol="EURUSD",
                                          economics=self.economics, config=self.config)
        self.assertEqual(asdict(implicit), asdict(self.run_lab()))

    def test_control_has_zero_trades_pnl_and_drawdown(self):
        run = self.run_lab(EntryPolicy.NO_TRADE)
        self.assertFalse(run.potential_trades)
        self.assertFalse(run.growth_sizing)
        for result in (run.minimum_lot_backtest, run.growth_backtest):
            self.assertEqual((result.trade_count, result.net_pnl, result.max_drawdown_fraction), (0, 0, 0))

    def test_veto_does_not_rewrite_cognition_and_every_entry_is_authorized(self):
        seed, filtered = self.run_lab(), self.run_lab(EntryPolicy.TREND)
        self.assertEqual(seed.cognition, filtered.cognition)
        signals = {t.at: t.decision for t in filtered.cognition}
        permissions = {v.at: entry_eligibility(v, TradeSide.LONG, EntryPolicy.TREND).allowed
                       for v in compute_standard_features(self.bars)}
        self.assertTrue(filtered.potential_trades, "fixture must exercise entries, not vacuous success")
        for trade in filtered.potential_trades:
            self.assertTrue(permissions[trade.entry_at])
            self.assertIs(signals[trade.entry_at], Decision.ENTRY_LONG)
        shared = {t.entry_at: t for t in seed.potential_trades}
        for trade in filtered.potential_trades:
            if trade.entry_at in shared:
                self.assertEqual(trade, shared[trade.entry_at])

    def test_unknown_veto_is_rejected(self):
        with self.assertRaises(ValueError):
            self.run_lab("custom-secret-policy")

    def test_additional_veto_cannot_override_guardian_stop(self):
        # Missing health evidence denies entries regardless of aligned trend.
        for health in HealthState:
            if health.value == "healthy":
                continue
            result = self.run_lab(EntryPolicy.TREND, health=health)
            self.assertFalse(result.potential_trades)

    def test_filtered_native_manifest_and_envelope_authority_are_unavailable(self):
        for policy in (EntryPolicy.TREND, EntryPolicy.NO_TRADE):
            run = self.run_lab(policy)
            self.assertFalse(run.mt5_manifest_supported)
            self.assertEqual(run.minimum_lot_manifest, "")
            self.assertEqual(run.growth_manifest, "")
            with self.assertRaises(ValueError):
                run.growth_execution_envelopes()

    def test_holdout_mutation_cannot_change_development_veto_results(self):
        plan = FixedEvaluationPlan(START, END-timedelta(days=2), END)
        changed = tuple(replace(b, open=b.open*1.5, high=b.high*1.5, low=b.low*1.5,
                                close=b.close*1.5, execution_price=b.execution_price*1.5)
                        if b.at >= plan.holdout_start else b for b in self.bars)
        args = dict(symbol="EURUSD", economics=self.economics, config=self.config, plan=plan, entry_policy=EntryPolicy.TREND)
        _, original = run_fixed_evaluation(self.package.compiled, self.bars, **args)
        _, modified = run_fixed_evaluation(self.package.compiled, changed, **args)
        self.assertEqual(original["development_laboratory"], modified["development_laboratory"])


class ComparisonMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.economics = setup_data()
        cls.config = LaboratoryConfig(growth_starting_equity=100000, commission_per_lot=1,
                                      expected_slippage_price=.00002)
        cls.plan = FixedEvaluationPlan(START, END-timedelta(days=2), END)
        cls.report = run_research_comparison(cls.bars, symbol="EURUSD", economics=cls.economics,
                                            config=cls.config, plan=cls.plan)

    def test_all_twenty_cases_retained_without_a_winner(self):
        self.assertEqual(len(self.report["cases"]), 20)
        self.assertEqual(len({c["case_fingerprint"] for c in self.report["cases"]}), 20)
        self.assertEqual(len(self.report["entry_eligibility"]), 5)
        self.assertIsNone(self.report["selected_winner"])
        self.assertFalse(self.report["promotion_eligible"])
        self.assertEqual(self.report["deployment_decision"], "ABSTAIN_UNQUALIFIED")

    def test_each_cost_and_candidate_has_both_segments(self):
        contract = comparison_contract()
        expected = {(p["id"], c["id"], s) for p in contract["candidates"]
                    for c in contract["cost_scenarios"] for s in ("development", "holdout")}
        self.assertEqual(expected, {(c["candidate_id"], c["cost_scenario"], c["segment"]) for c in self.report["cases"]})

    def test_no_trade_control_cannot_pass_the_limited_screen(self):
        for case in self.report["cases"]:
            if case["candidate_id"] == "no-trade":
                self.assertFalse(case["screen"]["limited_screen_passed"])
                self.assertEqual(case["metrics"]["growth_net_pnl"], 0)
                self.assertEqual(case["metrics"]["growth_trades"], 0)

    def test_cost_stress_uses_broker_point_not_trade_tick_size(self):
        for case in self.report["cases"]:
            expected = self.config.expected_slippage_price + (10*self.economics.point_size
                       if case["cost_scenario"] == "stress-plus-10-points" else 0)
            self.assertEqual(case["config"]["expected_slippage_price"], expected)
            self.assertEqual(case["config"]["commission_per_lot"], 1)
        other = replace(self.economics, tick_size=self.economics.tick_size*5)
        report = run_research_comparison(self.bars, symbol="EURUSD", economics=other, config=self.config, plan=self.plan)
        self.assertEqual(report["cases"][2]["config"]["expected_slippage_price"], self.config.expected_slippage_price + 10*other.point_size)

    def test_unknown_point_size_cannot_silently_assume_zero_stress(self):
        with self.assertRaisesRegex(ValueError, "point_size"):
            run_research_comparison(self.bars, symbol="EURUSD", economics=replace(self.economics, point_size=0),
                                    config=self.config, plan=self.plan)

    def test_seed_metrics_reproduce_original_fixed_evaluation(self):
        for package in reviewed_research_packages():
            _, baseline = run_fixed_evaluation(package.compiled, self.bars, symbol="EURUSD", economics=self.economics,
                                               config=self.config, plan=self.plan)
            for case in self.report["cases"]:
                if case["candidate_id"] == f"{package.spec.direction.value}:seed-v1" and case["cost_scenario"] == "configured":
                    self.assertEqual(case["metrics"], baseline["segments"][case["segment"]])

    def test_every_case_keeps_cash_ledger_and_trade_sizing_provenance(self):
        for case in self.report["cases"]:
            self.assertEqual(len(case["potential_trades"]), len(case["growth_sizing"]))
            approved = [t for t in case["growth_sizing"] if t["approved"]]
            self.assertEqual(len(approved), case["metrics"]["growth_trades"])
            self.assertAlmostEqual(sum(t["expected_net_pnl"] for t in approved), case["metrics"]["growth_net_pnl"], places=7)
            for key in ("minimum_lot_backtest", "growth_backtest"):
                self.assertTrue(case[key]["ledger"])
                self.assertEqual(case[key]["starting_equity"], 100000)
            self.assertFalse(case["screen"]["promotion_eligible"])

    def test_veto_reasons_and_cutoff_boundaries_are_auditable(self):
        for case in self.report["cases"]:
            self.assertEqual(case["blocked_cognition_signals_before_tail"], sum(case["blocked_reasons"].values()))
            if case["candidate_id"].endswith(":seed-v1"):
                self.assertEqual(case["blocked_cognition_signals_before_tail"], 0)
            for trade in case["potential_trades"]:
                m = case["metrics"]
                self.assertLess(trade["entry_at"].isoformat(), m["entry_cutoff_exclusive"])
                self.assertLess(trade["exit_at"].isoformat(), m["end"])

    def test_contract_and_data_fingerprints_are_deterministic(self):
        self.assertEqual(self.report["contract_fingerprint"], _fingerprint(comparison_contract()))
        self.assertEqual(self.report["data_fingerprint"], _fingerprint([asdict(b) for b in self.bars]))
        with self.assertRaises(TypeError):
            _fingerprint(object())

    def test_limited_screen_boundary_never_grants_authority(self):
        screen = comparison_contract()["screen"]
        passing = {"growth_trades": 20, "growth_net_pnl": .01, "growth_drawdown": .02}
        self.assertTrue(_screen(passing, screen, control=False)["limited_screen_passed"])
        self.assertFalse(_screen(passing, screen, control=False)["promotion_eligible"])
        for changes in ({"growth_trades": 19}, {"growth_net_pnl": 0}, {"growth_drawdown": .020001}):
            self.assertFalse(_screen(dict(passing, **changes), screen, control=False)["limited_screen_passed"])


class ComparisonIntegrationTests(unittest.TestCase):
    def test_comparison_requires_explicit_fixed_split_and_cost_note(self):
        for changes in ({"fixed_end": None, "holdout_days": 0}, {"holdout_days": 0}, {"cost_source": " "}, {"comparison": 1}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                comparison_settings(**changes)

    def test_preview_discloses_actual_dates_and_zero_holdout(self):
        text = comparison_settings().window_preview(END)
        self.assertIn("Development UTC: 2026-08-24 00:00 to 2026-08-29 00:00", text)
        self.assertIn("Holdout UTC: 2026-08-29 00:00 to 2026-08-31 00:00", text)
        self.assertIn("NO HOLDOUT", ResearchSettings().window_preview(END))
        with self.assertRaisesRegex(ValueError, "future"):
            comparison_settings().window_preview(START)

    def test_default_request_shape_preserved_and_comparison_contract_frozen(self):
        original = _request_payload(selection(), fixed_settings(), START, END)
        self.assertNotIn("comparison", original["settings"])
        self.assertNotIn("comparison_contract", original)
        proposed = _request_payload(selection(), comparison_settings(), START, END)
        self.assertTrue(proposed["settings"]["comparison"])
        self.assertEqual(proposed["comparison_contract"], comparison_contract())

    def test_contract_tampering_fails_before_market_read(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            settings = comparison_settings()
            payload = _request_payload(selection(), settings, START, END)
            payload["comparison_contract"]["candidates"].pop()
            _atomic_json(directory/"request.json", payload)
            with patch.object(FullWindowReader, "read", side_effect=AssertionError("read forbidden")):
                with self.assertRaisesRegex(ValueError, "frozen_request"):
                    execute_research(selection(), settings, directory, START, END, reader=FullWindowReader())

    def test_completed_comparison_is_hash_bound_and_read_only(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            selected, settings = selection(), comparison_settings()
            _atomic_json(directory/"request.json", _request_payload(selected, settings, START, END))
            result = execute_research(selected, settings, directory, START, END, reader=FullWindowReader())
            _atomic_json(directory/"result.json", result)
            self.assertFalse(read_research_result(directory)["promotion_eligible"])
            self.assertIn("COMPARISON COMPLETED — ABSTAIN", result["message"])
            report = json.loads((directory/"report.json").read_text())
            self.assertEqual(len(report["comparison"]["cases"]), 20)
            self.assertNotIn("prospective_registration", report)
            report["comparison"]["cases"].pop()
            _atomic_json(directory/"report.json", report)
            with self.assertRaisesRegex(ValueError, "hash_mismatch"):
                read_research_result(directory)

    def test_comparison_cannot_register_or_consume_a_future_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"research",
                                           settings=comparison_settings(), code_checker=lambda *_: True)
            with self.assertRaisesRegex(ValueError, "cannot_register"):
                runtime.register_plan(selection())
            with self.assertRaisesRegex(ValueError, "cannot_register"):
                _request_payload(selection(), comparison_settings(), START, END, registration={})
            self.assertFalse(runtime.active)

    def test_spawned_comparison_uses_existing_worker_and_polling(self):
        import time
        with tempfile.TemporaryDirectory() as root:
            runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"research",
                settings=comparison_settings(), code_checker=lambda *_: True, context=FixedSpawnContext())
            self.assertTrue(runtime.start(selection()).accepted)
            deadline = time.monotonic() + 60
            try:
                while runtime.active and time.monotonic() < deadline:
                    runtime.poll()
                    time.sleep(.02)
                view = runtime.poll()
                self.assertEqual(view.state, "COMPLETED", view.message)
                self.assertIn("COMPARISON SELECTS NO WINNER", view.capital_label)
                self.assertIsNotNone(view.capital_summary)
            finally:
                if runtime.active:
                    runtime.stop_new_entries()
                    runtime._process.join(timeout=5)

    def test_incomplete_matrix_cannot_publish_a_successful_report(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            selected, settings = selection(), comparison_settings()
            _atomic_json(directory/"request.json", _request_payload(selected, settings, START, END))
            original = run_fixed_evaluation
            calls = []
            def fail_second(*args, **kwargs):
                calls.append(1)
                if len(calls) == 2:
                    raise ValueError("synthetic_case_failure")
                return original(*args, **kwargs)
            with patch("dusty.research_comparison.run_fixed_evaluation", side_effect=fail_second):
                with self.assertRaisesRegex(ValueError, "synthetic_case_failure"):
                    execute_research(selected, settings, directory, START, END, reader=FullWindowReader())
            self.assertFalse((directory/"report.json").exists())
            self.assertTrue((directory/"bars.json").exists())

    @unittest.skipUnless(os.name == "nt", "Windows real Tk comparison confirmation")
    def test_windows_comparison_confirmation_cancel_and_zero_holdout(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"research", settings=comparison_settings())
            app = connected_fixtures.ApplicationLifecycleTests().app(root, runtime)
            with patch.object(DustyBasicUI, "_refresh"):
                ui = DustyBasicUI(app, SimpleNamespace(), code_commit="f"*40, research=runtime)
            calls = []
            ui._background = lambda action, callback: calls.append((action, callback))
            try:
                ui._start()
                window = next(w for w in ui._root.winfo_children() if isinstance(w, ui._tk.Toplevel))
                button = next(w for w in window.winfo_children() if isinstance(w, ui._ttk.Button) and "Compare strategies" in w.cget("text"))
                with patch("tkinter.messagebox.askokcancel", return_value=False) as confirm:
                    button.invoke()
                    self.assertIn("Holdout UTC", confirm.call_args.args[1])
                    self.assertFalse(calls)
                    self.assertTrue(window.winfo_exists())
                field = next(w for w in window.winfo_children() if isinstance(w, ui._ttk.Entry) and int(w.grid_info()["row"]) == 6)
                field.delete(0, "end")
                field.insert(0, "0")
                with patch("tkinter.messagebox.showerror") as error, patch("tkinter.messagebox.askokcancel") as confirm:
                    button.invoke()
                    error.assert_called_once()
                    confirm.assert_not_called()
                    self.assertFalse(calls)
                field.delete(0, "end")
                field.insert(0, "2")
                with patch("tkinter.messagebox.askokcancel", return_value=True):
                    button.invoke()
                self.assertEqual(len(calls), 1)
                self.assertTrue(runtime.settings.comparison)
                self.assertEqual(str(ui._mode_buttons[OperatingMode.DEMO].cget("state")), "disabled")
                self.assertEqual(str(ui._mode_buttons[OperatingMode.LIVE].cget("state")), "disabled")
            finally:
                ui._root.destroy()


if __name__ == "__main__":
    unittest.main()
