from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from dusty.basic_ui import DustyBasicUI
from dusty.closed_position_costs import observe_closed_positions, reconcile_closed_positions
from dusty.local_research import LocalResearchRuntime, ResearchSettings, SelectedTerminalHistoryReader, _atomic_json, _json, _request_payload, execute_research, read_research_result
from dusty.prospective_research import ProspectiveRegistry, SCREEN, digest, screen_result, validate_for_evaluation, validate_receipt
from dusty.strategy_catalog import OperatingMode
from test_connected_research import START, END, FakeContext, selection
import test_connected_research as connected_fixtures
from test_fixed_evaluation import FullWindowReader


CREATED = START + timedelta(days=4)


def fixture_request():
    selected = selection()
    selected = replace(selected, terminal=replace(selected.terminal, captured_at=CREATED - timedelta(minutes=1)))
    settings = ResearchSettings(history_days=7, fixed_end=END, holdout_days=2, cost_source="Synthetic, unverified")
    request = json.loads(_json(_request_payload(selected, settings, START, END)))
    return selected, settings, request


def prospective_fixture_worker(selected, settings, directory, start, end, repository, receipt):
    result = execute_research(selected, settings, directory, start, end, reader=FullWindowReader(), registration=receipt)
    _atomic_json(directory / "result.json", result)


