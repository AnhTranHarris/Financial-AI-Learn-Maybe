from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol

from .local_terminal import (
    BrokerSymbolOption,
    ReadOnlyTerminalSnapshotReader,
    TerminalInstallation,
    TerminalSnapshot,
    WindowsMT5Discovery,
)
from .strategy_catalog import (
    ModeGate,
    ModeProof,
    OperatingMode,
    QualificationBinding,
    StrategyCatalogEntry,
    assess_mode_gates,
    compatible_strategies,
)


@dataclass(frozen=True, slots=True)
class RuntimeSelection:
    terminal: TerminalSnapshot
    symbol: BrokerSymbolOption
    strategy: StrategyCatalogEntry
    mode: OperatingMode
    binding: QualificationBinding


@dataclass(frozen=True, slots=True)
class RuntimeActionResult:
    accepted: bool
    message: str


class RuntimeControlPort(Protocol):
    @property
    def configured(self) -> bool: ...

    def start(self, selection: RuntimeSelection) -> RuntimeActionResult: ...

    def stop_new_entries(self) -> RuntimeActionResult: ...

    def emergency_halt(self) -> RuntimeActionResult: ...


class UnconfiguredRuntimeControl:
    """Honest default: the desktop shell cannot pretend that an execution loop exists."""

    @property
    def configured(self) -> bool:
        return False

    def start(self, selection: RuntimeSelection) -> RuntimeActionResult:
        return RuntimeActionResult(False, "runtime_coordinator_not_configured")

    def stop_new_entries(self) -> RuntimeActionResult:
        return RuntimeActionResult(True, "no_runtime_active")

    def emergency_halt(self) -> RuntimeActionResult:
        return RuntimeActionResult(True, "no_runtime_active")


ProofResolver = Callable[[QualificationBinding], ModeProof | None]


@dataclass(frozen=True, slots=True)
class LocalApplicationView:
    terminals: tuple[TerminalInstallation, ...]
    terminal: TerminalSnapshot | None
    symbols: tuple[BrokerSymbolOption, ...]
    selected_symbol: BrokerSymbolOption | None
    compatible_strategies: tuple[StrategyCatalogEntry, ...]
    selected_strategy: StrategyCatalogEntry | None
    selected_mode: OperatingMode
    mode_gates: tuple[ModeGate, ...]
    runtime_configured: bool
    runtime_active: bool
    new_entries_halted: bool
    last_message: str
    runtime_message: str = ""
    run_directory: str = ""
    maintenance_active: bool = False
    restart_required: bool = False


