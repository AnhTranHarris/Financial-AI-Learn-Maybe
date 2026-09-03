from copy import deepcopy
from dataclasses import replace
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
from dusty.features import compute_standard_features
from dusty.investment_lab import LaboratoryConfig
from dusty.local_research import _atomic_json, _request_payload, execute_research, read_research_result
from dusty.research_comparison import run_research_comparison, comparison_summary
from dusty.research_diagnosis import (DIAGNOSIS_PROTOCOL, TRADE_DETAILS_SEPARATOR, _cash,
                                     attribute_cost_pair, diagnose_case)
from dusty.research_eligibility import EntryPolicy
from dusty.research_evaluation import FixedEvaluationPlan
from dusty.reviewed_strategies import reviewed_research_packages
from test_connected_research import START, END, selection
from test_fixed_evaluation import FullWindowReader, fixed_settings
from test_research_comparison import setup_data, comparison_settings


class DiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars, cls.economics = setup_data()
        cls.package = reviewed_research_packages()[0]
        cls.vectors = {v.at: v for v in compute_standard_features(cls.bars, cls.package.features)}
        cls.config = LaboratoryConfig(growth_starting_equity=100000, commission_per_lot=1,
                                     expected_slippage_price=.00002)
        cls.plan = FixedEvaluationPlan(START, END-timedelta(days=2), END)
        cls.report = run_research_comparison(cls.bars, symbol="EURUSD", economics=cls.economics,
                                            config=cls.config, plan=cls.plan)
        cls.case = cls.report["cases"][0]
        assert cls.case["potential_trades"]

    def diagnose(self, case=None, **kwargs):
        return diagnose_case(case or self.case, strategy=self.package.compiled, policy=EntryPolicy.SEED,
                             vectors=kwargs.get("vectors", self.vectors), bars=kwargs.get("bars", self.bars),
                             economics=self.economics)

    def test_diagnosis_is_additive_deterministic_and_does_not_mutate_case(self):
        original = deepcopy(self.case)
        self.assertEqual(self.diagnose(), original["diagnosis"])
        self.assertEqual(self.case, original)
        self.assertFalse(self.diagnose()["causal_explanation_claimed"])
        self.assertFalse(self.diagnose()["promotion_eligible"])

    def test_all_twenty_cases_and_both_ledgers_reconcile(self):
        for case in self.report["cases"]:
            d = case["diagnosis"]
            self.assertEqual(d["source_case_fingerprint"], case["case_fingerprint"])
            self.assertEqual(len(d["rows"]), len(case["potential_trades"]))
            for name in ("growth", "minimum_lot"):
                t, ledger = d["totals"][name], case[name+"_backtest"]
                self.assertAlmostEqual(t["net_pnl"], ledger["net_pnl"], places=7)
                self.assertAlmostEqual(t["gross_pnl"]-t["total_cost"], t["net_pnl"], places=7)
                self.assertEqual(t["trade_count"], ledger["trade_count"])
                self.assertEqual(t["wins"]+t["losses"]+t["flat"], t["trade_count"])

    def test_entry_rules_use_recorded_observations_not_outcomes(self):
        for row in self.diagnose()["rows"]:
            context = row["entry_context"]
            self.assertEqual(context["available_at"], row["entry_at"])
            self.assertLess(context["source_open_at"], context["available_at"])
            self.assertTrue(all(g["passed"] for g in context["rule_groups"]))
            features = self.vectors[row["entry_at"]].feature_map()
            for group in context["rule_groups"]:
                for clause in group["clauses"]:
                    self.assertEqual(clause["observed"], features[clause["feature"]])
                    self.assertTrue(clause["passed"])

    def test_future_features_are_not_read_by_diagnosis(self):
        used = {t["entry_at"] for t in self.case["potential_trades"]}
        only_entries = {at: value for at, value in self.vectors.items() if at in used}
        self.assertEqual(self.diagnose(vectors=only_entries), self.diagnose())

    def test_missing_or_inconsistent_entry_evidence_fails(self):
        first = self.case["potential_trades"][0]["entry_at"]
        missing = dict(self.vectors)
        del missing[first]
        with self.assertRaisesRegex(ValueError, "identity_or_boundary"):
            self.diagnose(vectors=missing)
        changed = dict(self.vectors)
        changed[first] = replace(changed[first], at=first+timedelta(minutes=15))
        with self.assertRaisesRegex(ValueError, "entry_context"):
            self.diagnose(vectors=changed)

    def test_trade_identity_side_hash_and_boundaries_are_checked(self):
        for field, value in (("side", "short"), ("strategy_hash", "forged"), ("entry_at", END)):
            case = deepcopy(self.case)
            case["potential_trades"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.diagnose(case)
        case = deepcopy(self.case)
        case["growth_sizing"][0]["trade_id"] = "forged"
        with self.assertRaises(ValueError):
            self.diagnose(case)

    def test_duplicate_out_of_order_bars_and_trace_length_mismatch_fail(self):
        for bars in (self.bars+self.bars[:1], self.bars[::-1]):
            with self.assertRaisesRegex(ValueError, "ordered_bars"):
                self.diagnose(bars=bars)
        case = deepcopy(self.case)
        case["growth_sizing"] = case["growth_sizing"][:-1]
        with self.assertRaises(ValueError):
            self.diagnose(case)

    def test_cash_and_counts_cannot_silently_disagree(self):
        for field in ("net_pnl", "ending_balance", "trade_count"):
            case = deepcopy(self.case)
            case["growth_backtest"][field] += 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.diagnose(case)
        case = deepcopy(self.case)
        case["growth_sizing"][0]["expected_net_pnl"] += 1
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            self.diagnose(case)

    def test_costs_and_nonfinite_inputs_fail_closed(self):
        for value in (math.nan, math.inf, -math.inf, True, "1", -1):
            case = deepcopy(self.case)
            case["config"]["expected_slippage_price"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.diagnose(case)
        case = deepcopy(self.case)
        case["growth_sizing"][0]["spread_price_used"] += .1
        with self.assertRaisesRegex(ValueError, "reconciliation"):
            self.diagnose(case)

    def test_simple_cost_cash_arithmetic(self):
        cash = _cash(10, {"spread_cost": 2, "slippage_cost": 1, "commission_cost": 3}, 4)
        self.assertEqual(cash["gross_pnl"], 40)
        self.assertEqual(cash["total_cost"], 24)
        self.assertEqual(cash["net_pnl"], 16)
        self.assertEqual(_cash(-10, {"spread_cost": 2}, 4)["net_pnl"], -48)

    def test_short_cash_direction_and_cost_components(self):
        for case in self.report["cases"]:
            for row in case["diagnosis"]["rows"]:
                sign = 1 if row["side"] == "long" else -1
                g = row["growth"]
                expected = sign*(row["exit_price"]-row["entry_price"])/self.economics.tick_size*self.economics.tick_value*g["volume"]
                self.assertAlmostEqual(g["gross_pnl"], expected, places=7)
                self.assertAlmostEqual(g["total_cost"], g["spread_cost"]+g["slippage_cost"]+g["commission_cost"], places=7)

    def test_zero_control_has_no_fake_trades_or_forecast_success(self):
        for case in self.report["cases"]:
            if case["candidate_id"] == "no-trade":
                d = case["diagnosis"]
                self.assertFalse(d["rows"])
                self.assertFalse(d["growth_exit_counts"])
                self.assertEqual(d["totals"]["growth"]["net_pnl"], 0)
                self.assertFalse(d["promotion_eligible"])

    def test_rejected_growth_entries_preserve_minimum_lot_evidence(self):
        report = run_research_comparison(self.bars, symbol="EURUSD", economics=self.economics,
                    config=replace(self.config, growth_starting_equity=1), plan=self.plan)
        rows = [r for c in report["cases"] for r in c["diagnosis"]["rows"]]
        self.assertTrue(rows)
        self.assertTrue(all(not r["growth_approved"] and r["growth"]["net_pnl"] == 0 for r in rows))
        self.assertTrue(all(r["minimum_lot"]["volume"] == self.economics.volume_min for r in rows))
        self.assertTrue(all(r["growth_rejection_reasons"] for r in rows))

    def test_cost_attribution_is_exact_with_no_approval(self):
        for pair in self.report["cost_attribution"]:
            self.assertFalse(pair["risk_feasibility_retested"])
            self.assertFalse(pair["promotion_eligible"])
            self.assertAlmostEqual(pair["baseline_net_pnl"]+pair["direct_cost_effect"], pair["fixed_volume_stressed_net_pnl"], places=7)
            self.assertAlmostEqual(pair["fixed_volume_stressed_net_pnl"]+pair["sizing_and_selection_effect"], pair["stressed_resized_net_pnl"], places=7)
            for row in pair["rows"]:
                expected = -10*self.economics.point_size/self.economics.tick_size*self.economics.tick_value*row["baseline_volume"]
                self.assertAlmostEqual(row["direct_cost_effect"], expected, places=7)

    def test_pair_mismatch_fails_and_changed_trade_path_declines_attribution(self):
        baseline, stressed = self.report["cases"][0], deepcopy(self.report["cases"][2])
        stressed["candidate_id"] = "other"
        with self.assertRaises(ValueError):
            attribute_cost_pair(baseline, stressed)
        stressed = deepcopy(self.report["cases"][2])
        stressed["potential_trades"][0]["exit_price"] += .1
        result = attribute_cost_pair(baseline, stressed)
        self.assertEqual(result["status"], "UNAVAILABLE_TRADE_PATH_CHANGED")
        self.assertNotIn("fixed_volume_stressed_net_pnl", result)

    def test_summary_separates_details_and_labels_risk_limits(self):
        overview, separator, details = comparison_summary(self.report, "USD").partition(TRADE_DETAILS_SEPARATOR)
        self.assertTrue(separator)
        self.assertIn("NOT reapproved", overview)
        self.assertIn("stressed fixed-size", overview)
        self.assertIn("Entry features", details)
        self.assertIn("swaps/fees are incomplete", details)

    def test_holdout_prices_cannot_change_development_diagnostic_rows(self):
        changed = tuple(replace(b, open=b.open*2, high=b.high*2, low=b.low*2,
                                close=b.close*2, execution_price=b.execution_price*2)
                        if b.at >= self.plan.holdout_start else b for b in self.bars)
        other = run_research_comparison(changed, symbol="EURUSD", economics=self.economics,
                                        config=self.config, plan=self.plan)
        for old, new in zip(self.report["cases"], other["cases"], strict=True):
            if old["segment"] == "development":
                self.assertEqual(old["diagnosis"]["rows"], new["diagnosis"]["rows"])
                self.assertEqual(old["diagnosis"]["totals"], new["diagnosis"]["totals"])


class CostAttributionArithmeticTests(unittest.TestCase):
    def pair(self, before_volume=5, after_volume=4):
        def case(scenario, volume, cost):
            row = {"trade_id": "growth-000000", "entry_at": START,
                   "cost_per_lot": {"spread_cost": 0, "slippage_cost": cost, "commission_cost": 0},
                   "growth": _cash(-10, {"spread_cost": 0, "slippage_cost": cost, "commission_cost": 0}, volume)}
            return {"candidate_id": "long:seed-v1", "segment": "holdout", "case_fingerprint": scenario,
                    "cost_scenario": scenario, "potential_trades": [{"entry_at": START, "exit_at": END}],
                    "diagnosis": {"rows": [row], "totals": {"growth": {"net_pnl": row["growth"]["net_pnl"]}}}}
        return attribute_cost_pair(case("configured", before_volume, 1), case("stress-plus-10-points", after_volume, 2))

    def test_higher_cost_can_lose_less_only_because_size_shrinks(self):
        p = self.pair()
        self.assertEqual(p["baseline_net_pnl"], -55)
        self.assertEqual(p["fixed_volume_stressed_net_pnl"], -60)
        self.assertEqual(p["stressed_resized_net_pnl"], -48)
        self.assertEqual(p["direct_cost_effect"], -5)
        self.assertEqual(p["sizing_and_selection_effect"], 12)
        self.assertEqual(p["volume_changed_trades"], 1)

    def test_identical_volumes_have_no_sizing_effect(self):
        p = self.pair(after_volume=5)
        self.assertEqual(p["sizing_and_selection_effect"], 0)
        self.assertEqual(p["volume_changed_trades"], 0)

    def test_rejected_stressed_entry_is_selection_not_negative_fees(self):
        p = self.pair(after_volume=0)
        self.assertEqual(p["direct_cost_effect"], -5)
        self.assertEqual(p["sizing_and_selection_effect"], 60)
        self.assertEqual(p["stressed_resized_net_pnl"], 0)

    def test_no_baseline_position_has_no_direct_cash_cost_effect(self):
        p = self.pair(before_volume=0)
        self.assertEqual(p["direct_cost_effect"], 0)
        self.assertEqual(p["sizing_and_selection_effect"], -48)


class DiagnosticIntegrationTests(unittest.TestCase):
    def test_protocol_frozen_only_for_comparison(self):
        original = _request_payload(selection(), fixed_settings(), START, END)
        self.assertNotIn("diagnostic_protocol", original)
        payload = _request_payload(selection(), comparison_settings(), START, END)
        self.assertEqual(payload["diagnostic_protocol"], DIAGNOSIS_PROTOCOL)

    def test_corrupt_protocol_rejected_before_acquisition(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            selected, settings = selection(), comparison_settings()
            payload = _request_payload(selected, settings, START, END)
            payload["diagnostic_protocol"] = "other"
            _atomic_json(path/"request.json", payload)
            with patch.object(FullWindowReader, "read", side_effect=AssertionError("no read")):
                with self.assertRaisesRegex(ValueError, "frozen_request"):
                    execute_research(selected, settings, path, START, END, reader=FullWindowReader())

    def test_diagnostic_failure_prevents_completed_report(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            selected, settings = selection(), comparison_settings()
            _atomic_json(path/"request.json", _request_payload(selected, settings, START, END))
            with patch("dusty.research_comparison.diagnose_case", side_effect=ValueError("diagnostic_failure")):
                with self.assertRaisesRegex(ValueError, "diagnostic_failure"):
                    execute_research(selected, settings, path, START, END, reader=FullWindowReader())
            self.assertFalse((path/"report.json").exists())

    def test_completed_diagnosis_is_hash_bound(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            selected, settings = selection(), comparison_settings()
            _atomic_json(path/"request.json", _request_payload(selected, settings, START, END))
            result = execute_research(selected, settings, path, START, END, reader=FullWindowReader())
            _atomic_json(path/"result.json", result)
            self.assertEqual(read_research_result(path)["state"], "COMPLETED")
            report = json.loads((path/"report.json").read_text())
            self.assertEqual(report["comparison"]["diagnostic_protocol"], DIAGNOSIS_PROTOCOL)
            overview, _, details = result["message"].partition(TRADE_DETAILS_SEPARATOR)
            self.assertIn("SELECTED SEED BASELINE DETAILS", overview)
            self.assertIn("POST-RUN DIAGNOSIS", details)
            report["comparison"]["cases"][0]["diagnosis"]["totals"]["growth"]["net_pnl"] += 1
            _atomic_json(path/"report.json", report)
            with self.assertRaisesRegex(ValueError, "hash_mismatch"):
                read_research_result(path)

    @unittest.skipUnless(os.name == "nt", "Windows real Tk viewer lifecycle")
    def test_windows_viewer_updates_and_stays_bound_to_original_run(self):
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
        current = SimpleNamespace(run_directory="one", runtime_message="Reading history", runtime_active=True)
        ui = DustyBasicUI.__new__(DustyBasicUI)
        ui._tk, ui._ttk, ui._root, ui._closing = tk, ttk, root, False
        ui._application = SimpleNamespace(view=lambda: current)
        callbacks = []
        try:
            with patch.object(tk.Toplevel, "after", side_effect=lambda delay, callback: callbacks.append(callback) or "fake"):
                ui._show_research_result()
                window = next(w for w in root.winfo_children() if isinstance(w, tk.Toplevel))
                book = next(w for w in window.winfo_children() if isinstance(w, ttk.Notebook))
                texts = [next(w for w in frame.winfo_children() if isinstance(w, tk.Text))
                         for frame in book.winfo_children()[:2]]
                self.assertIn("Reading history", texts[0].get("1.0", "end"))
                current.runtime_message = "COMPLETED"+TRADE_DETAILS_SEPARATOR+"Trade cash details"
                callbacks.pop(0)()
                self.assertIn("COMPLETED", texts[0].get("1.0", "end"))
                self.assertIn("Trade cash details", texts[1].get("1.0", "end"))
                self.assertTrue(all(t.cget("state") == "disabled" for t in texts))
                current.run_directory, current.runtime_message = "two", "unrelated run"
                callbacks.pop(0)()
                self.assertNotIn("unrelated", texts[0].get("1.0", "end"))
                self.assertFalse(callbacks)
                # A completed viewer schedules nothing; close cancels a running viewer timer.
                current.runtime_active = False
                ui._show_research_result()
                self.assertFalse(callbacks)
                current.runtime_active = True
                ui._show_research_result()
                newest = root.winfo_children()[-1]
                with patch.object(tk.Toplevel, "after_cancel") as cancel:
                    newest.tk.call(newest.protocol("WM_DELETE_WINDOW"))
                    cancel.assert_called_once_with("fake")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
