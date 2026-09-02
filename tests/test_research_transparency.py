"""Synthetic fixtures only. Never commit a user's broker/account artifacts here."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
from hashlib import sha256
from importlib.metadata import PackageNotFoundError
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dusty.basic_ui import DustyBasicUI
from dusty.features import FEATURE_NUMERICS_VERSION, FeatureBar, compute_standard_features, ema, smma, rsi
from dusty.local_research import ResearchJobView, ResearchSettings, _atomic_json, _request_payload, execute_research
from dusty.local_terminal import ReadOnlyTerminalSnapshotReader
from dusty.research_capital import capital_summary_from_report
from dusty.research_environment import runtime_provenance
from dusty.reviewed_strategies import reviewed_research_packages
from dusty.strategy_catalog import OperatingMode
import test_connected_research as research_fixtures
from test_connected_research import (
    END, START, FixtureReader, HistoryMT5, selection,
)
from test_local_runtime_ui import FakeSnapshotReader


def example_report():
    return {
        "economics": {"volume_min": 1.0},
        "config": {"growth_starting_equity": 1000.0, "growth_risk_fraction": .0025},
        "laboratory": {"growth_sizing": [
            {"equity_before": 1000.0, "approved": False,
             "sizing": {"loss_per_lot": 60.0, "allowed_loss": 2.5},
             "reasons": ["broker_minimum_volume_exceeds_risk_budget", "minimum_loss:60"]},
            {"equity_before": 1000.0, "approved": False,
             "sizing": {"loss_per_lot": 140.0, "allowed_loss": 2.5},
             "reasons": ["broker_minimum_volume_exceeds_risk_budget", "minimum_loss:140"]},
        ]},
    }


def summary(report=None):
    return capital_summary_from_report(report or example_report(), currency="USD", symbol="TEST.INDEX")


class CapitalExplanationTests(unittest.TestCase):
    def test_minimum_lot_rejections_and_highest_sample_threshold(self):
        original = example_report()
        frozen = deepcopy(original)
        result = summary(original)
        self.assertEqual((result.required_balance_low, result.preferred_balance), (24000, 56000))
        self.assertEqual(result.minimum_lot_rejections, 2)
        self.assertEqual(result.approved_candidates, 0)
        self.assertEqual(result.minimum_loss_high, 140)
        self.assertEqual(result.rejection_counts, (("broker_minimum_volume_exceeds_risk_budget", 2),))
        self.assertEqual(original, frozen)  # explanatory only, no altered trade/risk decisions
        self.assertIn("not a deposit recommendation", result.display())

    def test_actual_fractional_lot_and_reduced_risk_are_respected(self):
        report = example_report()
        report["economics"]["volume_min"] = .01
        for trace in report["laboratory"]["growth_sizing"]:
            trace["sizing"]["allowed_loss"] = 1.25
        self.assertAlmostEqual(summary(report).preferred_balance, 1120)

    def test_effective_risk_uses_each_trace_equity_not_starting_balance(self):
        report = example_report()
        report["laboratory"]["growth_sizing"][1]["equity_before"] = 2000
        self.assertEqual(summary(report).preferred_balance, 112000)

    def test_unsized_or_no_setups_never_manufacture_preferred_balance(self):
        report = example_report()
        report["laboratory"]["growth_sizing"][0].update(sizing=None, reasons=["drawdown_halt"])
        self.assertIsNone(summary(report).preferred_balance)
        self.assertIn(("drawdown_halt", 1), summary(report).rejection_counts)
        report["laboratory"]["growth_sizing"] = []
        self.assertIsNone(summary(report).preferred_balance)
        self.assertIn("unavailable", summary(report).display())

    def test_risk_threshold_does_not_hide_margin_rejection(self):
        report = example_report()
        report["laboratory"]["growth_sizing"][0]["reasons"] = ["margin_hard_limit"]
        result = summary(report)
        self.assertEqual(result.approved_candidates, 0)
        self.assertIn(("margin_hard_limit", 1), result.rejection_counts)

    def test_invalid_economics_or_budget_fail_closed(self):
        for invalid in (0, -1, float("nan"), float("inf"), True, "1"):
            with self.subTest(invalid=invalid):
                report = example_report()
                report["economics"]["volume_min"] = invalid
                with self.assertRaises(ValueError):
                    summary(report)
        report = example_report()
        report["laboratory"]["growth_sizing"][0]["sizing"]["allowed_loss"] = 0
        with self.assertRaises(ValueError):
            summary(report)

    def test_display_rounds_threshold_up_without_changing_calculation(self):
        report = example_report()
        report["laboratory"]["growth_sizing"][1]["sizing"]["loss_per_lot"] = 140.00001
        result = summary(report)
        self.assertIn("56,000.01", result.display())
        self.assertAlmostEqual(result.preferred_balance, 56000.004)


class NumericalPolicyTests(unittest.TestCase):
    def test_explicit_seed_arithmetic_preserves_311_addition_order(self):
        # Built-in sum in 3.12 compensates the tiny additions; the old seed did not.
        values = (1e16, 1.0, 1.0, 1.0)
        with patch("dusty.features.sum", side_effect=AssertionError("unversioned sum"), create=True):
            self.assertEqual(ema(values, 4)[-1], 2500000000000000.0)
            self.assertEqual(smma(values, 4)[-1], 2500000000000000.0)
            self.assertEqual(rsi((1.0, 1e16, 1.0, 2.0, 3.0, 4.0), 5)[-1], 50.0)

    def test_complete_feature_fingerprint_golden_on_every_ci_runtime(self):
        # Arithmetic-only fixture, no platform-dependent sin/random functions.
        bars = tuple(FeatureBar(START + timedelta(minutes=15 * i),
                                100.1 + (i % 11) * .03, 101.0, 99.0,
                                100.1 + (i % 11) * .03, 2.0, 100.0) for i in range(60))
        payload = json.dumps([asdict(row) for row in compute_standard_features(bars)],
                             sort_keys=True, separators=(",", ":"), default=lambda value: value.isoformat())
        self.assertEqual(sha256(payload.encode()).hexdigest(),
                         "d37d2004b7b7eb53181a8f51c527607e145df4e49dec1c98fbb42af73a29cce9")

    def test_package_fingerprint_binds_numerical_policy(self):
        package = reviewed_research_packages()[0]
        before = package.fingerprint
        with patch("dusty.reviewed_strategies.FEATURE_NUMERICS_VERSION", "future-policy"):
            self.assertNotEqual(package.fingerprint, before)

    def test_provenance_is_explicit_without_importing_terminal_bridge(self):
        with patch("dusty.research_environment.version", side_effect=PackageNotFoundError):
            data = runtime_provenance()
        self.assertEqual(data["feature_numerics"], FEATURE_NUMERICS_VERSION)
        self.assertEqual((data["float_radix"], data["float_mantissa_bits"]), (2, 53))
        self.assertEqual(data["packages"]["MetaTrader5"], "not_installed")
        self.assertNotIn("node", data)
        self.assertNotIn("username", data)

    def test_new_artifacts_record_versions_and_explanations_without_unlocking(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            selected = selection()
            request = _request_payload(selected, ResearchSettings(), START, END)
            _atomic_json(directory / "request.json", request)
            result = execute_research(selected, ResearchSettings(), directory, START, END, reader=FixtureReader())
            report = json.loads((directory / "report.json").read_text())
            self.assertEqual(request["schema"], 2)
            self.assertEqual(request["runtime"], report["runtime"])
            self.assertEqual(report["capital_summary"]["minimum_lot"], selected.symbol.volume_min)
            self.assertIn("Growth rejection counts:", result["message"])
            self.assertFalse(result["promotion_eligible"])


class AccountAndDisplayTests(unittest.TestCase):
    def app(self, root):
        return research_fixtures.ApplicationLifecycleTests().app(root)

    def test_refresh_updates_balance_preserves_same_account_selection(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            before = app.view()
            fresh = replace(before.terminal, account=replace(before.terminal.account, balance=1234, equity=900),
                            captured_at=before.terminal.captured_at + timedelta(seconds=5))
            app._snapshot_reader = FakeSnapshotReader(fresh)
            after = app.refresh_account()
            self.assertEqual(after.terminal.account.balance, 1234)
            self.assertEqual(after.selected_symbol, before.selected_symbol)
            self.assertEqual(after.selected_strategy, before.selected_strategy)
            self.assertTrue(all(not gate.available for gate in after.mode_gates if gate.mode is not OperatingMode.BACKTEST))

    def test_changed_account_same_login_hint_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            before = app.view().terminal
            fresh = replace(before, account=replace(before.account, identity_fingerprint="different-full-account"))
            app._snapshot_reader = FakeSnapshotReader(fresh)
            with self.assertRaisesRegex(ValueError, "account_or_terminal_changed"):
                app.refresh_account()
            self.assertIsNone(app.view().terminal)
            self.assertFalse(app.start().accepted)

    def test_failed_refresh_does_not_leave_stale_balance_or_allow_start(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app._snapshot_reader = SimpleNamespace(read=lambda _: (_ for _ in ()).throw(RuntimeError("offline")))
            with self.assertRaises(RuntimeError):
                app.refresh_account()
            self.assertIsNone(app.view().terminal)
            self.assertFalse(app.start().accepted)

    def test_symbol_spec_change_requires_reselection(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            before = app.view().terminal
            fresh = replace(before, symbols=(replace(before.symbols[0], volume_min=.02),))
            app._snapshot_reader = FakeSnapshotReader(fresh)
            self.assertIsNone(app.refresh_account().selected_symbol)
            self.assertFalse(app.start().accepted)

    def test_refresh_is_locked_during_research_or_development(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.start()
            with self.assertRaises(RuntimeError):
                app.refresh_account()
            app.emergency_halt()
            app.begin_development()
            with self.assertRaises(RuntimeError):
                app.refresh_account()

    def test_snapshot_detects_account_switch_during_inventory(self):
        module = HistoryMT5()
        old = module.account_info()
        changed = SimpleNamespace(**vars(old))
        changed.login += 10000
        with patch.object(module, "account_info", side_effect=[old, changed]):
            with self.assertRaisesRegex(RuntimeError, "account_changed_during_snapshot"):
                ReadOnlyTerminalSnapshotReader(module).read(module.installation)

    def test_snapshot_uses_latest_balance_and_rejects_currency_drift(self):
        for currency in ("USD", "EUR"):
            module = HistoryMT5()
            before = module.account_info()
            after = SimpleNamespace(**dict(vars(before), balance=3210, currency=currency))
            with patch.object(module, "account_info", side_effect=[before, after]):
                if currency == "USD":
                    snapshot = ReadOnlyTerminalSnapshotReader(module).read(module.installation)
                    self.assertEqual(snapshot.account.balance, 3210)
                else:
                    with self.assertRaisesRegex(RuntimeError, "account_changed_during_snapshot"):
                        ReadOnlyTerminalSnapshotReader(module).read(module.installation)

    def test_snapshot_rejects_changed_terminal_environment(self):
        module = HistoryMT5()
        before = module.terminal
        after = SimpleNamespace(**dict(vars(before), data_path="other-terminal-data"))
        with patch.object(module, "terminal_info", side_effect=[before, after]):
            with self.assertRaisesRegex(RuntimeError, "terminal_changed_during_snapshot"):
                ReadOnlyTerminalSnapshotReader(module).read(module.installation)

    def test_scope_rejects_other_symbol_strategy_account_or_broker_spec(self):
        selected = selection()
        variants = (
            replace(selected, symbol=replace(selected.symbol, symbol="OTHER")),
            replace(selected, symbol=replace(selected.symbol, volume_min=.02)),
            replace(selected, strategy=reviewed_research_packages()[1].catalog_entry),
            replace(selected, terminal=replace(selected.terminal, account=replace(
                selected.terminal.account, identity_fingerprint="other"))),
            replace(selected, terminal=replace(selected.terminal, terminal_build="new-build")),
        )
        for variant in variants:
            self.assertNotEqual(variant.research_scope, selected.research_scope)
        self.assertEqual(selected.research_scope, replace(selected, terminal=replace(selected.terminal,
            account=replace(selected.terminal.account, balance=2000))).research_scope)

    def test_application_hides_previous_selection_estimate(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            selected = app._runtime_selection()
            job = ResearchJobView("COMPLETED", "done", "some-run", summary(), selected.research_scope)
            app._runtime.poll = lambda: job
            self.assertIsNotNone(app.view().capital_summary)
            app._selected_strategy = None
            self.assertIsNone(app.view().capital_summary)

    def test_failed_refresh_callback_never_starts_research(self):
        ui = DustyBasicUI.__new__(DustyBasicUI)
        ui._closing = False
        errors = []
        ui._show_error = errors.append
        ui._application = SimpleNamespace(start=lambda: self.fail("must not start"))
        ui._start_after_account_refresh(None, "offline")
        self.assertEqual(errors, ["offline"])

    def test_headless_render_displays_balance_not_equity_and_min_lot(self):
        class Widget:
            def __init__(self):
                self.value, self.options = "", {}
            def set(self, value):
                self.value = value
            def get(self):
                return self.value
            def configure(self, **kwargs):
                self.options.update(kwargs)
        ui = DustyBasicUI.__new__(DustyBasicUI)
        ui._busy = ui._closing = False
        for name in ("terminal", "symbol", "strategy", "mode", "account", "market", "position", "status", "lot", "capital"):
            setattr(ui, f"_{name}_var", Widget())
        for name in ("terminal_box", "symbol_box", "strategy_box", "refresh_button", "connect_button", "report_button",
                     "development_button", "account_refresh_button", "start_button", "results_button"):
            setattr(ui, f"_{name}", Widget())
        ui._mode_buttons = {mode: Widget() for mode in OperatingMode}
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            ui._render(app.view())
            self.assertIn("Current balance: 20,000.00 USD", ui._account_var.get())
            self.assertIn("Equity: 19,500.00", ui._account_var.get())
            self.assertIn("not a live feed", ui._account_var.get())
            self.assertIn("0.01 lots", ui._lot_var.get())
            self.assertIn("unavailable", ui._capital_var.get())

    @unittest.skipUnless(os.name == "nt", "Windows Tk construction smoke test")
    def test_windows_tk_window_constructs_and_renders_completed_explanation(self):
        # Real Tk widgets, fake broker/controller evidence; not interactive MT5 proof.
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            job = ResearchJobView("COMPLETED", "RESEARCH COMPLETED — NOT CERTIFIED", "fixture-run",
                                  summary(), app._runtime_selection().research_scope)
            app._runtime.poll = lambda: job
            with patch.object(DustyBasicUI, "_refresh"):
                ui = DustyBasicUI(app, SimpleNamespace(), code_commit="f" * 40)
            try:
                ui._root.withdraw()
                ui._render(app.view())
                ui._root.update_idletasks()
                self.assertIn("56,000.00 USD", ui._capital_var.get())
                self.assertIn("0.01 lots", ui._lot_var.get())
                self.assertEqual(str(ui._account_refresh_button.cget("state")), "normal")
                self.assertEqual(str(ui._mode_buttons[OperatingMode.DEMO].cget("state")), "disabled")
                self.assertEqual(str(ui._mode_buttons[OperatingMode.LIVE].cget("state")), "disabled")
            finally:
                ui._root.destroy()


if __name__ == "__main__":
    unittest.main()