class LocalDustyApplication:
    """Small application controller connecting inventory, selection, gates and runtime ports.

    The controller is deliberately not a trading strategy. It translates explicit UI selections
    into one immutable runtime request only after the selected mode gate passes.
    """

    def __init__(
        self,
        discovery: WindowsMT5Discovery,
        snapshot_reader: ReadOnlyTerminalSnapshotReader,
        strategy_catalog: tuple[StrategyCatalogEntry, ...],
        *,
        code_commit: str,
        proof_resolver: ProofResolver | None = None,
        runtime: RuntimeControlPort | None = None,
    ) -> None:
        if len(code_commit) != 40 or any(character not in "0123456789abcdefABCDEF" for character in code_commit):
            raise ValueError("local application requires an exact code commit")
        self._discovery = discovery
        self._snapshot_reader = snapshot_reader
        self._catalog = strategy_catalog
        self._code_commit = code_commit
        self._proof_resolver = proof_resolver or (lambda binding: None)
        self._runtime = runtime or UnconfiguredRuntimeControl()
        self._terminals: tuple[TerminalInstallation, ...] = ()
        self._terminal: TerminalSnapshot | None = None
        self._selected_symbol: BrokerSymbolOption | None = None
        self._selected_strategy: StrategyCatalogEntry | None = None
        self._selected_mode = OperatingMode.BACKTEST
        self._runtime_active = False
        self._new_entries_halted = False
        self._last_message = "ready"
        self._maintenance_active = False
        self._restart_required = False

    @property
    def runtime_active(self) -> bool:
        return bool(getattr(self._runtime, "active", self._runtime_active))

    def _require_idle(self) -> None:
        if self.runtime_active or self._maintenance_active:
            raise RuntimeError("selection_locked_while_work_is_active")
        if self._restart_required:
            raise RuntimeError("restart_required_after_development")

    def begin_development(self) -> None:
        self._require_idle()
        self._maintenance_active = True

    def finish_development(self) -> None:
        self._maintenance_active = False
        # Even a failed developer process may have changed files before returning.
        self._restart_required = True
        self._last_message = "restart_required_after_development"

    def refresh_terminals(self) -> LocalApplicationView:
        self._require_idle()
        self._terminals = self._discovery.discover()
        if self._terminal is not None and all(
            row.identity_key != self._terminal.installation.identity_key for row in self._terminals
        ):
            self._clear_selection("selected_terminal_no_longer_discovered")
        else:
            self._last_message = f"terminals_discovered:{len(self._terminals)}"
        return self.view()

    def connect_terminal(self, identity_key: str) -> LocalApplicationView:
        self._require_idle()
        installation = next((row for row in self._terminals if row.identity_key == identity_key), None)
        if installation is None:
            raise KeyError("terminal was not discovered")
        self._terminal = self._snapshot_reader.read(installation)
        self._selected_symbol = None
        self._selected_strategy = None
        self._selected_mode = OperatingMode.BACKTEST
        self._last_message = "terminal_connected_read_only"
        return self.view()

    def select_symbol(self, raw_symbol: str) -> LocalApplicationView:
        self._require_idle()
        if self._terminal is None:
            raise RuntimeError("connect a terminal before selecting a symbol")
        symbol = next((row for row in self._terminal.symbols if row.symbol == raw_symbol), None)
        if symbol is None:
            raise KeyError("symbol does not belong to the connected broker terminal")
        self._selected_symbol = symbol
        candidates = compatible_strategies(self._catalog, symbol)
        if self._selected_strategy not in candidates:
            self._selected_strategy = None
        self._last_message = f"symbol_selected:{symbol.symbol}"
        return self.view()

    def select_strategy(self, strategy_id: str) -> LocalApplicationView:
        self._require_idle()
        if self._selected_symbol is None:
            raise RuntimeError("select a symbol before selecting a strategy")
        candidates = compatible_strategies(self._catalog, self._selected_symbol)
        strategy = next((row for row in candidates if row.strategy_id == strategy_id), None)
        if strategy is None:
            raise KeyError("strategy is not compatible with the selected symbol")
        self._selected_strategy = strategy
        self._last_message = f"strategy_selected:{strategy.strategy_id}"
        return self.view()

    def select_mode(self, mode: OperatingMode) -> LocalApplicationView:
        self._require_idle()
        gate = next(row for row in self._mode_gates() if row.mode is mode)
        if not gate.available:
            self._last_message = f"mode_locked:{mode.value}:{'|'.join(gate.reasons)}"
            return self.view()
        self._selected_mode = mode
        self._last_message = f"mode_selected:{mode.value}"
        return self.view()

    def start(self) -> RuntimeActionResult:
        if self.runtime_active or self._maintenance_active or self._restart_required:
            return RuntimeActionResult(False, "work_active_or_restart_required")
        selection = self._runtime_selection()
        if selection is None:
            result = RuntimeActionResult(False, "terminal_symbol_strategy_selection_incomplete")
        else:
            gate = next(row for row in self._mode_gates() if row.mode is self._selected_mode)
            if not gate.available:
                result = RuntimeActionResult(False, f"mode_locked:{'|'.join(gate.reasons)}")
            elif self._new_entries_halted:
                result = RuntimeActionResult(False, "new_entries_halted")
            else:
                result = self._runtime.start(selection)
        self._last_message = result.message
        if result.accepted:
            self._runtime_active = True
        return result

    def stop_new_entries(self) -> RuntimeActionResult:
        self._new_entries_halted = True
        result = self._runtime.stop_new_entries()
        self._last_message = result.message
        return result

    def emergency_halt(self) -> RuntimeActionResult:
        self._new_entries_halted = True
        result = self._runtime.emergency_halt()
        if result.accepted and not hasattr(self._runtime, "active"):
            self._runtime_active = False
        self._last_message = result.message
        return result

    def view(self) -> LocalApplicationView:
        job = self._runtime.poll() if hasattr(self._runtime, "poll") else None
        symbols = self._terminal.symbols if self._terminal is not None else ()
        candidates = (
            compatible_strategies(self._catalog, self._selected_symbol)
            if self._selected_symbol is not None
            else ()
        )
        return LocalApplicationView(
            terminals=self._terminals,
            terminal=self._terminal,
            symbols=symbols,
            selected_symbol=self._selected_symbol,
            compatible_strategies=candidates,
            selected_strategy=self._selected_strategy,
            selected_mode=self._selected_mode,
            mode_gates=self._mode_gates(),
            runtime_configured=self._runtime.configured and (
                not hasattr(self._runtime, "supports") or (
                    self._runtime_selection() is not None and self._runtime.supports(self._runtime_selection())
                )
            ),
            runtime_active=self.runtime_active,
            new_entries_halted=self._new_entries_halted,
            last_message=self._last_message,
            runtime_message=job.message if job else "",
            run_directory=job.run_directory if job else "",
            maintenance_active=self._maintenance_active,
            restart_required=self._restart_required,
        )

    def _mode_gates(self) -> tuple[ModeGate, ...]:
        selection = self._selection_parts()
        if selection is None:
            return tuple(
                ModeGate(mode, False, ("terminal_symbol_strategy_selection_incomplete",))
                for mode in OperatingMode
            )
        terminal, symbol, strategy = selection
        binding = self._binding(terminal, symbol, strategy)
        return assess_mode_gates(
            terminal,
            symbol,
            strategy,
            binding=binding,
            proof=self._proof_resolver(binding),
            live_engineering_authorized=False,
        )

    def _selection_parts(
        self,
    ) -> tuple[TerminalSnapshot, BrokerSymbolOption, StrategyCatalogEntry] | None:
        if self._terminal is None or self._selected_symbol is None or self._selected_strategy is None:
            return None
        return self._terminal, self._selected_symbol, self._selected_strategy

    def _runtime_selection(self) -> RuntimeSelection | None:
        parts = self._selection_parts()
        if parts is None:
            return None
        terminal, symbol, strategy = parts
        return RuntimeSelection(
            terminal,
            symbol,
            strategy,
            self._selected_mode,
            self._binding(terminal, symbol, strategy),
        )

    def _binding(
        self,
        terminal: TerminalSnapshot,
        symbol: BrokerSymbolOption,
        strategy: StrategyCatalogEntry,
    ) -> QualificationBinding:
        return QualificationBinding(
            terminal.installation.identity_key,
            terminal.account.server,
            terminal.account.mode,
            symbol.symbol,
            strategy.strategy_hash,
            self._code_commit,
        )

    def _clear_selection(self, message: str) -> None:
        self._terminal = None
        self._selected_symbol = None
        self._selected_strategy = None
        self._selected_mode = OperatingMode.BACKTEST
        self._runtime_active = False
        self._last_message = message
