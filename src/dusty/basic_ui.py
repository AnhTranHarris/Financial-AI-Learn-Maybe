from __future__ import annotations

import argparse
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
from .local_terminal import ReadOnlyTerminalSnapshotReader, WindowsMT5Discovery
from .strategy_catalog import OperatingMode, load_strategy_catalog


class DustyBasicUI:
    """One-window operational shell; trading intelligence remains in backend services."""

    def __init__(
        self,
        application: LocalDustyApplication,
        codex: CodexCLIReporter,
        *,
        code_commit: str,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self._ttk = ttk
        self._application = application
        self._codex = codex
        self._code_commit = code_commit
        self._root = tk.Tk()
        self._root.title("Dusty Dragon — Local Control")
        self._root.minsize(700, 470)
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
        self._build()
        self._render(self._application.refresh_terminals())

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
        ttk.Button(outer, text="Refresh", command=self._refresh).grid(row=0, column=2, padx=4)
        ttk.Button(outer, text="Connect", command=self._connect).grid(row=0, column=3, padx=4)

        ttk.Label(outer, text="Account").grid(row=1, column=0, sticky="nw", pady=5)
        ttk.Label(outer, textvariable=self._account_var, wraplength=560).grid(
            row=1, column=1, columnspan=3, sticky="w", padx=8, pady=5
        )

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
        ttk.Button(controls, text="Codex Report", command=self._codex_report).pack(side="right", padx=4)
        ttk.Button(controls, text="Codex Development", command=self._codex_development).pack(
            side="right", padx=4
        )

        status = ttk.LabelFrame(outer, text="Status", padding=12)
        status.grid(row=7, column=0, columnspan=4, sticky="nsew", pady=(16, 0))
        outer.rowconfigure(7, weight=1)
        ttk.Label(status, textvariable=self._market_var).pack(anchor="w", pady=2)
        ttk.Label(status, textvariable=self._position_var).pack(anchor="w", pady=2)
        ttk.Label(status, textvariable=self._status_var, wraplength=640).pack(anchor="w", pady=2)

    def _refresh(self) -> None:
        self._guard(lambda: self._render(self._application.refresh_terminals()))

    def _connect(self) -> None:
        label = self._terminal_var.get()
        identity = self._terminal_by_label.get(label)
        if identity is None:
            self._show_error("Select a discovered MT5 terminal first.")
            return
        self._guard(lambda: self._render(self._application.connect_terminal(identity)))

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
        self._guard(lambda: self._finish_action(self._application.start()))

    def _stop_entries(self) -> None:
        self._guard(lambda: self._finish_action(self._application.stop_new_entries()))

    def _halt(self) -> None:
        from tkinter import messagebox

        if messagebox.askyesno("Emergency halt", "Halt all new entries and invoke the runtime halt policy?"):
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
            "Codex may edit only the Dusty repository in a workspace-write sandbox. Continue?",
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
        safety = CodexSafetyContext(
            human_confirmed,
            _repository_clean(self._codex.repository),
            view.runtime_active,
        )
        self._status_var.set(f"Codex {request.kind.value} running…")

        def worker() -> None:
            result = self._codex.run(request, safety)
            self._root.after(0, lambda: self._show_codex_result(result.output, result.error, result.accepted))

        threading.Thread(target=worker, name="dusty-codex", daemon=True).start()

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
            f"{row.title} [{row.stage.value}]": row.strategy_id for row in view.compatible_strategies
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
        for mode, button in self._mode_buttons.items():
            button.configure(state="normal" if gates[mode].available else "disabled")
        if not gates[view.selected_mode].available:
            self._mode_var.set(OperatingMode.BACKTEST.value)
        else:
            self._mode_var.set(view.selected_mode.value)

        if view.terminal is None:
            self._account_var.set("No terminal connected")
            self._market_var.set("Terminal: not connected")
            self._position_var.set("Positions: —   Orders: —   Recent deals: —")
        else:
            account = view.terminal.account
            self._account_var.set(
                f"{account.company or account.server} · {account.mode.value.upper()} · {account.login_hint} · "
                f"Balance {account.balance:.2f} {account.currency} · Equity {account.equity:.2f}"
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
        self._status_var.set(f"{view.last_message}{lock}")
        can_start = view.runtime_configured and gate is not None and gate.available and not view.new_entries_halted
        self._start_button.configure(state="normal" if can_start else "disabled")

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
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    commit = _current_commit(repository)
    catalog = load_strategy_catalog(args.catalog) if args.catalog else ()
    application = LocalDustyApplication(
        WindowsMT5Discovery(manual_paths=args.terminal),
        ReadOnlyTerminalSnapshotReader(),
        catalog,
        code_commit=commit,
    )
    DustyBasicUI(application, CodexCLIReporter(repository), code_commit=commit).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
