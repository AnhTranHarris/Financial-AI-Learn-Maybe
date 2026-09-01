from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from dusty.codex_bridge import (
    CodexCLIReporter,
    CodexRequest,
    CodexSafetyContext,
    CodexTaskKind,
    build_support_bundle,
    sanitize_payload,
)
from dusty.demo_session import AccountMode
from dusty.local_app import (
    LocalDustyApplication,
    RuntimeActionResult,
    RuntimeSelection,
)
from dusty.local_terminal import (
    AccountSummary,
    BrokerSymbolOption,
    ReadOnlyTerminalSnapshotReader,
    RunningTerminalProcess,
    TerminalDiscoverySource,
    TerminalInstallation,
    TerminalSnapshot,
    WindowsMT5Discovery,
)
from dusty.strategy_catalog import (
    ModeProof,
    OperatingMode,
    QualificationBinding,
    StrategyCatalogEntry,
    StrategyStage,
    assess_mode_gates,
    compatible_strategies,
    load_strategy_catalog,
)


COMMIT = "f" * 40
STRATEGY_HASH = "a" * 64
NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def symbol(name: str = "EURUSD", category: str = "Forex\\Majors") -> BrokerSymbolOption:
    return BrokerSymbolOption(
        name,
        category,
        "Euro vs US Dollar",
        "EUR",
        "USD",
        5,
        0.00001,
        0.00001,
        1.0,
        100_000.0,
        0.01,
        0.01,
        100.0,
        4,
        True,
        False,
    )


def strategy(**changes: object) -> StrategyCatalogEntry:
    values = {
        "strategy_id": "fx-trend-1",
        "title": "FX Trend",
        "strategy_hash": STRATEGY_HASH,
        "stage": StrategyStage.BACKTEST_CANDIDATE,
        "allowed_symbols": ("EURUSD",),
    }
    values.update(changes)
    return StrategyCatalogEntry(**values)


def installation(path: str) -> TerminalInstallation:
    return TerminalInstallation(path, (TerminalDiscoverySource.MANUAL,))


def snapshot(path: str, *, mode: AccountMode = AccountMode.DEMO) -> TerminalSnapshot:
    install = installation(path)
    account = AccountSummary(
        "Broker-Demo" if mode is AccountMode.DEMO else "Broker-Live",
        "Broker",
        "••••1234",
        mode,
        "USD",
        100.0,
        20_000.0,
        20_100.0,
        100.0,
        200.0,
        19_900.0,
        True,
        True,
    )
    return TerminalSnapshot(install, "5000", "5.5000.1", True, "C:/Data", account, (symbol(),), 1, 2, 3, NOW)


class FakeMT5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    ACCOUNT_TRADE_MODE_CONTEST = 1
    ACCOUNT_TRADE_MODE_REAL = 2

    def __init__(self) -> None:
        self.initialize_calls: list[tuple[str, bool]] = []
        self.shutdown_count = 0

    def initialize(self, path: str, *, portable: bool = False) -> bool:
        self.initialize_calls.append((path, portable))
        return True

    def shutdown(self) -> None:
        self.shutdown_count += 1

    def last_error(self):
        return (0, "ok")

    def terminal_info(self):
        return SimpleNamespace(build=5000, connected=True, data_path="C:/TerminalData")

    def version(self):
        return (5, 5000, "1 Sep 2026")

    def account_info(self):
        return SimpleNamespace(
            server="Broker-Demo",
            company="Broker",
            login=12345678,
            trade_mode=0,
            currency="USD",
            leverage=100,
            balance=20_000,
            equity=19_500,
            profit=-500,
            margin=100,
            margin_free=-10,
            trade_allowed=True,
            trade_expert=True,
        )

    def symbols_get(self):
        return (
            SimpleNamespace(
                name="EURUSD",
                path="Forex\\Majors",
                description="Euro vs US Dollar",
                currency_base="EUR",
                currency_profit="USD",
                digits=5,
                point=0.00001,
                trade_tick_size=0.00001,
                trade_tick_value=1.0,
                trade_contract_size=100_000,
                volume_min=0.01,
                volume_step=0.01,
                volume_max=100.0,
                trade_mode=4,
                visible=True,
                custom=False,
            ),
        )

    def positions_get(self):
        return (object(),)

    def orders_get(self):
        return (object(), object())

    def history_deals_get(self, start: datetime, end: datetime):
        return (object(), object(), object())


