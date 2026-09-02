from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import json
import math
import multiprocessing
import queue
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

from dusty.local_app import LocalDustyApplication, RuntimeSelection, RuntimeActionResult
from dusty.basic_ui import DustyBasicUI
from dusty.local_research import (
    LocalResearchRuntime, ResearchSettings, SelectedTerminalHistoryReader,
    _atomic_json, _request_payload, execute_research, read_research_result, validate_history,
)
from dusty.local_terminal import (
    ReadOnlyTerminalSnapshotReader, account_identity_fingerprint, WindowsMT5Discovery,
)
from dusty.markets import InstrumentEconomics
from dusty.mt5worker import MT5Bar
from dusty.reviewed_strategies import reviewed_research_packages, resolve_research_package
from dusty.strategy_catalog import OperatingMode, QualificationBinding
from test_local_runtime_ui import FakeMT5, FakeRuntime, FakeSnapshotReader, installation


COMMIT = "f" * 40
START = datetime(2026, 8, 24, tzinfo=timezone.utc)
END = START + timedelta(days=7)


def fixture_bars(start=START, count=512):
    bars = []
    for index in range(count):
        center = 1.1 + 0.003 * math.sin(index / 9) + index * 0.000001
        bars.append(MT5Bar(start + timedelta(minutes=15 * index), center, center + 0.0003,
                           center - 0.0003, center + 0.00002, 100, 2, 0))
    return tuple(bars)


class HistoryMT5(FakeMT5):
    TIMEFRAME_M15 = 15
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1

    def __init__(self):
        super().__init__()
        self.installation = installation(str(Path("broker") / "terminal64.exe"))
        self.account = super().account_info()
        self.info = super().symbols_get()[0]
        self.info.bid, self.info.ask = 1.1, 1.10002
        self.terminal = SimpleNamespace(path=str(Path(self.installation.executable_path).parent),
                                        data_path="C:/TerminalData", build=5000, connected=True)
        self.rows = [dict(time=int(b.at.timestamp()), open=b.open, high=b.high, low=b.low,
                          close=b.close, tick_volume=b.tick_volume, spread=b.spread,
                          real_volume=b.real_volume) for b in fixture_bars()]
        self.after_read = lambda: None

    def initialize(self, path, *, portable=False, timeout=None):
        self.initialize_calls.append((path, portable, timeout))
        return True

    def terminal_info(self):
        return self.terminal

    def account_info(self):
        return self.account

    def symbols_get(self):
        return (self.info,)

    def symbol_info(self, name):
        return self.info if name == self.info.name else None

    def order_calc_margin(self, kind, symbol, volume, price):
        return price * 100_000 * volume * .01

    def copy_rates_range(self, symbol, timeframe, start, end):
        self.request = (symbol, timeframe, start, end)
        self.after_read()
        return self.rows

    def order_send(self, *_args):
        raise AssertionError("Research must never send orders")

    def symbol_select(self, *_args):
        raise AssertionError("Research must not change Market Watch")


def selection(module=None):
    module = module or HistoryMT5()
    terminal = ReadOnlyTerminalSnapshotReader(module).read(module.installation)
    package = reviewed_research_packages()[0]
    entry = package.catalog_entry
    binding = QualificationBinding(terminal.installation.identity_key, terminal.account.server,
                                   terminal.account.mode, terminal.symbols[0].symbol,
                                   entry.strategy_hash, COMMIT)
    return RuntimeSelection(terminal, terminal.symbols[0], entry, OperatingMode.BACKTEST, binding)


class FixtureReader:
    def read(self, selected, start, end):
        return fixture_bars(start), InstrumentEconomics(100_000, .00001, 1, .01, .01, 100,
                                                       margin_rate=.01, point_size=.00001)


def fixture_worker(selected, settings, directory, start, end, repository):
    """Spawn-safe fixture exercises the real lab and artifact writer, never an MT5 claim."""
    result = execute_research(selected, settings, directory, start, end, reader=FixtureReader())
    _atomic_json(directory / "result.json", result)


