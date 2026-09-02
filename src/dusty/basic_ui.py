from __future__ import annotations

import argparse
import queue
import subprocess
import threading
from pathlib import Path
from typing import Any

from .codex_bridge import (
    CodexCLIReporter,
    CodexRequest,
    CodexSafetyContext,
    CodexTaskKind,
    build_support_bundle,
)
from .local_app import LocalDustyApplication
from .local_research import LocalResearchRuntime, ResearchSettings
from .local_terminal import ReadOnlyTerminalSnapshotReader, WindowsMT5Discovery
from .reviewed_strategies import reviewed_research_packages
from .strategy_catalog import OperatingMode, load_strategy_catalog


class DustyBasicUI:
    """One-window operational shell; trading intelligence remains in backend services."""

    def __init__(
        self,
        application: LocalDustyApplication,
        codex: CodexCLIReporter,
        *,
        code_commit: str,
        research: LocalResearchRuntime | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self._application = application
        self._codex = codex
        self._code_commit = code_commit
        self._research = research
        self._events: queue.Queue = queue.Queue()
        self._busy = False
        self._closing = False
        self._root = tk.Tk()
        self._root.title("Dusty Dragon — Local Control")
        self._root.minsize(800, 650)
        self._terminal_by_label: dict[str, str] = {}
        self._strategy_by_label: dict[str, str] = {}
        self._terminal_var = tk.StringVar()
        self._symbol_var = tk.StringVar()
        self._strategy_var = tk.StringVar()
        self._mode_var = tk.StringVar(value=OperatingMode.BACKTEST.value)
        self._account_var = tk.StringVar(value="No terminal connected")
        self._status_var = tk.StringVar(value="Ready")
        self._market_var = tk.StringVar(value="Terminal: not connected")
        self._position_var = tk.StringVar(value="Positions: —   Orders: —   Recent deals: —")
        self._lot_var = tk.StringVar(value="Broker minimum lot: select a symbol")
        self._capital_var = tk.StringVar(value="Preferred balance (risk sizing only): run research first")
        self._build()
        self._root.protocol("WM_DELETE_WINDOW", self._close)
        self._root.after(100, self._poll)
        self._refresh()

    def run(self) -> None:
        self._root.mainloop()

    def _build(self) -> None:
        ttk = self._ttk
        root = self._root
        outer = ttk.Frame(root, padding=16)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)

        ttk.Label(outer, text="MT5 Terminal").grid(row=0, column=0, sticky="w", pady=5)
        self._terminal_box = ttk.Combobox(outer, textvariable=self._terminal_var, state="readonly")
        self._terminal_box.grid(row=0, column=1, sticky="ew", padx=8, pady=5)
        self._refresh_button = ttk.Button(outer, text="Refresh", command=self._refresh)
        self._refresh_button.grid(row=0, column=2, padx=4)
        self._connect_button = ttk.Button(outer, text="Connect", command=self._connect)
        self._connect_button.grid(row=0, column=3, padx=4)

        ttk.Label(outer, text="Account").grid(row=1, column=0, sticky="nw", pady=5)
        ttk.Label(outer, textvariable=self._account_var, wraplength=490).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=8, pady=5
        )
        self._account_refresh_button = ttk.Button(outer, text="Refresh Account", command=self._refresh_account)
        self._account_refresh_button.grid(row=1, column=3, padx=4)

        ttk.Label(outer, text="Symbol").grid(row=2, column=0, sticky="w", pady=5)
        self._symbol_box = ttk.Combobox(outer, textvariable=self._symbol_var, state="readonly")
        self._symbol_box.grid(row=2, column=1, columnspan=3, sticky="ew", padx=8, pady=5)
        self._symbol_box.bind("<<ComboboxSelected>>", self._select_symbol)

        ttk.Label(outer, text="Strategy").grid(row=3, column=0, sticky="w", pady=5)
        self._strategy_box = ttk.Combobox(outer, textvariable=self._strategy_var, state="readonly")
        self._strategy_box.grid(row=3, column=1, columnspan=3, sticky="ew", padx=8, pady=5)
        self._strategy_box.bind("<<ComboboxSelected>>", self._select_strategy)

        ttk.Label(outer, text="Mode").grid(row=4, column=0, sticky="nw", pady=8)
        mode_frame = ttk.Frame(outer)
        mode_frame.grid(row=4, column=1, columnspan=3, sticky="w", padx=8, pady=8)
        self._mode_buttons: dict[OperatingMode, Any] = {}
        for index, mode in enumerate(OperatingMode):
            button = ttk.Radiobutton(
                mode_frame,
                text=mode.value.title(),
                value=mode.value,
                variable=self._mode_var,
                command=lambda selected=mode: self._select_mode(selected),
            )
            button.grid(row=0, column=index, padx=(0, 18))
            self._mode_buttons[mode] = button

        ttk.Separator(outer).grid(row=5, column=0, columnspan=4, sticky="ew", pady=12)
        controls = ttk.Frame(outer)
        controls.grid(row=6, column=0, columnspan=4, sticky="ew")
        self._start_button = ttk.Button(controls, text="Start", command=self._start)
        self._start_button.pack(side="left", padx=(0, 8))
        ttk.Button(controls, text="Stop Entries", command=self._stop_entries).pack(side="left", padx=8)
        ttk.Button(controls, text="Emergency Halt", command=self._halt).pack(side="left", padx=8)
        self._report_button = ttk.Button(controls, text="Codex Report", command=self._codex_report)
        self._report_button.pack(side="right", padx=4)
        self._development_button = ttk.Button(controls, text="Codex Development", command=self._codex_development)
        self._development_button.pack(side="right", padx=4)

        status = ttk.LabelFrame(outer, text="Status", padding=12)
        status.grid(row=7, column=0, columnspan=4, sticky="nsew", pady=(16, 0))
        outer.rowconfigure(7, weight=1)
        ttk.Label(status, textvariable=self._market_var).pack(anchor="w", pady=2)
        ttk.Label(status, textvariable=self._position_var).pack(anchor="w", pady=2)
        ttk.Label(status, textvariable=self._lot_var, wraplength=730).pack(anchor="w", pady=2)
        ttk.Label(status, textvariable=self._capital_var, wraplength=730).pack(anchor="w", pady=6)
        ttk.Label(status, textvariable=self._status_var, wraplength=640).pack(anchor="w", pady=2)
        self._results_button = ttk.Button(status, text="Last Research Results", command=self._show_research_result)
        self._results_button.pack(anchor="w", pady=8)

    def _poll(self) -> None:
        # All Tk calls, including after(), originate on the Tk thread. Workers only queue data.
        try:
            while True:
                callback, result, error = self._events.get_nowait()
                self._busy = False
                callback(result, error)
        except queue.Empty:
            pass
        if not self._busy:
            self._guard(lambda: self._render(self._application.view()))
        if self._closing and not self._busy and not self._application.runtime_active:
            self._root.destroy()
            return
        self._root.after(200, self._poll)

    def _background(self, action: Any, callback: Any) -> None:
        if self._busy or self._application.runtime_active or self._closing:
            self._show_error("Wait for the current operation to finish.")
            return
        self._busy = True
        self._render(self._application.view())
        self._status_var.set("Working…")

        def worker() -> None:
            try:
                result, error = action(), None
            except Exception as exc:
                result, error = None, f"{type(exc).__name__}: {exc}"
            self._events.put((callback, result, error))

        threading.Thread(target=worker, name="dusty-desktop-task", daemon=True).start()

    def _background_view(self, result: Any, error: str | None) -> None:
        if error:
            self._show_error(error)
        else:
            self._render(result)

    def _refresh(self) -> None:
        self._background(self._application.refresh_terminals, self._background_view)

    def _connect(self) -> None:
        label = self._terminal_var.get()
        identity = self._terminal_by_label.get(label)
        if identity is None:
            self._show_error("Select a discovered MT5 terminal first.")
            return
        self._background(lambda: self._application.connect_terminal(identity), self._background_view)

    def _refresh_account(self) -> None:
        self._background(self._application.refresh_account, self._background_view)

    def _start_after_account_refresh(self, result: Any, error: str | None) -> None:
        if error:
            self._show_error(error)
        elif not self._closing:
            self._guard(lambda: self._finish_action(self._application.start()))

    def _select_symbol(self, _event: object = None) -> None:
        value = self._symbol_var.get()
        if value:
            self._guard(lambda: self._render(self._application.select_symbol(value)))

    def _select_strategy(self, _event: object = None) -> None:
        strategy_id = self._strategy_by_label.get(self._strategy_var.get())
        if strategy_id:
            self._guard(lambda: self._render(self._application.select_strategy(strategy_id)))

    def _select_mode(self, mode: OperatingMode) -> None:
        self._guard(lambda: self._render(self._application.select_mode(mode)))

    def _start(self) -> None:
        if self._busy or self._application.runtime_active:
            return
        if self._research is None:
            self._guard(lambda: self._finish_action(self._application.start()))
            return
        # A small explicit assumption sheet, not an automatic optimizer.
        from tkinter import messagebox
        window = self._tk.Toplevel(self._root)
        window.title("Read-only research assumptions")
        window.transient(self._root)
        window.grab_set()
        self._ttk.Label(window, text=(
            "M15 research hypothesis only. No broker orders.\n"
            "Costs are assumptions, NOT verified broker fees.\n"
            "Swaps/fees are incomplete; results never unlock trading."
        ), padding=12).grid(row=0, column=0, columnspan=2)
        settings = self._research.settings
        variables = []
        labels = ("History days (1–30)", "Round-trip commission / lot (account currency)",
                  "Total slippage (broker points)", "Spread floor (broker points)")
        defaults = (settings.history_days, settings.commission_per_lot, settings.slippage_points, settings.spread_floor_points)
        for index, (label, value) in enumerate(zip(labels, defaults), 1):
            variable = self._tk.StringVar(value=str(value))
            variables.append(variable)
            self._ttk.Label(window, text=label).grid(row=index, column=0, padx=12, pady=4, sticky="w")
            self._ttk.Entry(window, textvariable=variable, width=16).grid(row=index, column=1, padx=12, pady=4)

        def launch() -> None:
            try:
                self._research.settings = ResearchSettings(int(variables[0].get()), *(float(v.get()) for v in variables[1:]))
            except ValueError as exc:
                messagebox.showerror("Invalid research setting", str(exc), parent=window)
                return
            window.destroy()
            # Read a fresh balance before freezing a new research request. Account
            # changes fail closed; this thread never logs in or sends orders.
            self._background(self._application.refresh_account, self._start_after_account_refresh)

        self._ttk.Button(window, text="Run research (no orders)", command=launch).grid(row=5, column=0, columnspan=2, pady=12)

    def _stop_entries(self) -> None:
        self._guard(lambda: self._finish_action(self._application.stop_new_entries()))

    def _halt(self) -> None:
        from tkinter import messagebox

        if messagebox.askyesno("Emergency halt", "Cancel Dusty's research and latch Start off until restart? This does NOT close any MT5 positions."):
            self._guard(lambda: self._finish_action(self._application.emergency_halt()))

    def _codex_report(self) -> None:
        request = CodexRequest(
            CodexTaskKind.REPORT,
            (
                "Audit this Dusty Dragon support bundle. Explain the current state, blockers, "
                "and safest next action. Do not modify files."
            ),
            build_support_bundle(self._application.view(), code_commit=self._code_commit),
        )
        self._run_codex(request, human_confirmed=True)

    def _codex_development(self) -> None:
        from tkinter import messagebox, simpledialog

        instruction = simpledialog.askstring(
            "Codex development",
            "Describe the repository change or investigation:",
            parent=self._root,
        )
        if not instruction:
            return
        confirmed = messagebox.askyesno(
            "Allow repository edits",
            "Allow Codex repository edits? Research stays paused; restart Dusty afterward. This is not broker authorization.",
        )
        if not confirmed:
            return
        request = CodexRequest(
            CodexTaskKind.DEVELOPMENT,
            instruction,
            build_support_bundle(self._application.view(), code_commit=self._code_commit),
            timeout_seconds=1800,
        )
        self._run_codex(request, human_confirmed=True)

    def _run_codex(self, request: CodexRequest, *, human_confirmed: bool) -> None:
        view = self._application.view()
        if self._busy or view.runtime_active or view.restart_required:
            self._show_error("Finish or cancel active work first; restart after development edits.")
            return
        safety = CodexSafetyContext(
            human_confirmed,
            _repository_clean(self._codex.repository),
            view.runtime_active,
        )
        if request.kind is CodexTaskKind.DEVELOPMENT:
            self._application.begin_development()

        def complete(result: Any, error: str | None) -> None:
            if request.kind is CodexTaskKind.DEVELOPMENT:
                self._application.finish_development()
            if error:
                self._show_error(error)
            else:
                self._show_codex_result(result.output, result.error, result.accepted)

        self._background(lambda: self._codex.run(request, safety), complete)

    def _show_codex_result(self, output: str, error: str, accepted: bool) -> None:
        window = self._tk.Toplevel(self._root)
        window.title("Codex result")
        text = self._tk.Text(window, wrap="word", width=100, height=32)
        text.pack(fill="both", expand=True, padx=10, pady=10)
        body = output.strip() or error.strip() or "No output returned."
        text.insert("1.0", body)
        text.configure(state="disabled")
        self._status_var.set("Codex request completed" if accepted else f"Codex request failed: {error}")

    def _finish_action(self, result: Any) -> None:
        self._render(self._application.view())
        if not result.accepted:
            self._show_error(result.message)

    def _render(self, view: Any) -> None:
        self._terminal_by_label = {row.display_name: row.identity_key for row in view.terminals}
        self._terminal_box.configure(values=tuple(self._terminal_by_label))
        if self._terminal_var.get() not in self._terminal_by_label:
            self._terminal_var.set("")

        self._symbol_box.configure(values=tuple(row.symbol for row in view.symbols))
        self._symbol_var.set(view.selected_symbol.symbol if view.selected_symbol else "")
        self._strategy_by_label = {
            f"{row.title} [{row.strategy_id}]": row.strategy_id for row in view.compatible_strategies
        }
        self._strategy_box.configure(values=tuple(self._strategy_by_label))
        selected_label = next(
            (
                label
                for label, value in self._strategy_by_label.items()
                if view.selected_strategy and value == view.selected_strategy.strategy_id
            ),
            "",
        )
        self._strategy_var.set(selected_label)

        gates = {row.mode: row for row in view.mode_gates}
        locked = self._busy or view.runtime_active or view.maintenance_active or view.restart_required or self._closing
        for mode, button in self._mode_buttons.items():
            button.configure(state="normal" if gates[mode].available and not locked else "disabled")
        self._mode_var.set(view.selected_mode.value)
        for box in (self._terminal_box, self._symbol_box, self._strategy_box):
            box.configure(state="disabled" if locked else "readonly")
        for button in (self._refresh_button, self._connect_button, self._report_button, self._development_button):
            button.configure(state="disabled" if locked else "normal")
        self._account_refresh_button.configure(state="normal" if view.terminal and not locked else "disabled")
        symbol = view.selected_symbol
        self._lot_var.set(
            f"Broker minimum lot: {symbol.volume_min:g} lots · Step: {symbol.volume_step:g} · {symbol.symbol}"
            if symbol and symbol.volume_min > 0 else "Broker minimum lot: unavailable — select a symbol with valid broker economics"
        )
        self._capital_var.set(view.capital_summary.display() if view.capital_summary else
                             "Preferred balance (risk sizing only): unavailable — complete research for this selection")

        if view.terminal is None:
            self._account_var.set("No terminal connected")
            self._market_var.set("Terminal: not connected")
            self._position_var.set("Positions: —   Orders: —   Recent deals: —")
        else:
            account = view.terminal.account
            self._account_var.set(
                f"{account.company or account.server} · {account.mode.value.upper()} · {account.login_hint}\n"
                f"Current balance: {account.balance:,.2f} {account.currency} · Equity: {account.equity:,.2f}\n"
                f"Last checked: {view.terminal.captured_at.strftime('%Y-%m-%d %H:%M:%S %Z')} (not a live feed)"
            )
            self._market_var.set(
                f"Terminal build {view.terminal.terminal_build} · "
                f"{'connected' if view.terminal.connected else 'disconnected'} · Risk/execution reads only"
            )
            self._position_var.set(
                f"Positions: {view.terminal.open_positions}   Orders: {view.terminal.active_orders}   "
                f"Recent deals: {view.terminal.recent_deals}"
            )
        gate = gates.get(view.selected_mode)
        lock = "" if gate is None or gate.available else f" · Locked: {', '.join(gate.reasons)}"
        message = view.runtime_message if view.run_directory else view.last_message
        if view.run_directory and not view.runtime_active:
            message = f"Last run: {message}"
        if view.restart_required:
            message = "Restart Dusty after development; loaded code has not been refreshed."
        elif view.new_entries_halted and not view.runtime_active:
            message = "Research halted. Restart Dusty to allow a new run. No MT5 positions managed."
        elif view.selected_strategy and not view.runtime_configured:
            message = "Selected catalog entry has no reviewed executable research package."
        warnings = "; ".join(view.terminal.warnings) if view.terminal else ""
        if not self._busy:
            self._status_var.set(f"{message.splitlines()[0]}{lock}" + (f" · Read warnings: {warnings}" if warnings else ""))
        can_start = view.runtime_configured and gate is not None and gate.available and not view.new_entries_halted and not locked
        self._start_button.configure(state="normal" if can_start else "disabled")
        self._results_button.configure(state="normal" if view.run_directory else "disabled")

    def _show_research_result(self) -> None:
        view = self._application.view()
        window = self._tk.Toplevel(self._root)
        window.title("Research evidence — not trading certification")
        text = self._tk.Text(window, wrap="word", width=100, height=30)
        text.pack(fill="both", expand=True, padx=10, pady=10)
        text.insert("1.0", f"{view.runtime_message}\n\nSaved locally:\n{view.run_directory}")
        text.configure(state="disabled")

    def _close(self) -> None:
        from tkinter import messagebox
        if self._busy:
            messagebox.showinfo("Task active", "Wait for the current connection or Codex task to finish before closing.")
            return
        if self._application.runtime_active:
            if not messagebox.askyesno("Cancel research?", "Cancel this read-only research worker and close Dusty? MT5 is left running."):
                return
            self._application.emergency_halt()
        self._closing = True

    def _guard(self, action: Any) -> None:
        try:
            action()
        except Exception as exc:  # UI boundary converts typed failures into visible status.
            self._show_error(f"{type(exc).__name__}: {exc}")

    def _show_error(self, message: str) -> None:
        from tkinter import messagebox

        self._status_var.set(message)
        messagebox.showerror("Dusty Dragon", message)