class TerminalDiscoveryTests(unittest.TestCase):
    def test_discovery_deduplicates_process_manual_and_filesystem_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            terminal = Path(directory) / "Broker" / "terminal64.exe"
            terminal.parent.mkdir()
            terminal.touch()
            discovery = WindowsMT5Discovery(
                search_roots=(directory,),
                manual_paths=(terminal,),
                process_reader=lambda: (RunningTerminalProcess(42, str(terminal), True),),
                registry_reader=lambda: (),
                platform_name="posix",
            )
            result = discovery.discover()
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].running_process_ids, (42,))
            self.assertTrue(result[0].portable)
            self.assertEqual(
                set(result[0].sources),
                {
                    TerminalDiscoverySource.MANUAL,
                    TerminalDiscoverySource.RUNNING_PROCESS,
                    TerminalDiscoverySource.STANDARD_LOCATION,
                },
            )

    def test_same_named_installations_have_distinct_ui_labels(self):
        left = installation("C:/BrokerA/MetaTrader 5/terminal64.exe")
        right = installation("C:/BrokerB/MetaTrader 5/terminal64.exe")
        self.assertNotEqual(left.display_name, right.display_name)

    def test_all_discovery_sources_obey_terminal_count_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = []
            for index in range(3):
                terminal = Path(directory) / f"Broker{index}" / "terminal64.exe"
                terminal.parent.mkdir()
                terminal.touch()
                paths.append(terminal)
            result = WindowsMT5Discovery(
                manual_paths=paths,
                search_roots=(),
                process_reader=lambda: (),
                registry_reader=lambda: (),
                platform_name="posix",
                max_terminals=2,
            ).discover()
            self.assertEqual(len(result), 2)

    def test_non_windows_default_discovery_does_not_scan_host(self):
        result = WindowsMT5Discovery(platform_name="posix").discover()
        self.assertEqual(result, ())

    def test_invalid_manual_file_is_not_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            invalid = Path(directory) / "not-mt5.exe"
            invalid.touch()
            result = WindowsMT5Discovery(
                manual_paths=(invalid,),
                search_roots=(),
                process_reader=lambda: (),
                registry_reader=lambda: (),
                platform_name="posix",
            ).discover()
            self.assertEqual(result, ())


class ReadOnlySnapshotTests(unittest.TestCase):
    def test_reader_collects_account_symbols_and_broker_state_without_write_surface(self):
        module = FakeMT5()
        reader = ReadOnlyTerminalSnapshotReader(module)
        result = reader.read(installation("C:/Broker/terminal64.exe"))
        self.assertFalse(reader.broker_write_authorized)
        self.assertFalse(hasattr(reader, "order_send"))
        self.assertEqual(result.account.login_hint, "••••5678")
        self.assertEqual(result.account.margin_free, -10.0)
        self.assertEqual((result.open_positions, result.active_orders, result.recent_deals), (1, 2, 3))
        self.assertEqual(result.symbols[0].symbol, "EURUSD")
        self.assertEqual(module.initialize_calls, [("C:/Broker/terminal64.exe", False)])
        self.assertEqual(module.shutdown_count, 1)

    def test_missing_optional_broker_read_is_visible_as_warning(self):
        module = FakeMT5()
        module.positions_get = lambda: None
        result = ReadOnlyTerminalSnapshotReader(module).read(installation("C:/Broker/terminal64.exe"))
        self.assertEqual(result.open_positions, 0)
        self.assertIn("mt5_read_failed:positions_get", result.warnings)

    def test_missing_symbol_inventory_fails_and_still_shuts_down(self):
        module = FakeMT5()
        module.symbols_get = lambda: None
        with self.assertRaises(RuntimeError):
            ReadOnlyTerminalSnapshotReader(module).read(installation("C:/Broker/terminal64.exe"))
        self.assertEqual(module.shutdown_count, 1)

    def test_missing_tick_size_never_assumes_point_size(self):
        module = FakeMT5()
        native = module.symbols_get()[0]
        native.trade_tick_size = 0.0
        module.symbols_get = lambda: (native,)
        result = ReadOnlyTerminalSnapshotReader(module).read(installation("C:/Broker/terminal64.exe"))
        self.assertEqual(result.symbols[0].point_size, 0.00001)
        self.assertEqual(result.symbols[0].tick_size, 0.0)


