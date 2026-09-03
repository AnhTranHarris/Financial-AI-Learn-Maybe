from __future__ import annotations

"""M114 desktop shell with explicit single-provider or all-three selection."""

import argparse
from datetime import timedelta
from pathlib import Path
from typing import Any

from .basic_ui import DustyBasicUI, _current_commit
from .codex_bridge import CodexCLIReporter
from .local_app import LocalDustyApplication
from .local_research import LocalResearchRuntime
from .local_terminal import ReadOnlyTerminalSnapshotReader, WindowsMT5Discovery
from .provider_forecast_adapter import _smoke_bars
from .provider_multi_service import (
    ForecastContractorManager,
    ForecastSelectionMode,
)
from .provider_registry import ProviderRegistry
from .reviewed_strategies import reviewed_research_packages
from .strategy_catalog import load_strategy_catalog


class DustyProviderUI(DustyBasicUI):
    def __init__(self, *args: Any, providers: ProviderRegistry, **kwargs: Any) -> None:
        self._provider_manager = ForecastContractorManager(providers)
        super().__init__(*args, providers=providers, **kwargs)

    def _provider_selection_label(self) -> str:
        if self._providers is None:
            return "unavailable"
        names = {
            row.spec.provider_id: row.spec.display_name
            for row in self._providers.discover()
        }
        ids = self._provider_manager.selected_provider_ids
        if len(ids) == 3:
            return "ALL THREE (independent evidence)"
        return names.get(ids[0], ids[0])

    def _refresh_provider_status(self) -> None:
        if self._providers is None:
            self._provider_status_var.set("Forecast contractors: discovery unavailable")
            return
        snapshots = self._providers.discover()
        installed = sum(snapshot.selectable for snapshot in snapshots)
        states = self._provider_manager.states()
        state_text = ", ".join(
            f"{provider_id}={state.value.upper()}"
            for provider_id, state in states.items()
        )
        self._provider_status_var.set(
            f"Forecast contractors: {installed}/{len(snapshots)} installed · "
            f"selected: {self._provider_selection_label()} · {state_text}"
        )

    def _show_providers(self) -> None:
        from tkinter import messagebox

        if self._providers is None:
            self._show_error("Forecast contractor discovery is not configured.")
            return
        self._refresh_provider_status()
        window = self._tk.Toplevel(self._root)
        window.title("Forecast contractors — research only")
        window.transient(self._root)
        frame = self._ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        self._ttk.Label(
            frame,
            text=(
                "Select ONE contractor or ALL THREE. Selection alone starts nothing.\n"
                "ALL THREE means three independent ForecastEvidence records; no averaging, voting or hidden ensemble.\n"
                "Workers are CPU-only, isolated and RESEARCH_ONLY. They cannot write to MT5, veto entries or promote strategies."
            ),
            wraplength=950,
        ).grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 10))

        selected = self._tk.StringVar(value=self._provider_manager.selection.value)
        options = (
            ("Amazon Chronos-2", ForecastSelectionMode.CHRONOS2),
            ("Kronos-small", ForecastSelectionMode.KRONOS_SMALL),
            ("TimesFM 2.5", ForecastSelectionMode.TIMESFM25),
            ("Use all three — independent evidence", ForecastSelectionMode.ALL_THREE),
        )
        selection_frame = self._ttk.LabelFrame(frame, text="Forecast contractor mode", padding=8)
        selection_frame.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(0, 10))
        for index, (label, mode) in enumerate(options):
            self._ttk.Radiobutton(
                selection_frame,
                text=label,
                value=mode.value,
                variable=selected,
            ).grid(row=index, column=0, sticky="w", pady=2)

        headings = ("Provider", "Install", "Runtime", "Model", "License", "Process")
        for column, heading in enumerate(headings):
            self._ttk.Label(frame, text=heading).grid(
                row=2, column=column, sticky="w", padx=(0, 14), pady=4
            )
        state_map = self._provider_manager.states()
        pid_map = self._provider_manager.pids()
        for row_index, snapshot in enumerate(self._providers.discover(), start=3):
            process = state_map[snapshot.spec.provider_id].value.upper()
            if pid_map[snapshot.spec.provider_id] is not None:
                process += f" PID {pid_map[snapshot.spec.provider_id]}"
            values = (
                snapshot.spec.display_name,
                snapshot.health.value.upper(),
                snapshot.spec.runtime_version or "—",
                snapshot.spec.model_id,
                snapshot.spec.license_id,
                process,
            )
            for column, value in enumerate(values):
                self._ttk.Label(
                    frame,
                    text=value,
                    wraplength=250,
                ).grid(row=row_index, column=column, sticky="w", padx=(0, 14), pady=4)

        def apply_selection() -> None:
            try:
                mode = self._provider_manager.select(selected.get())
            except (ValueError, KeyError) as exc:
                messagebox.showerror("Forecast contractors", str(exc), parent=window)
                return
            self._refresh_provider_status()
            messagebox.showinfo(
                "Forecast contractors",
                f"Selection saved for this Dusty session: {mode.value}.\nNo model was started.",
                parent=window,
            )

        def start_complete(result: Any, error: str | None) -> None:
            self._refresh_provider_status()
            if error:
                self._show_error(error)
                return
            rendered = "\n".join(f"{key}: {value.value.upper()}" for key, value in result.items())
            messagebox.showinfo("Forecast contractor startup", rendered, parent=window)
            window.destroy()
            self._show_providers()

        def start_selected() -> None:
            try:
                self._provider_manager.select(selected.get())
            except ValueError as exc:
                messagebox.showerror("Forecast contractors", str(exc), parent=window)
                return
            self._refresh_provider_status()
            self._background(self._provider_manager.start_selected, start_complete)

        def stop_complete(result: Any, error: str | None) -> None:
            self._refresh_provider_status()
            if error:
                self._show_error(error)
            else:
                messagebox.showinfo("Forecast contractors", "All contractor workers stopped.", parent=window)
            window.destroy()
            self._show_providers()

        def stop_all() -> None:
            self._background(self._provider_manager.stop_all, stop_complete)

        def test_complete(result: Any, error: str | None) -> None:
            self._refresh_provider_status()
            if error:
                self._show_error(error)
                return
            lines = []
            for row in result:
                item = row.result
                if item.available and item.evidence is not None:
                    evidence = item.evidence
                    lines.append(
                        f"{item.provider_id}: AVAILABLE · p50={evidence.p50:.8g} · "
                        f"method={row.provenance.distribution_method}"
                    )
                else:
                    lines.append(f"{item.provider_id}: UNAVAILABLE · {item.error}")
            messagebox.showinfo(
                "Synthetic contractor check — no MT5 data",
                "\n".join(lines),
                parent=window,
            )
            window.destroy()
            self._show_providers()

        def test_selected() -> None:
            try:
                self._provider_manager.select(selected.get())
            except ValueError as exc:
                messagebox.showerror("Forecast contractors", str(exc), parent=window)
                return
            bars = _smoke_bars()
            future = tuple(
                bars[-1].at + timedelta(minutes=15 * (index + 1))
                for index in range(16)
            )

            def start_and_test() -> Any:
                states = self._provider_manager.start_selected()
                if not any(state.value == "ready" for state in states.values()):
                    rendered = ", ".join(
                        f"{provider_id}={state.value}"
                        for provider_id, state in states.items()
                    )
                    raise RuntimeError(
                        "no_selected_contractor_reached_ready:" + rendered
                    )
                return self._provider_manager.forecast_selected(
                    bars,
                    symbol="EURUSD",
                    timeframe="M15",
                    horizon_steps=16,
                    future_times=future,
                )

            self._refresh_provider_status()
            self._background(start_and_test, test_complete)

        controls = self._ttk.Frame(frame)
        controls.grid(row=7, column=0, columnspan=6, sticky="ew", pady=(14, 0))
        self._ttk.Button(controls, text="Apply selection", command=apply_selection).pack(side="left", padx=(0, 8))
        self._ttk.Button(controls, text="Start selected", command=start_selected).pack(side="left", padx=8)
        self._ttk.Button(controls, text="Test selected (synthetic)", command=test_selected).pack(side="left", padx=8)
        self._ttk.Button(controls, text="Stop all", command=stop_all).pack(side="left", padx=8)
        self._ttk.Button(controls, text="Refresh", command=lambda: (self._refresh_provider_status(), window.destroy(), self._show_providers())).pack(side="right", padx=8)

    def _close(self) -> None:
        from tkinter import messagebox

        if self._busy:
            messagebox.showinfo(
                "Task active",
                "Wait for the current connection, contractor or Codex task to finish before closing.",
            )
            return
        if self._application.runtime_active:
            if not messagebox.askyesno(
                "Cancel research?",
                "Cancel this read-only research worker and close Dusty? MT5 is left running.",
            ):
                return
            self._application.emergency_halt()
        self._provider_manager.stop_all()
        self._closing = True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dusty Dragon M114 multi-provider control panel")
    parser.add_argument("--terminal", action="append", default=[], help="manual terminal.exe/terminal64.exe path")
    parser.add_argument("--catalog", type=Path, help="reviewed non-executable strategy catalog JSON")
    parser.add_argument("--repository", type=Path, default=Path.cwd(), help="Dusty Git repository")
    parser.add_argument("--research-directory", type=Path, help="local artifacts outside the Git repository")
    parser.add_argument("--provider-root", type=Path, help="isolated provider root; defaults to ~/DustyProviders")
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    commit = _current_commit(repository)
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
    providers = ProviderRegistry(args.provider_root)
    DustyProviderUI(
        application,
        CodexCLIReporter(repository),
        code_commit=commit,
        research=research,
        providers=providers,
    ).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