def _repository_clean(repository: Path | None = None) -> bool:
    repo = repository or Path.cwd()
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.returncode == 0 and not completed.stdout.strip()


def _current_commit(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    commit = completed.stdout.strip()
    if len(commit) != 40 or any(character not in "0123456789abcdefABCDEF" for character in commit):
        raise RuntimeError("Git did not return an exact commit")
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dusty Dragon bare-bones local MT5 control panel")
    parser.add_argument("--terminal", action="append", default=[], help="manual terminal.exe/terminal64.exe path")
    parser.add_argument("--catalog", type=Path, help="reviewed non-executable strategy catalog JSON")
    parser.add_argument("--repository", type=Path, default=Path.cwd(), help="Dusty Git repository")
    parser.add_argument("--research-directory", type=Path, help="local artifacts outside the Git repository")
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    if Path(__file__).resolve() != repository / "src" / "dusty" / "basic_ui.py":
        parser.error("--repository must be the repository providing this installed dusty module")
    commit = _current_commit(repository)
    # External catalogs remain metadata-only. Only exact built-in packages can execute.
    catalog = load_strategy_catalog(args.catalog) if args.catalog else tuple(
        package.catalog_entry for package in reviewed_research_packages()
    )
    research = LocalResearchRuntime(repository, output_directory=args.research_directory)
    application = LocalDustyApplication(
        WindowsMT5Discovery(manual_paths=args.terminal),
        ReadOnlyTerminalSnapshotReader(),
        catalog,
        code_commit=commit,
        runtime=research,
    )
    DustyBasicUI(application, CodexCLIReporter(repository), code_commit=commit, research=research).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