class StrategyCatalogAndModeGateTests(unittest.TestCase):
    def test_compatibility_requires_explicit_symbol_category_or_universal_scope(self):
        exact = strategy()
        category = strategy(
            strategy_id="fx-category",
            title="FX Category",
            allowed_symbols=(),
            allowed_category_prefixes=("Forex",),
        )
        unrelated = strategy(strategy_id="gold", title="Gold", allowed_symbols=("XAUUSD",))
        self.assertEqual(compatible_strategies((unrelated, exact, category), symbol()), (category, exact))

    def test_catalog_json_rejects_code_fields_and_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.json"
            row = {
                "strategy_id": "one",
                "title": "One",
                "strategy_hash": STRATEGY_HASH,
                "stage": "translated",
                "allowed_symbols": ["EURUSD"],
                "code_text": "this field is ignored and never executed",
            }
            path.write_text(json.dumps([row]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported fields"):
                load_strategy_catalog(path)
            del row["code_text"]
            path.write_text(json.dumps([row]), encoding="utf-8")
            loaded = load_strategy_catalog(path)
            self.assertEqual(loaded[0].strategy_id, "one")
            row["universal_symbol_compatibility"] = "false"
            path.write_text(json.dumps([row]), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "must be a boolean"):
                load_strategy_catalog(path)
            del row["universal_symbol_compatibility"]
            path.write_text(json.dumps([row, row]), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_strategy_catalog(path)

    def test_demo_unlock_requires_exact_native_backtest_and_user_proof(self):
        terminal = snapshot("C:/Broker/terminal64.exe")
        entry = strategy()
        binding = QualificationBinding(
            terminal.installation.identity_key,
            terminal.account.server,
            terminal.account.mode,
            "EURUSD",
            STRATEGY_HASH,
            COMMIT,
        )
        locked = assess_mode_gates(terminal, symbol(), entry, binding=binding, proof=None)
        self.assertTrue(locked[0].available)
        self.assertFalse(locked[1].available)
        proof = ModeProof(binding.fingerprint, True, True, user_demo_confirmation=True)
        unlocked = assess_mode_gates(terminal, symbol(), entry, binding=binding, proof=proof)
        self.assertTrue(unlocked[1].available)

    def test_live_remains_locked_even_if_supplied_demo_claims_pass(self):
        terminal = snapshot("C:/Broker/terminal64.exe", mode=AccountMode.REAL)
        entry = strategy(stage=StrategyStage.LIVE_ELIGIBLE)
        binding = QualificationBinding(
            terminal.installation.identity_key,
            terminal.account.server,
            terminal.account.mode,
            "EURUSD",
            STRATEGY_HASH,
            COMMIT,
        )
        proof = ModeProof(binding.fingerprint, True, True, True, True, True, True)
        gates = assess_mode_gates(terminal, symbol(), entry, binding=binding, proof=proof)
        self.assertFalse(gates[2].available)
        self.assertIn("live_execution_not_implemented", gates[2].reasons)


class FakeSnapshotReader:
    def __init__(self, value: TerminalSnapshot) -> None:
        self.value = value

    def read(self, installation: TerminalInstallation) -> TerminalSnapshot:
        self.value = TerminalSnapshot(
            installation,
            self.value.terminal_build,
            self.value.terminal_version,
            self.value.connected,
            self.value.data_path,
            self.value.account,
            self.value.symbols,
            self.value.open_positions,
            self.value.active_orders,
            self.value.recent_deals,
            self.value.captured_at,
            self.value.warnings,
        )
        return self.value


class FakeRuntime:
    configured = True

    def __init__(self) -> None:
        self.started: RuntimeSelection | None = None

    def start(self, selection: RuntimeSelection) -> RuntimeActionResult:
        self.started = selection
        return RuntimeActionResult(True, "started")

    def stop_new_entries(self) -> RuntimeActionResult:
        return RuntimeActionResult(True, "entries_stopped")

    def emergency_halt(self) -> RuntimeActionResult:
        return RuntimeActionResult(True, "halted")


class LocalApplicationTests(unittest.TestCase):
    def test_controller_requires_explicit_terminal_symbol_and_strategy_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terminal64.exe"
            path.touch()
            terminal = snapshot(str(path))
            discovery = WindowsMT5Discovery(
                manual_paths=(path,),
                search_roots=(),
                process_reader=lambda: (),
                registry_reader=lambda: (),
                platform_name="posix",
            )
            runtime = FakeRuntime()
            app = LocalDustyApplication(
                discovery,
                FakeSnapshotReader(terminal),
                (strategy(),),
                code_commit=COMMIT,
                runtime=runtime,
            )
            view = app.refresh_terminals()
            self.assertIsNone(view.terminal)
            view = app.connect_terminal(view.terminals[0].identity_key)
            self.assertIsNone(view.selected_symbol)
            app.select_symbol("EURUSD")
            app.select_strategy("fx-trend-1")
            result = app.start()
            self.assertTrue(result.accepted)
            self.assertEqual(runtime.started.mode, OperatingMode.BACKTEST)

    def test_stop_entries_is_latched_in_application_view(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "terminal64.exe"
            path.touch()
            terminal = snapshot(str(path))
            app = LocalDustyApplication(
                WindowsMT5Discovery(
                    manual_paths=(path,),
                    search_roots=(),
                    process_reader=lambda: (),
                    registry_reader=lambda: (),
                    platform_name="posix",
                ),
                FakeSnapshotReader(terminal),
                (strategy(),),
                code_commit=COMMIT,
                runtime=FakeRuntime(),
            )
            app.refresh_terminals()
            app.connect_terminal(app.view().terminals[0].identity_key)
            app.select_symbol("EURUSD")
            app.select_strategy("fx-trend-1")
            app.stop_new_entries()
            self.assertTrue(app.view().new_entries_halted)
            self.assertFalse(app.start().accepted)


class CodexBridgeTests(unittest.TestCase):
    def test_support_bundle_excludes_login_and_terminal_path(self):
        empty_app_view = SimpleNamespace(
            terminal=snapshot("C:/Broker/terminal64.exe"),
            selected_symbol=symbol(),
            selected_strategy=strategy(),
            selected_mode=OperatingMode.BACKTEST,
            mode_gates=(),
            runtime_configured=False,
            runtime_active=False,
            new_entries_halted=False,
            last_message="ready",
        )
        bundle = build_support_bundle(empty_app_view, code_commit=COMMIT)
        rendered = json.dumps(bundle)
        self.assertNotIn("1234", rendered)
        self.assertNotIn("C:/Broker", rendered)

    def test_sanitizer_redacts_nested_credentials(self):
        result = sanitize_payload({"nested": {"api_key": "value", "password": "value"}, "safe": 2})
        self.assertEqual(result["nested"]["api_key"], "[REDACTED]")
        self.assertEqual(result["safe"], 2)

    def test_report_is_ephemeral_read_only_and_uses_stdin_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            executable = repo / "codex.exe"
            executable.touch()
            calls = []

            def runner(command, **kwargs):
                calls.append((command, kwargs))
                return subprocess.CompletedProcess(command, 0, "report", "")

            bridge = CodexCLIReporter(repo, codex_executable=str(executable), runner=runner)
            result = bridge.run(
                CodexRequest(CodexTaskKind.REPORT, "audit", {"password": "no", "safe": True}),
                CodexSafetyContext(True, False, True),
            )
            self.assertTrue(result.accepted)
            command, kwargs = calls[0]
            self.assertIn("read-only", command)
            self.assertIn("--ephemeral", command)
            self.assertNotIn("no", kwargs["input"])
            self.assertIn("[REDACTED]", kwargs["input"])

    def test_development_refuses_dirty_unconfirmed_or_active_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            executable = repo / "codex.exe"
            executable.touch()
            bridge = CodexCLIReporter(repo, codex_executable=str(executable), runner=lambda *args, **kwargs: None)
            result = bridge.run(
                CodexRequest(CodexTaskKind.DEVELOPMENT, "edit", {}),
                CodexSafetyContext(False, False, True),
            )
            self.assertFalse(result.accepted)
            self.assertIn("development_not_human_confirmed", result.error)
            self.assertIn("repository_not_clean", result.error)
            self.assertIn("trading_runtime_active", result.error)

    def test_development_uses_workspace_write_only_after_explicit_clean_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            executable = repo / "codex.exe"
            executable.touch()
            calls = []

            def runner(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, "done", "")

            bridge = CodexCLIReporter(repo, codex_executable=str(executable), runner=runner)
            result = bridge.run(
                CodexRequest(CodexTaskKind.DEVELOPMENT, "edit", {}),
                CodexSafetyContext(True, True, False),
            )
            self.assertTrue(result.accepted)
            self.assertIn("workspace-write", calls[0])
            self.assertNotIn("danger-full-access", calls[0])


if __name__ == "__main__":
    unittest.main()