class ProspectiveSpawnContext:
    def Process(self, *, target, args, daemon):
        return multiprocessing.get_context("spawn").Process(target=prospective_fixture_worker, args=args, daemon=daemon)


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.registry = ProspectiveRegistry(self.root / "plans.sqlite3")
        self.selected, self.settings, self.request = fixture_request()

    def receipt(self):
        return self.registry.register(self.request, now=CREATED)

    def test_receipt_persists_exact_rules_costs_capital_runtime_and_screen(self):
        receipt = self.receipt()
        self.assertEqual(self.registry.get(receipt["plan_id"]), receipt)
        self.assertEqual(validate_receipt(receipt), self.request)
        self.assertEqual(receipt["payload"]["screen"], SCREEN)
        self.assertEqual(receipt["payload"]["timestamp_authority"], "LOCAL_CLOCK_NOT_INDEPENDENTLY_ATTESTED")
        self.assertIsNone(self.registry.list_plans()[0]["run_id"])

    def test_registering_at_or_after_holdout_start_is_rejected(self):
        holdout = datetime.fromisoformat(self.request["evaluation_plan"]["holdout_start"])
        for now in (holdout, holdout + timedelta(seconds=1), END):
            with self.subTest(now=now), self.assertRaisesRegex(ValueError, "register_before"):
                self.registry.register(self.request, now=now)

    def test_naive_clock_future_snapshot_and_missing_split_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.register(self.request, now=CREATED.replace(tzinfo=None))
        for key, value in (("snapshot_at", END.isoformat()), ("evaluation_plan", None)):
            bad = dict(self.request, **{key: value})
            with self.assertRaises(ValueError):
                self.registry.register(bad, now=CREATED)

    def test_duplicate_plan_does_not_become_a_new_trial(self):
        self.receipt()
        newer = dict(self.request, snapshot_at=CREATED.isoformat())
        with self.assertRaisesRegex(ValueError, "already_registered"):
            self.registry.register(newer, now=CREATED + timedelta(seconds=1))
        self.assertEqual(len(self.registry.list_plans()), 1)

    def test_hash_tamper_or_changed_protocol_is_rejected(self):
        receipt = self.receipt()
        for field, value in (("screen", dict(SCREEN, minimum_closed_trades=1)), ("protocol", "changed")):
            bad = deepcopy(receipt)
            bad["payload"][field] = value
            with self.assertRaises(ValueError):
                validate_receipt(bad)
            bad["plan_id"] = digest(bad["payload"])
            with self.assertRaises(ValueError):
                validate_receipt(bad)

    def test_ready_time_is_enforced_before_claim(self):
        receipt = self.receipt()
        with self.assertRaisesRegex(ValueError, "not_finished"):
            self.registry.claim(receipt["plan_id"], current=self.request, now=END - timedelta(seconds=1), run_id="a" * 32)
        self.assertIsNone(self.registry.list_plans()[0]["run_id"])

    def test_changed_rules_costs_runtime_account_symbol_and_code_are_rejected(self):
        receipt = self.receipt()
        for field, value in (("code_commit", "a" * 40), ("account_fingerprint", "other"),
                             ("package_fingerprint", "other"), ("terminal_build", "new"),
                             ("symbol", dict(self.request["symbol"], volume_min=10)),
                             ("runtime", dict(self.request["runtime"], python="other")),
                             ("settings", dict(self.request["settings"], commission_per_lot=1))):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "configuration_changed"):
                validate_for_evaluation(receipt, dict(self.request, **{field: value}), END)

    def test_current_balance_refresh_allowed_but_frozen_capital_retained(self):
        receipt = self.receipt()
        fresh = dict(self.request, snapshot_at=END.isoformat(), growth_starting_balance=123456)
        validate_for_evaluation(receipt, fresh, END)
        self.assertNotEqual(receipt["payload"]["request"]["growth_starting_balance"], fresh["growth_starting_balance"])

    def test_one_atomic_claim_across_concurrent_callers(self):
        receipt = self.receipt()
        def claim(index):
            try:
                self.registry.claim(receipt["plan_id"], current=self.request, now=END, run_id=f"{index:032x}")
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(claim, (1, 2))), 1)
        self.assertIsNotNone(self.registry.list_plans()[0]["run_id"])

    def test_corrupt_database_receipt_never_rebinds_identity(self):
        receipt = self.receipt()
        with sqlite3.connect(self.registry.path) as db:
            db.execute("UPDATE plans SET receipt=?", ('{}',))
        with self.assertRaises(ValueError):
            self.registry.get(receipt["plan_id"])

    def test_invalid_ids_cannot_be_used_as_paths(self):
        for value in ("../bad", "x" * 64, ""):
            with self.assertRaises(ValueError):
                self.registry.get(value)

    def test_frozen_screen_failures_and_pass_never_authorize_trading(self):
        receipt = self.receipt()
        baseline = {"trade_count": 20, "net_pnl": 1.0, "max_drawdown_fraction": .02}
        result = screen_result(receipt, {"growth_backtest": baseline})
        self.assertTrue(result["screen_passed"])
        self.assertFalse(result["promotion_eligible"])
        self.assertIn("broker_costs_not_verified", result["remaining_evidence"])
        for key, value in (("trade_count", 19), ("net_pnl", 0), ("max_drawdown_fraction", .020001)):
            self.assertFalse(screen_result(receipt, {"growth_backtest": dict(baseline, **{key: value})})["screen_passed"])
        for key, value in (("trade_count", True), ("net_pnl", float("nan")), ("max_drawdown_fraction", float("inf"))):
            with self.assertRaises(ValueError):
                screen_result(receipt, {"growth_backtest": dict(baseline, **{key: value})})