class FixtureSpawnContext:
    def Process(self, *, target, args, daemon):
        return multiprocessing.get_context("spawn").Process(target=fixture_worker, args=args, daemon=daemon)


class FakeProcess:
    alive = True
    exitcode = -15
    def start(self): pass
    def is_alive(self): return self.alive
    def terminate(self): self.alive = False
    def join(self, timeout): pass
    def close(self): pass


class FakeContext:
    def Process(self, **kwargs):
        self.process = FakeProcess()
        return self.process


class ReviewedPackageTests(unittest.TestCase):
    def test_exact_packages_and_modified_challengers(self):
        packages = reviewed_research_packages()
        self.assertEqual(len(packages), 2)
        self.assertNotEqual(packages[0].fingerprint, packages[1].fingerprint)
        for package in packages:
            self.assertEqual(resolve_research_package(package.catalog_entry), package)
            self.assertIn("RESEARCH ONLY", package.title)
            changed = replace(package, features=replace(package.features, rsi_period=21))
            self.assertNotEqual(package.fingerprint, changed.fingerprint)
            for field, value in (("title", "Certified"), ("strategy_hash", "a" * 64), ("timeframe", "H1")):
                with self.assertRaises(ValueError):
                    resolve_research_package(replace(package.catalog_entry, **{field: value}))

    def test_unknown_metadata_cannot_become_executable(self):
        with self.assertRaises(ValueError):
            resolve_research_package(replace(reviewed_research_packages()[0].catalog_entry, strategy_id="downloaded-code"))

    def test_invalid_settings(self):
        for days in (0, 31, True, 2.5):
            with self.assertRaises(ValueError):
                ResearchSettings(history_days=days)
        for value in (-1, math.inf, math.nan, True):
            with self.assertRaises(ValueError):
                ResearchSettings(commission_per_lot=value)


