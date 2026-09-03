from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from dusty.connected_forecast import (HORIZON, forecast_contract, fit_return_model, forecast_fold,
                                      ridge_forecast_map)
from dusty.core import Decision, HealthState
from dusty.features import FeatureBar
from dusty.forecasting import Forecast
from dusty.investment_lab import LaboratoryConfig, run_laboratory_from_bars
from dusty.local_research import (LocalResearchRuntime, ResearchSettings, _atomic_json, _json,
    _request_payload, _seal_campaign_queue, execute_research, read_research_result)
from dusty.research_campaign import campaign_contract, run_forecast_campaign
from dusty.research_viewer import case_label, case_overview, trade_detail, trade_values, load_case_report
from dusty.reviewed_strategies import reviewed_research_packages
from test_connected_research import START, END, selection, FakeContext
from test_fixed_evaluation import FullWindowReader, FixedSpawnContext, fixed_settings
from test_research_comparison import setup_data


def settings(**changes):
    return replace(fixed_settings(holdout_days=1, campaign=True), **changes)


class FittedForecastTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.economics = setup_data()
        cls.start = END-timedelta(days=1)

    def test_model_is_fitted_to_past_labels_and_deterministic(self):
        model = fit_return_model(self.bars, before=self.start)
        self.assertLess(model.trained_through, self.start)
        self.assertGreater(model.pairs, 64)
        self.assertNotEqual(model.slope, 0)
        self.assertEqual(model, fit_return_model(self.bars, before=self.start))
        past = tuple(b for b in self.bars if b.at < self.start)
        self.assertEqual(model, fit_return_model(past, before=self.start))

    def test_mutating_all_test_prices_cannot_change_fit(self):
        changed = tuple(replace(b, close=b.close*2, high=b.high*2, low=b.low*2, open=b.open*2)
                        if b.at >= self.start else b for b in self.bars)
        self.assertEqual(fit_return_model(self.bars, before=self.start), fit_return_model(changed, before=self.start))

    def test_mutating_future_prices_leaves_issued_forecasts_unchanged(self):
        boundary = self.start+timedelta(hours=8)
        changed = tuple(replace(b, close=b.close*2, high=b.high*2, low=b.low*2, open=b.open*2)
                        if b.at > boundary else b for b in self.bars)
        a = forecast_fold(self.bars, start=self.start, end=END)
        b = forecast_fold(changed, start=self.start, end=END)
        prior = lambda x: [r for r in x["forecasts"] if r["issued_at"] <= boundary]
        self.assertEqual(prior(a), prior(b))

    def test_labels_maturing_at_boundary_are_purged(self):
        past = tuple(b for b in self.bars if b.at < self.start)
        model = fit_return_model(self.bars, before=self.start)
        self.assertEqual(model.pairs, len(past)-4-HORIZON)
        self.assertEqual(model.trained_through, past[-1].at)

    def test_target_alignment_and_all_baselines_share_same_origins(self):
        f = forecast_fold(self.bars, start=self.start, end=END)
        indices = {b.at: i for i, b in enumerate(self.bars)}
        for target in f["realized_targets"]:
            i = indices[target["at"]]
            self.assertEqual(target["target"], self.bars[i+HORIZON].close)
            self.assertLess(self.bars[i+HORIZON].at, END)
        self.assertEqual({s["count"] for s in f["scores"].values()}, {len(f["realized_targets"])})
        self.assertEqual(f["unscored_tail_observations"], HORIZON)
        self.assertEqual(f["scores"]["no-change"]["mae_skill_vs_no_change"], 0)

    def test_closed_market_gaps_are_not_filled_or_called_15_minute_horizons(self):
        bars = tuple(replace(b, at=b.at+timedelta(days=2) if b.at >= self.start+timedelta(hours=4) else b.at)
                     for b in self.bars)
        f = forecast_fold(bars, start=self.start, end=END+timedelta(days=2))
        self.assertEqual(len(f["forecasts"]), 96)
        self.assertIn("observations", f["target"])

    def test_flat_training_and_zero_baseline_error_are_defined(self):
        bars = tuple(FeatureBar(b.at, 100, 100, 100, 100) for b in self.bars)
        f = forecast_fold(bars, start=self.start, end=END)
        self.assertEqual(f["model"]["slope"], 0)
        for s in f["scores"].values():
            self.assertEqual(s["mae"], 0)
            self.assertIsNone(s["mae_skill_vs_no_change"])
        self.assertFalse(f["promotion_eligible"])

    def test_insufficient_training_fails_without_fallback(self):
        with self.assertRaisesRegex(ValueError, "insufficient"):
            fit_return_model(self.bars[:83], before=self.start)

    def test_duplicates_unordered_empty_and_naive_boundary_rejected(self):
        for bars in ((), self.bars[::-1], self.bars[:2]*2):
            with self.subTest(bars=len(bars)), self.assertRaises(ValueError):
                fit_return_model(bars, before=self.start)
        with self.assertRaises(ValueError):
            fit_return_model(self.bars, before=self.start.replace(tzinfo=None))

    def test_prediction_cap_and_fingerprint_bind_actual_fit(self):
        model = fit_return_model(self.bars, before=self.start)
        self.assertLessEqual(abs(model.predict(1e9)), .2)
        self.assertNotEqual(model.fingerprint, replace(model, slope=model.slope+1).fingerprint)
        self.assertEqual(len(model.training_sha256), 64)

    def test_nonfinite_boolean_or_bad_price_forecasts_rejected(self):
        for value in (math.nan, math.inf, -math.inf, True, 0, -1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Forecast("test", START, 16, 100, value)
        with self.assertRaises(ValueError):
            Forecast("test", START, True, 100, 101)

    def test_forecast_interface_fails_closed_for_missing_or_misaligned_data(self):
        p = reviewed_research_packages()[0]
        rows = tuple(b for b in self.bars if self.start <= b.at < END)
        kwargs = dict(symbol="EURUSD", economics=self.economics)
        run = run_laboratory_from_bars(p.compiled, rows, require_forecasts=True, forecasts_by_time={}, **kwargs)
        self.assertFalse(run.potential_trades)
        self.assertFalse(run.mt5_manifest_supported)
        for forecast in (Forecast("test", rows[0].at-timedelta(minutes=15), 16, rows[0].close, rows[0].close),
                         Forecast("test", rows[0].at, 16, rows[0].close*2, rows[0].close)):
            with self.assertRaisesRegex(ValueError, "timestamp_or_origin"):
                run_laboratory_from_bars(p.compiled, rows, forecasts_by_time={rows[0].at: [forecast]}, **kwargs)

    def test_forecasts_cannot_override_halted_health_or_create_native_manifest(self):
        p = reviewed_research_packages()[0]
        rows = tuple(b for b in self.bars if self.start <= b.at < END)
        fs = ridge_forecast_map(forecast_fold(self.bars, start=self.start, end=END))
        run = run_laboratory_from_bars(p.compiled, rows, symbol="EURUSD", economics=self.economics,
            forecasts_by_time=fs, require_forecasts=True, health=HealthState.FAILED)
        self.assertFalse(run.potential_trades)
        self.assertFalse(run.minimum_lot_manifest)
        with self.assertRaises(ValueError):
            run.growth_execution_envelopes()


class CampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.economics = setup_data()
        cls.config = LaboratoryConfig(growth_starting_equity=100000, commission_per_lot=1)
        cls.contract = campaign_contract(START, END, 1)
        cls.report = run_forecast_campaign(cls.bars, symbol="EURUSD", economics=cls.economics,
                                           config=cls.config, contract=cls.contract)

    def run_campaign(self, **kwargs):
        return run_forecast_campaign(kwargs.pop("bars", self.bars), symbol="EURUSD", economics=self.economics,
                                    config=self.config, contract=kwargs.pop("contract", self.contract), **kwargs)

    def test_full_fixed_matrix_no_winner_and_every_case_reconciles(self):
        self.assertEqual(len(self.report["cases"]), 30)
        self.assertEqual(len({c["case_fingerprint"] for c in self.report["cases"]}), 30)
        self.assertIsNone(self.report["selected_winner"])
        self.assertFalse(self.report["promotion_eligible"])
        for c in self.report["cases"]:
            self.assertAlmostEqual(c["metrics"]["growth_net_pnl"], c["diagnosis"]["totals"]["growth"]["net_pnl"])
            self.assertEqual(c["growth_backtest"]["starting_equity"], 100000)
            self.assertFalse(c["screen"]["promotion_eligible"])
        self.assertEqual(len(self.report["cost_attribution"]), 15)

    def test_forecast_entries_have_same_timestamp_evidence_and_never_cross_folds(self):
        for c in self.report["cases"]:
            fold = next(f for f in self.contract["folds"] if f["id"] == c["segment"])
            for row in c["diagnosis"]["rows"]:
                self.assertGreaterEqual(row["entry_at"], fold["start"])
                self.assertLess(row["exit_at"], fold["end"])
                if c["forecast_required"]:
                    fs = row["entry_context"]["forecast"]
                    self.assertEqual(fs[0]["at"], row["entry_at"])
                    direction = 1 if row["side"] == "long" else -1
                    self.assertGreaterEqual(direction*(fs[0]["point"]/fs[0]["origin"]-1), -.000100000001)

    def test_control_all_six_cases_remain_zero_not_qualified(self):
        controls = [c for c in self.report["cases"] if c["candidate_id"] == "no-trade"]
        self.assertEqual(len(controls), 6)
        for c in controls:
            self.assertEqual(c["metrics"]["growth_net_pnl"], 0)
            self.assertFalse(c["potential_trades"])
            self.assertFalse(c["screen"]["limited_screen_passed"])

    def test_queue_checkpoints_in_declared_order_and_outputs_unchanged(self):
        seen = []
        out = self.run_campaign(checkpoint=lambda q, c: seen.append((deepcopy(q), c and c["id"])))
        self.assertEqual(out, self.report)
        self.assertEqual(len(seen), 61)
        self.assertEqual([s[1] for s in seen if s[1]], [f"case-{i:03d}" for i in range(30)])
        self.assertTrue(all(q["state"] == "COMPLETED" for q in seen[-1][0]))

    def test_failed_fit_is_retained_no_retry_no_window_expansion(self):
        seen = []
        with patch("dusty.research_campaign.forecast_fold", side_effect=ValueError("fit_failed")) as fit:
            with self.assertRaisesRegex(ValueError, "fit_failed"):
                self.run_campaign(checkpoint=lambda q, c: seen.append(deepcopy(q)))
        self.assertEqual(fit.call_count, 1)
        self.assertEqual(seen[-1][0]["state"], "FAILED")
        self.assertTrue(all(q["state"] == "NOT_RUN" for q in seen[-1][1:]))

    def test_later_failure_keeps_completed_cases(self):
        from dusty.research_campaign import forecast_fold as real_fit
        calls, seen = [], []
        def fit(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ValueError("second_fold_failure")
            return real_fit(*args, **kwargs)
        with patch("dusty.research_campaign.forecast_fold", side_effect=fit):
            with self.assertRaises(ValueError):
                self.run_campaign(checkpoint=lambda q, c: seen.append(deepcopy(q)))
        self.assertTrue(all(q["state"] == "COMPLETED" for q in seen[-1][:10]))
        self.assertEqual(seen[-1][10]["state"], "FAILED")

    def test_contract_tampering_and_invalid_windows_fail(self):
        for days in (0, True, 2, 7):
            with self.assertRaises(ValueError):
                campaign_contract(START, END, days)
        contract = deepcopy(self.contract)
        contract["forecast"]["ridge_penalty"] = 0
        with self.assertRaisesRegex(ValueError, "modified"):
            self.run_campaign(contract=contract)

    def test_missing_test_fold_is_not_silently_skipped(self):
        rows = tuple(b for b in self.bars if b.at < END-timedelta(days=1))
        with self.assertRaisesRegex(ValueError, "insufficient_bars"):
            self.run_campaign(bars=rows)

    def test_fitted_model_is_frozen_per_fold_and_expands_between_folds(self):
        previous = 0
        for f in self.report["forecast_evaluation"]:
            self.assertGreater(f["model"]["pairs"], previous)
            previous = f["model"]["pairs"]
            self.assertLess(f["model"]["trained_through"], f["fold"]["start"])
            self.assertEqual({r["model_fingerprint"] for r in f["forecasts"]}, {f["model_fingerprint"]})


class CampaignLifecycleTests(unittest.TestCase):
    def test_cancel_timeout_and_crash_seal_the_whole_queue(self):
        for action, state in (("cancel", "CANCELLED"), ("timeout", "TIMED_OUT"), ("crash", "FAILED")):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                context = FakeContext()
                runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"out",
                    settings=settings(), context=context, code_checker=lambda *_: True, timeout_seconds=1)
                self.assertTrue(runtime.start(selection()).accepted)
                path = Path(runtime.poll().run_directory)
                queue = [{"state": "COMPLETED"}, {"state": "RUNNING"}] + [{"state": "PENDING"} for _ in range(28)]
                _atomic_json(path/"queue.json", {"queue": queue, "request_sha256": runtime._request_hash,
                                                "case_sha256": {}, "promotion_eligible": False})
                self.assertIn("1/30", runtime.poll().message)
                if action == "cancel":
                    runtime.emergency_halt()
                elif action == "timeout":
                    runtime._started = time.monotonic()-2
                    self.assertEqual(runtime.poll().state, "CANCELLING")
                else:
                    context.process.alive = False
                self.assertEqual(runtime.poll().state, state)
                saved = json.loads((path/"queue.json").read_text())["queue"]
                self.assertEqual(saved[0]["state"], "COMPLETED")
                self.assertEqual(saved[1]["state"], state)
                self.assertTrue(all(r["state"] == "NOT_RUN" for r in saved[2:]))
                self.assertIsNone(runtime._process)

    def test_cancel_wins_race_against_complete_worker(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"out", settings=settings(),
                context=FakeContext(), code_checker=lambda *_: True)
            runtime.start(selection())
            path = Path(runtime.poll().run_directory)
            _atomic_json(path/"result.json", {"state": "COMPLETED", "message": "race", "promotion_eligible": False})
            runtime.stop_new_entries()
            self.assertEqual(runtime.poll().state, "CANCELLED")
            self.assertEqual(read_research_result(path)["state"], "CANCELLED")

    def test_progress_cannot_claim_completion_or_rebind_run(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"out", settings=settings(),
                context=FakeContext(), code_checker=lambda *_: True)
            runtime.start(selection())
            path = Path(runtime.poll().run_directory)
            try:
                _atomic_json(path/"queue.json", {"queue": [{"state": "COMPLETED"} for _ in range(30)],
                                                "request_sha256": "forged"})
                self.assertNotIn("30/30", runtime.poll().message)
                self.assertEqual(runtime.poll().state, "RUNNING")
            finally:
                runtime.emergency_halt()
                runtime.poll()

    def test_frozen_campaign_contract_binds_dates_and_models(self):
        selected = selection()
        request = _request_payload(selected, settings(), START, END)
        self.assertEqual(request["campaign_contract"]["expected_cases"], 30)
        self.assertEqual(request["campaign_contract"]["forecast"], forecast_contract())
        self.assertNotIn("prospective_registration", request)
        self.assertFalse(request["promotion_eligible"])
        preview = settings().window_preview(END)
        self.assertIn("fold-3", preview)
        self.assertIn("No automatic winner", preview)

    def test_new_flags_do_not_change_ordinary_frozen_requests(self):
        old = _request_payload(selection(), fixed_settings(), START, END)
        self.assertNotIn("campaign", old["settings"])
        self.assertNotIn("campaign_contract", old)
        self.assertNotIn("comparison", old["settings"])

    def test_invalid_settings_future_end_and_prospective_registration_blocked(self):
        for changes in ({"campaign": "yes"}, {"comparison": True}, {"holdout_days": 0},
                        {"cost_source": ""}, {"history_days": 4}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                settings(**changes)
        with self.assertRaisesRegex(ValueError, "future"):
            settings().bounds(END-timedelta(seconds=1))
        with self.assertRaisesRegex(ValueError, "prospective"):
            _request_payload(selection(), settings(), START, END, {})
        with tempfile.TemporaryDirectory() as root:
            runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"out",
                                            settings=settings(), code_checker=lambda *_: True)
            with self.assertRaisesRegex(ValueError, "prospective"):
                runtime.register_plan(selection())
            self.assertFalse(runtime.output_directory.exists())

    def test_worker_rejects_mutated_contract_before_history_read(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            request = _request_payload(selection(), settings(), START, END)
            request["campaign_contract"]["expected_cases"] = 1
            _atomic_json(path/"request.json", request)
            with patch.object(FullWindowReader, "read") as read:
                with self.assertRaisesRegex(ValueError, "frozen_request"):
                    execute_research(selection(), settings(), path, START, END, reader=FullWindowReader())
                read.assert_not_called()

    def test_end_to_end_artifacts_queue_cases_and_viewer_are_hash_checked(self):
        from hashlib import sha256
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            selected = selection()
            _atomic_json(path/"request.json", _request_payload(selected, settings(), START, END))
            result = execute_research(selected, settings(), path, START, END, reader=FullWindowReader())
            _atomic_json(path/"result.json", result)
            self.assertEqual(read_research_result(path)["state"], "COMPLETED")
            queue = json.loads((path/"queue.json").read_text())
            self.assertEqual(len(queue["case_sha256"]), 30)
            for filename, digest in queue["case_sha256"].items():
                self.assertEqual(sha256((path/filename).read_bytes()).hexdigest(), digest)
            cases, currency = load_case_report(path)
            self.assertEqual(len(cases), 30)
            self.assertEqual(currency, "USD")
            report = json.loads((path/"report.json").read_text())
            report["campaign"]["forecast_evaluation"][0]["model"]["slope"] += 1
            _atomic_json(path/"report.json", report)
            with self.assertRaisesRegex(ValueError, "hash_mismatch"):
                load_case_report(path)

    def test_queue_seal_preserves_completed_cash_never_resumes(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            _atomic_json(path/"queue.json", {"queue": [{"state": s} for s in ("COMPLETED", "RUNNING", "PENDING")],
                                            "case_sha256": {"case-000.json": "a"*64}})
            _seal_campaign_queue(path, "CANCELLED")
            result = json.loads((path/"queue.json").read_text())
            self.assertEqual([r["state"] for r in result["queue"]], ["COMPLETED", "CANCELLED", "NOT_RUN"])
            self.assertEqual(result["case_sha256"], {"case-000.json": "a"*64})

    def test_spawn_runtime_end_to_end_uses_single_owned_worker(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = LocalResearchRuntime(Path(root)/"repo", output_directory=Path(root)/"out", settings=settings(),
                context=FixedSpawnContext(), code_checker=lambda *_: True, timeout_seconds=60)
            self.assertTrue(runtime.start(selection()).accepted)
            self.assertFalse(runtime.start(selection()).accepted)
            deadline = time.monotonic()+60
            try:
                while runtime.active and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertEqual(runtime.poll().state, "COMPLETED", runtime.poll().message)
                self.assertIn("CAMPAIGN SELECTS NO WINNER", runtime.poll().capital_label)
            finally:
                runtime.emergency_halt()


class ReadableResultsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.economics = setup_data()
        cls.report = run_forecast_campaign(cls.bars, symbol="EURUSD", economics=cls.economics,
            config=LaboratoryConfig(growth_starting_equity=100000), contract=campaign_contract(START, END, 1))

    def test_display_rounding_does_not_mutate_full_precision_evidence(self):
        before = _json(self.report)
        for case in self.report["cases"]:
            self.assertIn(case["segment"], case_label(case))
            self.assertIn("Costs unverified", case_overview(case, "USD"))
            for row in case["diagnosis"]["rows"]:
                self.assertEqual(len(trade_values(row)), 9)
                details = trade_detail(row, "USD")
                self.assertIn("Recorded entry features", details)
                self.assertNotIn("{'feature':", details)
        self.assertEqual(_json(self.report), before)

    @unittest.skipUnless(os.name == "nt" or os.environ.get("DISPLAY"), "real Tk requires a display")
    def test_real_tk_case_selection_details_no_trade_and_read_only_state(self):
        import tkinter as tk
        from tkinter import ttk
        from dusty.research_viewer import CaseExplorer
        root = tk.Tk()
        try:
            book = ttk.Notebook(root)
            book.pack(fill="both", expand=True)
            explorer = CaseExplorer(book, "not-loaded")
            explorer.set_report(self.report["cases"], "USD")
            self.assertEqual(len(explorer.selector.cget("values")), 30)
            index = next(i for i, c in enumerate(self.report["cases"]) if c["diagnosis"]["rows"])
            explorer.selector.current(index)
            explorer._select_case()
            explorer.tree.selection_set("0")
            explorer._select_trade()
            self.assertIn("Recorded entry", explorer.details.get("1.0", "end"))
            self.assertEqual(explorer.details.cget("state"), "disabled")
            explorer.selector.current(8)
            explorer._select_case()
            self.assertFalse(explorer.tree.get_children())
            self.assertIn("No trades", explorer.details.get("1.0", "end"))
            explorer._timer = explorer.frame.after(5000, lambda: None)
            explorer.frame.destroy()
            self.assertTrue(explorer._closed)
            self.assertIsNone(explorer._timer)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
