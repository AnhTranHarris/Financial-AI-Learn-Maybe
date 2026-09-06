from __future__ import annotations

"""Tk integration for bounded M196.5 strategy discovery.

This module extends the existing one-window PC shell without granting the UI any
new trading authority. Discovery runs only through StrategyDiscoveryService and
therefore remains research-only, fail-closed, and outside the broker-write path.
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import basic_ui
from .basic_ui import DustyBasicUI
from .codex_bridge import CodexCLIReporter
from .local_app import LocalDustyApplication
from .local_research import LocalResearchRuntime
from .local_terminal import ReadOnlyTerminalSnapshotReader, WindowsMT5Discovery
from .provider_registry import ProviderRegistry
from .reviewed_strategies import reviewed_research_packages
from .strategy_catalog import load_strategy_catalog
from .strategy_discovery import (
    DiscoveryMode,
    DiscoveryTrigger,
    StrategyDiscoveryResult,
    StrategyDiscoveryService,
    format_central,
    most_recent_sunday_slot_utc,
)


class DustyStrategyDiscoveryUI(DustyBasicUI):
    """Existing Dusty UI plus a research-only strategy-discovery control."""

    def __init__(
        self,
        application: LocalDustyApplication,
        codex: CodexCLIReporter,
        *,
        code_commit: str,
        research: LocalResearchRuntime | None = None,
        providers: ProviderRegistry | None = None,
        strategy_discovery: StrategyDiscoveryService,
    ) -> None:
        self._strategy_discovery = strategy_discovery
        super().__init__(
            application,
            codex,
            code_commit=code_commit,
            research=research,
            providers=providers,
        )
        self._root.minsize(840, 720)
        self._discovery_status_var = self._tk.StringVar(value="Strategy discovery: schedule loading")
        self._build_discovery_bar()
        self._refresh_discovery_status()
        self._root.after(5000, self._scheduled_discovery_tick)

    def _build_discovery_bar(self) -> None:
        frame = self._ttk.LabelFrame(self._root, text="Strategy Discovery — research only", padding=10)
        frame.pack(side="bottom", fill="x", padx=16, pady=(0, 12))
        self._ttk.Label(frame, textvariable=self._discovery_status_var, wraplength=650).pack(
            side="left", fill="x", expand=True, padx=(0, 10)
        )
        self._discovery_button = self._ttk.Button(
            frame,
            text="Search Strategies Now",
            command=self._open_discovery_dialog,
        )
        self._discovery_button.pack(side="right")

    def _refresh_discovery_status(self) -> None:
        try:
            self._discovery_status_var.set(self._strategy_discovery.status_line(datetime.now(timezone.utc)))
        except Exception as exc:
            self._discovery_status_var.set(f"Strategy discovery schedule unavailable: {type(exc).__name__}: {exc}")

    def _open_discovery_dialog(self) -> None:
        if self._busy or self._application.runtime_active or self._closing:
            self._show_error("Finish or cancel active work before starting strategy discovery.")
            return
        window = self._tk.Toplevel(self._root)
        window.title("Search strategies — research only")
        window.transient(self._root)
        window.grab_set()
        mode = self._tk.StringVar(value=DiscoveryMode.BOTH.value)
        self._ttk.Label(
            window,
            text=(
                "Dusty may inspect allowlisted Vibe research sources and local web-search leads.\n"
                "Single-symbol hypotheses can enter the Strategy Estate only through bounded Ollama reconstruction.\n"
                "Cross-symbol web results remain untrusted leads until Dusty has an explicit multi-symbol strategy compiler.\n"
                "No broker orders, Champion promotion, risk override, Guardian bypass, or live hot-swap."
            ),
            padding=12,
        ).pack(anchor="w")
        for text, value in (
            ("New single-symbol strategies", DiscoveryMode.NEW_STRATEGIES),
            ("Cross-symbol / intermarket leads", DiscoveryMode.CROSS_SYMBOL),
            ("Both", DiscoveryMode.BOTH),
        ):
            self._ttk.Radiobutton(window, text=text, value=value.value, variable=mode).pack(
                anchor="w", padx=16, pady=3
            )

        buttons = self._ttk.Frame(window, padding=12)
        buttons.pack(fill="x")

        def run() -> None:
            try:
                selected = DiscoveryMode(mode.get())
            except ValueError:
                self._show_error("Select a valid strategy-discovery mode.")
                return
            window.grab_release()
            window.destroy()
            self._launch_discovery(selected, DiscoveryTrigger.MANUAL)

        self._ttk.Button(buttons, text="Run Search", command=run).pack(side="left")
        self._ttk.Button(buttons, text="Cancel", command=window.destroy).pack(side="right")

    def _launch_discovery(self, mode: DiscoveryMode, trigger: DiscoveryTrigger) -> None:
        label = "scheduled" if trigger is DiscoveryTrigger.SCHEDULED else "manual"
        self._discovery_status_var.set(f"Strategy discovery: {label} {mode.value.replace('_', ' ')} scan running…")

        def complete(result: Any, error: str | None) -> None:
            self._finish_discovery(result, error, trigger)

        self._background(
            lambda: self._strategy_discovery.discover(mode, trigger=trigger),
            complete,
        )

    def _finish_discovery(
        self,
        result: Any,
        error: str | None,
        trigger: DiscoveryTrigger,
    ) -> None:
        from tkinter import messagebox

        self._refresh_discovery_status()
        if error:
            message = f"Strategy discovery failed: {error}"
            self._status_var.set(message)
            if trigger is DiscoveryTrigger.MANUAL:
                messagebox.showerror("Strategy discovery", message, parent=self._root)
            return
        if not isinstance(result, StrategyDiscoveryResult):
            message = "Strategy discovery returned an invalid result type."
            self._status_var.set(message)
            if trigger is DiscoveryTrigger.MANUAL:
                messagebox.showerror("Strategy discovery", message, parent=self._root)
            return

        body = (
            f"Status: {result.status.value.upper()}\n"
            f"Source proposals seen: {result.proposals_seen}\n"
            f"New bounded single-symbol candidates: {result.new_single_symbol_candidates}\n"
            f"Added to Strategy Estate: {result.added_to_estate}\n"
            f"Deferred cross-symbol catalog candidates: {result.deferred_cross_symbol_candidates}\n"
            f"Archived cross-symbol web searches: {result.cross_symbol_web_leads}\n"
            f"Report: {result.report_path}"
        )
        if result.errors:
            body += f"\nPartial/unavailable findings: {len(result.errors)} — inspect the saved report."
        if result.restart_required:
            body += (
                "\n\nNew immutable Strategy Estate rows were added. Restart Dusty to load the new "
                "hash-pinned estate snapshot; the current process will not hot-swap them."
            )
        self._status_var.set(
            "Strategy discovery completed; restart required for new estate rows."
            if result.restart_required
            else f"Strategy discovery {result.status.value}."
        )
        if trigger is DiscoveryTrigger.MANUAL:
            messagebox.showinfo("Strategy discovery", body, parent=self._root)

    def _scheduled_discovery_tick(self) -> None:
        if self._closing:
            return
        try:
            if not self._busy and not self._application.runtime_active:
                now = datetime.now(timezone.utc)
                state = self._strategy_discovery.state()
                initialized = state.last_attempt_utc is not None or state.last_completed_utc is not None
                due = self._strategy_discovery.scheduled_due(now)
                if not initialized:
                    # On a brand-new install, do not treat every earlier Sunday
                    # as missed. Arm naturally: run if the UI is actually open
                    # on the current Central Sunday after 08:00. Once any scan
                    # has run, normal catch-up semantics apply on later starts.
                    recent = most_recent_sunday_slot_utc(now)
                    due = due and format_central(now)[:10] == format_central(recent)[:10]
                if due:
                    self._launch_discovery(DiscoveryMode.BOTH, DiscoveryTrigger.SCHEDULED)
        except Exception as exc:
            self._discovery_status_var.set(
                f"Strategy discovery scheduler unavailable: {type(exc).__name__}: {exc}"
            )
        finally:
            if not self._closing:
                self._root.after(60_000, self._scheduled_discovery_tick)


def run_strategy_discovery_ui(
    argv: list[str] | None,
    strategy_discovery: StrategyDiscoveryService | None,
) -> int:
    """Launch the existing PC shell, using the discovery extension when enabled."""

    parser = argparse.ArgumentParser(description="Dusty Dragon local MT5 control panel")
    parser.add_argument("--terminal", action="append", default=[], help="manual terminal.exe/terminal64.exe path")
    parser.add_argument("--catalog", type=Path, help="reviewed non-executable strategy catalog JSON")
    parser.add_argument("--repository", type=Path, default=Path.cwd(), help="Dusty Git repository")
    parser.add_argument("--research-directory", type=Path, help="local artifacts outside the Git repository")
    parser.add_argument("--provider-root", type=Path, help="optional isolated provider root; defaults to ~/DustyProviders")
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    if Path(basic_ui.__file__).resolve() != repository / "src" / "dusty" / "basic_ui.py":
        parser.error("--repository must be the repository providing this installed dusty module")
    commit = basic_ui._current_commit(repository)
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
    ui_type = DustyStrategyDiscoveryUI if strategy_discovery is not None else DustyBasicUI
    kwargs: dict[str, object] = {
        "code_commit": commit,
        "research": research,
        "providers": ProviderRegistry(args.provider_root),
    }
    if strategy_discovery is not None:
        kwargs["strategy_discovery"] = strategy_discovery
    ui_type(
        application,
        CodexCLIReporter(repository),
        **kwargs,
    ).run()
    return 0