class SelectedHistoryTests(unittest.TestCase):
    def test_read_exact_terminal_symbol_UTC_portable_and_units(self):
        module = HistoryMT5()
        module.installation = replace(module.installation, portable=True)
        module.info.name = "EURUSD.a"
        module.info.trade_tick_size = .0001
        module.info.trade_tick_value = 10
        selected = selection(module)
        bars, economics = SelectedTerminalHistoryReader(module).read(selected, START, END)
        self.assertEqual(len(bars), 512)
        self.assertEqual(module.request, ("EURUSD.a", 15, START, END))
        self.assertEqual(module.initialize_calls[-1][1:], (True, 10_000))
        self.assertEqual(economics.point_size, .00001)
        self.assertEqual(economics.tick_size, .0001)
        self.assertEqual(module.shutdown_count, 2)

    def test_full_login_fingerprint_not_just_last_four(self):
        module = HistoryMT5()
        selected = selection(module)
        module.account.login = 99345678
        self.assertNotEqual(account_identity_fingerprint(module.account), selected.terminal.account.identity_fingerprint)
        with self.assertRaisesRegex(ValueError, "account_changed"):
            SelectedTerminalHistoryReader(module).read(selected, START, END)

    def test_account_switch_during_read_is_rejected(self):
        module = HistoryMT5()
        selected = selection(module)
        module.after_read = lambda: setattr(module.account, "login", 99345678)
        with self.assertRaisesRegex(ValueError, "account_changed"):
            SelectedTerminalHistoryReader(module).read(selected, START, END)

    def test_environment_or_symbol_switch_rejected(self):
        for target, field, value in (("terminal", "path", "other"), ("terminal", "data_path", "other"),
                                      ("terminal", "build", 6000), ("info", "point", .1)):
            module = HistoryMT5()
            selected = selection(module)
            setattr(getattr(module, target), field, value)
            with self.assertRaises(ValueError):
                SelectedTerminalHistoryReader(module).read(selected, START, END)
            self.assertEqual(module.shutdown_count, 2)

    def test_missing_tick_size_is_not_replaced_by_point(self):
        module = HistoryMT5()
        module.info.trade_tick_size = 0
        with self.assertRaisesRegex(ValueError, "economics_missing"):
            SelectedTerminalHistoryReader(module).read(selection(module), START, END)

    def test_converted_or_custom_or_nonlinear_symbol_rejected(self):
        for field, value in (("currency_profit", "JPY"), ("custom", True), ("trade_tick_value", 50)):
            module = HistoryMT5()
            setattr(module.info, field, value)
            with self.assertRaisesRegex(ValueError, "not_supported"):
                SelectedTerminalHistoryReader(module).read(selection(module), START, END)

    def test_missing_margin_or_history_fails_and_disconnects(self):
        module = HistoryMT5()
        selected = selection(module)
        module.order_calc_margin = lambda *_: None
        with self.assertRaisesRegex(ValueError, "margin_calculation"):
            SelectedTerminalHistoryReader(module).read(selected, START, END)
        module = HistoryMT5()
        selected = selection(module)
        module.rows = None
        with self.assertRaisesRegex(ValueError, "history_unavailable"):
            SelectedTerminalHistoryReader(module).read(selected, START, END)
        self.assertEqual(module.shutdown_count, 2)

    def test_bounds_UTC_and_confirmation_bar_validation(self):
        rows = fixture_bars()
        for invalid in (rows[:49], (rows[0],) + rows, tuple(reversed(rows)),
                        rows[:-1] + (replace(rows[-1], at=END + timedelta(minutes=15)),),
                        rows[:-1] + (replace(rows[-1], close=math.nan),),
                        rows[:-1] + (replace(rows[-1], spread=-1),),
                        rows[:-1] + (replace(rows[-1], at=rows[-1].at + timedelta(minutes=1)),)):
            with self.assertRaises(ValueError):
                validate_history(invalid, START, END)
        with self.assertRaises(ValueError):
            SelectedTerminalHistoryReader(HistoryMT5()).read(selection(), START, END + timedelta(days=30))
        with self.assertRaises(ValueError):
            SelectedTerminalHistoryReader(HistoryMT5()).read(selection(), START.replace(tzinfo=None), END.replace(tzinfo=None))