class ProspectiveRuntimeTests(unittest.TestCase):
    def runtime(self, root, **kwargs):
        return LocalResearchRuntime(Path(root) / "repo", output_directory=Path(root) / "evidence",
                                    code_checker=lambda *_: True, **kwargs)

    def test_freeze_future_plan_exports_receipt_and_does_not_spawn(self):
        now = datetime.now(timezone.utc)
        end = (now + timedelta(days=8)).replace(hour=0, minute=0, second=0, microsecond=0)
        with tempfile.TemporaryDirectory() as root:
            context = SimpleNamespace(Process=lambda **_: self.fail("registration must not spawn research"))
            runtime = self.runtime(root, context=context, settings=ResearchSettings(14, fixed_end=end, holdout_days=7, cost_source="Unverified"))
            selected = selection()
            receipt = runtime.register_plan(selected)
            path = runtime.output_directory / "prospective-plans" / (receipt["plan_id"] + ".json")
            self.assertEqual(json.loads(path.read_text()), receipt)
            self.assertFalse(runtime.active)
            self.assertFalse(runtime.evaluate_plan(selected, receipt["plan_id"]).accepted)
            self.assertIsNone(runtime.plans.list_plans()[0]["run_id"])

    def test_freeze_requires_cost_note_split_and_clean_code(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = self.runtime(root)
            with self.assertRaises(ValueError):
                runtime.register_plan(selection())
            runtime._code_checker = lambda *_: False
            with self.assertRaisesRegex(ValueError, "dirty_or_changed"):
                runtime.register_plan(selection())

    def test_cancellation_consumes_plan_and_records_failed_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = self.runtime(root, context=FakeContext())
            selected, settings, request = fixture_request()
            receipt = runtime.plans.register(request, now=CREATED)
            self.assertTrue(runtime.evaluate_plan(selected, receipt["plan_id"]).accepted)
            runtime.emergency_halt()
            self.assertEqual(runtime.poll().state, "CANCELLED")
            self.assertFalse(runtime.evaluate_plan(selected, receipt["plan_id"]).accepted)
            self.assertIn("already_attempted", runtime.poll().message)

    def test_real_spawn_uses_registered_capital_after_balance_refresh(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = self.runtime(root, context=ProspectiveSpawnContext())
            selected, settings, request = fixture_request()
            receipt = runtime.plans.register(request, now=CREATED)
            fresh = replace(selected, terminal=replace(selected.terminal, captured_at=END,
                account=replace(selected.terminal.account, balance=54321)))
            self.assertTrue(runtime.evaluate_plan(fresh, receipt["plan_id"]).accepted)
            try:
                deadline = time.monotonic() + 30
                while runtime.active and time.monotonic() < deadline:
                    time.sleep(.02)
                job = runtime.poll()
                self.assertEqual(job.state, "COMPLETED", job.message)
                self.assertIn("REGISTERED HOLDOUT", job.capital_label)
                directory = Path(job.run_directory)
                result = read_research_result(directory)
                report = json.loads((directory / "report.json").read_text())
                frozen = json.loads((directory / "request.json").read_text())
                self.assertEqual(frozen["prospective_registration"], receipt)
                self.assertEqual(report["config"]["growth_starting_equity"], request["growth_starting_balance"])
                self.assertNotEqual(report["config"]["growth_starting_equity"], 54321)
                self.assertFalse(report["prospective_screen"]["promotion_eligible"])
                self.assertIn("REGISTERED HOLDOUT COMPLETED", result["message"])
                self.assertIn("54,321.00", result["message"])
            finally:
                runtime.emergency_halt()
                if runtime._process:
                    runtime._process.join(timeout=5)
                    runtime.poll()

    def test_changed_frozen_cost_request_rejected_before_history_acquisition(self):
        selected, settings, request = fixture_request()
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            registry = ProspectiveRegistry(directory / "plans.sqlite3")
            receipt = registry.register(request, now=CREATED)
            _atomic_json(directory / "request.json", _request_payload(selected, settings, START, END, receipt))
            reader = SimpleNamespace(read=lambda *_: self.fail("must not acquire"))
            with self.assertRaisesRegex(ValueError, "configuration_changed"):
                execute_research(selected, replace(settings, commission_per_lot=1), directory, START, END,
                                 reader=reader, registration=receipt)

    def test_failed_account_refresh_never_registers(self):
        ui = DustyBasicUI.__new__(DustyBasicUI)
        ui._closing = False
        errors = []
        ui._show_error = errors.append
        ui._application = SimpleNamespace(register_prospective_plan=lambda: self.fail("must not register"))
        ui._register_after_refresh(None, "offline")
        self.assertEqual(errors, ["offline"])

    def test_application_halt_and_development_block_registration(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = self.runtime(root)
            app = connected_fixtures.ApplicationLifecycleTests().app(root, runtime)
            app.emergency_halt()
            with self.assertRaises(ValueError):
                app.register_prospective_plan()
            self.assertFalse(app.start(plan_id="a" * 64).accepted)
            app.begin_development()
            with self.assertRaises(RuntimeError):
                app.register_prospective_plan()

    @unittest.skipUnless(os.name == "nt", "Windows real Tk future-plan lifecycle")
    def test_windows_tk_freeze_receipt_and_saved_plans(self):
        with tempfile.TemporaryDirectory() as root:
            future = (datetime.now(timezone.utc) + timedelta(days=8)).replace(hour=0, minute=0, second=0, microsecond=0)
            runtime = self.runtime(root, settings=ResearchSettings(14, fixed_end=future, holdout_days=7, cost_source="Synthetic unverified costs"))
            app = connected_fixtures.ApplicationLifecycleTests().app(root, runtime)
            with patch.object(DustyBasicUI, "_refresh"):
                ui = DustyBasicUI(app, SimpleNamespace(), code_commit="f" * 40, research=runtime)
            errors = []
            ui._show_error = errors.append
            ui._background = lambda action, callback: callback(action(), None)
            try:
                ui._root.update_idletasks()
                ui._start()
                window = next(w for w in ui._root.winfo_children() if isinstance(w, ui._tk.Toplevel))
                button = next(w for w in window.winfo_children() if isinstance(w, ui._ttk.Button) and "Freeze future" in w.cget("text"))
                button.invoke()
                ui._root.update_idletasks()
                self.assertFalse(errors)
                self.assertEqual(len(runtime.plans.list_plans()), 1)
                self.assertFalse(runtime.active)
                for window in ui._root.winfo_children():
                    if isinstance(window, ui._tk.Toplevel):
                        window.destroy()
                ui._show_plans()
                ui._root.update_idletasks()
                window = next(w for w in ui._root.winfo_children() if isinstance(w, ui._tk.Toplevel))
                combo = next(w for w in window.winfo_children() if isinstance(w, ui._ttk.Combobox))
                self.assertIn("WAITING", combo.get())
                self.assertEqual(str(ui._mode_buttons[OperatingMode.DEMO].cget("state")), "disabled")
                self.assertEqual(str(ui._mode_buttons[OperatingMode.LIVE].cget("state")), "disabled")
                inspect = next(w for w in window.winfo_children() if isinstance(w, ui._ttk.Button) and "View / copy" in w.cget("text"))
                inspect.invoke()
                ui._root.update_idletasks()
                self.assertIsNone(ui._root.grab_current())
            finally:
                ui._root.destroy()


class ClosedPositionCostTests(unittest.TestCase):
    def deal(self, ticket=1, entry=0, volume=1.0, **changes):
        values = dict(ticket=ticket, position_id=77, symbol="NASUSD", type=0 if entry==0 else 1,
                      entry=entry, volume=volume, price=100.0, profit=0.0,
                      commission=-2.0*volume, fee=-.5*volume, swap=0.0,
                      time_msc=int((START+timedelta(hours=ticket)).timestamp()*1000))
        return SimpleNamespace(**dict(values, **changes))

    def run_rows(self, rows):
        return reconcile_closed_positions(rows, "NASUSD", START, END)

    def test_signed_costs_and_complete_partial_fills_reconcile(self):
        rows = [self.deal(1, volume=.4), self.deal(2, volume=.6),
                self.deal(3, entry=1, volume=.25, profit=5), self.deal(4, entry=1, volume=.75, profit=15, swap=-1)]
        result = self.run_rows(rows[::-1])
        self.assertEqual(result["status"], "OBSERVED_CLOSED_POSITION_ARITHMETIC_ONLY")
        position = result["positions"][0]
        self.assertEqual(position["opened_lots"], 1)
        self.assertEqual(position["net_cash"], 14)
        self.assertEqual(position["observed_commission_charge_per_round_trip_lot"], 4)
        self.assertEqual(position["observed_fee_charge_per_round_trip_lot"], 1)
        self.assertFalse(result["schedule_verified"])
        self.assertFalse(result["used_as_simulation_costs"])

    def test_empty_and_deposit_only_do_not_establish_free_trading(self):
        for rows in ([], [SimpleNamespace(symbol="", type=2, profit=100000)]):
            result = self.run_rows(rows)
            self.assertFalse(result["positions"])
            self.assertFalse(result["schedule_verified"])

    def test_open_partial_missing_entry_and_overclosed_positions_rejected(self):
        for rows in ([self.deal()], [self.deal(),self.deal(2,entry=1,volume=.5)],
                     [self.deal(2,entry=1)], [self.deal(),self.deal(2,entry=1,volume=2)]):
            self.assertFalse(self.run_rows(rows)["positions"])

    def test_reversal_close_by_canceled_or_inconsistent_direction_rejected(self):
        for changes in ({"entry":2},{"entry":3},{"type":13},{"type":0}):
            row = SimpleNamespace(**dict(vars(self.deal(2,entry=1)), **changes))
            self.assertFalse(self.run_rows([self.deal(),row])["positions"])

    def test_missing_fields_nonfinite_costs_and_out_of_window_rejected(self):
        for changes in ({"commission":None},{"fee":float("nan")},{"profit":float("inf")},
                        {"volume":True},{"time_msc":0},{"ticket":None},{"position_id":None}):
            row = SimpleNamespace(**dict(vars(self.deal(2,entry=1)), **changes))
            self.assertFalse(self.run_rows([self.deal(),row])["positions"])

    def test_duplicate_tickets_and_ambiguous_timing_rejected(self):
        first=self.deal()
        self.assertFalse(self.run_rows([first,first,self.deal(2,entry=1)])["positions"])
        self.assertFalse(self.run_rows([first,self.deal(2,entry=1,time_msc=first.time_msc)])["positions"])

    def test_rebates_keep_sign_and_do_not_become_assumed_tariff(self):
        result=self.run_rows([self.deal(commission=1),self.deal(2,entry=1,commission=1)])
        self.assertEqual(result["positions"][0]["observed_commission_charge_per_round_trip_lot"],-2)
        self.assertFalse(result["schedule_verified"])

    def test_oversized_history_and_other_symbol_are_not_used(self):
        self.assertFalse(self.run_rows([self.deal(symbol="OTHER")])["positions"])
        self.assertEqual(self.run_rows([self.deal()]*10001)["status"],"BOUNDED_READ_LIMIT_EXCEEDED")

    def test_recent_balanced_slice_is_not_a_complete_position(self):
        recent = [self.deal(), self.deal(2,entry=1)]
        older = self.deal(3,time_msc=int((START-timedelta(days=1)).timestamp()*1000))
        native = SimpleNamespace(history_deals_get=lambda **kwargs: [older]+recent)
        result = observe_closed_positions(native,recent,"NASUSD",START,END)
        self.assertFalse(result["positions"])
        self.assertIn("full_position_history_differs_from_window",result["excluded_reasons"])

    def test_full_native_position_query_matches_exact_rows_and_remains_unverified(self):
        rows = [self.deal(), self.deal(2,entry=1)]
        calls = []
        def history(**kwargs):
            calls.append(kwargs)
            return rows[::-1]
        result=observe_closed_positions(SimpleNamespace(history_deals_get=history),rows,"NASUSD",START,END)
        self.assertEqual(calls,[{"position":77}])
        self.assertEqual(len(result["positions"]),1)
        self.assertFalse(result["schedule_verified"])

    def test_native_full_position_reads_are_bounded_and_unavailable_is_unknown(self):
        rows = [self.deal(i+1,position_id=i+1) for i in range(40)]
        calls = []
        def history(**kwargs):
            calls.append(kwargs)
            return None
        result=observe_closed_positions(SimpleNamespace(history_deals_get=history),rows,"NASUSD",START,END)
        self.assertEqual(len(calls),32)
        self.assertFalse(result["positions"])
        self.assertEqual(result["excluded_reasons"]["position_query_limit"],8)

    def test_account_drift_during_extra_position_read_is_rejected(self):
        module = connected_fixtures.HistoryMT5()
        selected = selection(module)
        rows = [self.deal(symbol=selected.symbol.symbol), self.deal(2,entry=1,symbol=selected.symbol.symbol)]
        def history(*args, **kwargs):
            if "position" in kwargs:
                module.account.login += 1
            return rows
        module.history_deals_get = history
        with self.assertRaisesRegex(ValueError,"selected_account_changed"):
            SelectedTerminalHistoryReader(module).read(selected,START,END)


if __name__ == "__main__":
    unittest.main()