class ResearchEvidenceTests(unittest.TestCase):
    def test_full_laboratory_artifacts_keep_proof_boundaries(self):
        selected = selection()
        settings = ResearchSettings(commission_per_lot=7, slippage_points=1)
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            _atomic_json(directory / "request.json", _request_payload(selected, settings, START, END))
            result = execute_research(selected, settings, directory, START, END, reader=FixtureReader())
            _atomic_json(directory / "result.json", result)
            self.assertEqual(read_research_result(directory)["state"], "COMPLETED")
            self.assertIs(result["promotion_eligible"], False)
            self.assertIn("NOT CERTIFIED", result["message"])
            report = json.loads((directory / "report.json").read_text())
            lab = report["laboratory"]
            self.assertEqual(lab["bar_count"], 511)
            self.assertGreater(lab["minimum_lot_backtest"]["trade_count"], 0)
            self.assertGreater(lab["growth_backtest"]["trade_count"], 0)
            self.assertEqual(lab["feature_bars"][0]["spread_points"], 2)
            self.assertEqual(lab["feature_bars"][0]["decision_spread_proxy_points"], 2)
            self.assertNotIn("12345678", (directory / "request.json").read_text())
            (directory / "bars.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "artifact_hash"):
                read_research_result(directory)

    def test_request_tamper_and_arbitrary_artifact_path_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            _atomic_json(directory / "request.json", {})
            _atomic_json(directory / "result.json", {"state": "COMPLETED", "message": "fake", "promotion_eligible": False})
            with self.assertRaises(ValueError):
                read_research_result(directory)

    def test_dynamic_spread_increases_cost_without_rewriting_source_bars(self):
        class WideSpreadReader(FixtureReader):
            def read(self, selected, start, end):
                bars, economics = super().read(selected, start, end)
                return tuple(replace(bar, spread=20) for bar in bars), economics
        pnls = []
        for reader in (FixtureReader(), WideSpreadReader()):
            with tempfile.TemporaryDirectory() as root:
                directory = Path(root)
                selected = selection()
                _atomic_json(directory / "request.json", _request_payload(selected, ResearchSettings(), START, END))
                execute_research(selected, ResearchSettings(), directory, START, END, reader=reader)
                lab = json.loads((directory / "report.json").read_text())["laboratory"]
                pnls.append(lab["minimum_lot_backtest"]["net_pnl"])
                if isinstance(reader, WideSpreadReader):
                    trace = next(t for t in lab["growth_sizing"] if t["approved"])
                    self.assertAlmostEqual(trace["spread_price_used"], .0002)
                    self.assertIn("proxy", trace["spread_basis"])
        self.assertLess(pnls[1], pnls[0])

    def test_closed_market_gap_does_not_require_a_fresh_tick(self):
        rows = fixture_bars()
        # Weekend or missing data is reported, not synthesized or treated as an emergency.
        gap_rows = rows[:200] + tuple(replace(b, at=b.at + timedelta(days=1)) for b in rows[200:])
        validate_history(gap_rows, START, END)


class ResearchCoordinatorTests(unittest.TestCase):
    def runtime(self, root, **kwargs):
        return LocalResearchRuntime(Path(root) / "repo", output_directory=Path(root) / "evidence",
                                    code_checker=lambda *_: True, **kwargs)

    def test_duplicate_start_cancel_and_no_implicit_resume(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = self.runtime(root, context=FakeContext())
            self.assertTrue(runtime.start(selection()).accepted)
            self.assertFalse(runtime.start(selection()).accepted)
            first = runtime.poll().run_directory
            self.assertTrue(runtime.emergency_halt().accepted)
            self.assertEqual(runtime.poll().state, "CANCELLED")
            self.assertFalse(read_research_result(Path(first))["promotion_eligible"])
            self.assertTrue(runtime.start(selection()).accepted)
            self.assertNotEqual(runtime.poll().run_directory, first)
            runtime.emergency_halt()
            runtime.poll()

    def test_timeout_terminates_only_owned_worker(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = self.runtime(root, context=FakeContext(), timeout_seconds=1)
            runtime.start(selection())
            runtime._started = time.monotonic() - 2
            self.assertEqual(runtime.poll().state, "CANCELLING")
            self.assertEqual(runtime.poll().state, "TIMED_OUT")

    def test_worker_crash_or_missing_artifact_is_failed(self):
        with tempfile.TemporaryDirectory() as root:
            context = FakeContext()
            runtime = self.runtime(root, context=context)
            runtime.start(selection())
            context.process.alive = False
            self.assertEqual(runtime.poll().state, "FAILED")
            self.assertFalse(runtime.active)

    def test_metadata_live_and_dirty_code_are_not_executable(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = self.runtime(root, context=FakeContext())
            self.assertFalse(runtime.start(replace(selection(), mode=OperatingMode.LIVE)).accepted)
            self.assertFalse(runtime.start(replace(selection(), strategy=replace(selection().strategy, title="fake"))).accepted)
            self.assertFalse(runtime.start(replace(selection(), binding=replace(selection().binding, symbol="OTHER"))).accepted)
            runtime._code_checker = lambda *_: False
            self.assertFalse(runtime.start(selection()).accepted)

    def test_outputs_cannot_dirty_the_source_repository(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(ValueError):
                LocalResearchRuntime(Path(root), output_directory=Path(root) / "results")

    def test_real_spawn_process_runs_fixture_pipeline_on_windows_and_linux(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = self.runtime(root, context=FixtureSpawnContext())
            self.assertTrue(runtime.start(selection()).accepted)
            deadline = time.monotonic() + 30
            try:
                while runtime.active and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual(runtime.poll().state, "COMPLETED", runtime.poll().message)
                self.assertIn("NOT CERTIFIED", runtime.poll().message)
                self.assertIsNotNone(runtime.poll().capital_summary)
                self.assertEqual(runtime.poll().research_scope, selection().research_scope)
            finally:
                runtime.emergency_halt()
                if runtime._process:
                    runtime._process.join(timeout=5)
                    runtime.poll()


class ApplicationLifecycleTests(unittest.TestCase):
    def app(self, root, runtime=None):
        module = HistoryMT5()
        path = Path(root) / "terminal64.exe"
        path.touch()
        selected = selection(module)
        terminal = replace(selected.terminal, installation=installation(str(path)))
        app = LocalDustyApplication(
            WindowsMT5Discovery(manual_paths=(path,), search_roots=(), process_reader=lambda: (),
                                registry_reader=lambda: (), platform_name="posix"),
            FakeSnapshotReader(terminal), (selected.strategy,), code_commit=COMMIT,
            runtime=runtime or FakeRuntime())
        app.refresh_terminals()
        app.connect_terminal(app.view().terminals[0].identity_key)
        app.select_symbol(selected.symbol.symbol)
        app.select_strategy(selected.strategy.strategy_id)
        return app

    def test_active_selection_and_duplicate_start_are_locked(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            self.assertTrue(app.start().accepted)
            self.assertFalse(app.start().accepted)
            self.assertTrue(app.view().runtime_active)
            for action in (app.refresh_terminals, app.begin_development,
                           lambda: app.select_symbol("EURUSD"), lambda: app.select_mode(OperatingMode.BACKTEST)):
                with self.assertRaises(RuntimeError):
                    action()

    def test_failed_halt_does_not_claim_runtime_stopped(self):
        with tempfile.TemporaryDirectory() as root:
            runtime = FakeRuntime()
            runtime.emergency_halt = lambda: RuntimeActionResult(False, "failed")
            app = self.app(root, runtime)
            app.start()
            app.emergency_halt()
            self.assertTrue(app.view().runtime_active)

    def test_development_locks_start_and_requires_restart(self):
        with tempfile.TemporaryDirectory() as root:
            app = self.app(root)
            app.begin_development()
            self.assertFalse(app.start().accepted)
            app.finish_development()
            self.assertTrue(app.view().restart_required)
            self.assertFalse(app.start().accepted)
            self.assertTrue(all(not gate.available for gate in app.view().mode_gates if gate.mode is not OperatingMode.BACKTEST))


class DesktopEventTests(unittest.TestCase):
    """Headless event ownership tests; these do not claim visual Windows GUI validation."""
    def ui(self):
        ui = DustyBasicUI.__new__(DustyBasicUI)
        ui._events = queue.Queue()
        ui._busy = False
        ui._closing = False
        ui._application = SimpleNamespace(runtime_active=False, view=lambda: None)
        ui._render = lambda _: None
        ui._status_var = SimpleNamespace(set=lambda _: None)
        ui._root = SimpleNamespace(after=lambda *_: None)
        ui._show_error = lambda error: self.fail(error)
        return ui

    def test_worker_only_queues_callback_until_main_thread_polls(self):
        ui = self.ui()
        received = []
        ui._background(lambda: "result", lambda result, error: received.append((result, error)))
        event = ui._events.get(timeout=2)
        self.assertEqual(received, [])
        self.assertTrue(ui._busy)
        ui._events.put(event)
        ui._poll()
        self.assertEqual(received, [("result", None)])
        self.assertFalse(ui._busy)

    def test_worker_exception_reaches_main_thread_and_unlocks_busy(self):
        ui = self.ui()
        received = []
        def failed():
            raise RuntimeError("fixture")
        ui._background(failed, lambda result, error: received.append(error))
        event = ui._events.get(timeout=2)
        ui._events.put(event)
        ui._poll()
        self.assertEqual(received, ["RuntimeError: fixture"])
        self.assertFalse(ui._busy)


if __name__ == "__main__":
    unittest.main()
