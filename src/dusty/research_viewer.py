"""Read-only case browser: presentation rounding never changes saved evidence."""
from hashlib import sha256
import json
from pathlib import Path
import queue
import threading

from .local_research import read_research_result


def load_case_report(directory: Path) -> tuple[list[dict], str]:
    result = read_research_result(directory)
    if result["state"] != "COMPLETED":
        raise ValueError("Cases require a completed, integrity-checked research run.")
    data = (directory / "report.json").read_bytes()
    if sha256(data).hexdigest() != result["artifact_sha256"]["report.json"]:
        raise ValueError("Report changed while opening; close and reopen the viewer.")
    report = json.loads(data)
    request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    compared = report.get("campaign") or report.get("comparison") or {}
    return compared.get("cases", []), request["account_currency"]


def case_label(case: dict) -> str:
    return f"{case['segment']} | {case['candidate_id']} | {case['cost_scenario']}"


def case_overview(case: dict, currency: str) -> str:
    totals = case["diagnosis"]["totals"]["growth"]
    metric = case["metrics"]
    return (f"Simulated growth account — {currency} | {metric['start']} to {metric['end']} (end exclusive)\n"
            f"Gross {totals['gross_pnl']:+,.2f} − modeled costs {totals['total_cost']:,.2f} "
            f"= net {totals['net_pnl']:+,.2f} | Drawdown {metric['growth_drawdown']:.2%}\n"
            f"{totals['trade_count']} trades | {totals['wins']} wins / {totals['losses']} losses | "
            f"{case['diagnosis']['growth_rejections']} rejected entries\n"
            "Research only. Costs unverified. Display rounded; saved evidence retains full precision.")


def trade_values(row: dict) -> tuple[str, ...]:
    cash = row["growth"]
    return (row["trade_id"], row["side"], str(row["entry_at"]), str(row["exit_at"]),
            f"{cash['volume']:g}", f"{cash['gross_pnl']:+,.2f}", f"{cash['total_cost']:,.2f}",
            f"{cash['net_pnl']:+,.2f}", row["exit_reason"])


def trade_detail(row: dict, currency: str) -> str:
    c = row["growth"]
    context = row["entry_context"]
    lines = [f"{row['trade_id']} — {row['side']} — {row['exit_reason']}",
             f"Entry {row['entry_price']:,.5f} | Exit {row['exit_price']:,.5f} | "
             f"Initial stop {row['initial_stop']:,.5f} | Target {row['target']:,.5f}",
             f"Held {row['observed_hold_steps']} observations; {row['elapsed_minutes']:,.0f} elapsed minutes.",
             f"Growth volume {c['volume']:g} lots | Gross {c['gross_pnl']:+,.2f} {currency}",
             f"Costs: spread {c['spread_cost']:,.2f}, slippage {c['slippage_cost']:,.2f}, "
             f"commission {c['commission_cost']:,.2f} | Net {c['net_pnl']:+,.2f} {currency}",
             f"Minimum-lot net {row['minimum_lot']['net_pnl']:+,.2f} {currency} (separate simulation)",
             "\nRecorded entry features:"]
    for name, value in context["features"].items():
        lines.append(f"  {name}: {value:.6g}" if isinstance(value, (int, float)) else f"  {name}: unavailable")
    lines.append("\nRecorded entry rules:")
    for group in context["rule_groups"]:
        for clause in group["clauses"]:
            lines.append(f"  {clause['feature']} {clause['op']} {clause['threshold']:.6g}: "
                         + ("met" if clause["passed"] else "not met"))
    for f in context.get("forecast", []):
        lines.append(f"  Frozen fitted forecast: {(f['point']/f['origin']-1):+.4%} "
                     f"over {f['horizon_steps']} observations (not expected trade P&L).")
    lines += [f"\nEntry policy: {context['entry_policy_reason']}",
              "Growth rejection reasons: " + (", ".join(row["growth_rejection_reasons"]) or "none"),
              f"Cash classification: {row['outcome_label']}",
              "Recorded simulation evidence, not causal proof or permission to trade."]
    return "\n".join(lines)


class CaseExplorer:
    def __init__(self, notebook, directory: str):
        import tkinter as tk
        from tkinter import ttk
        self.frame = ttk.Frame(notebook)
        notebook.add(self.frame, text="Cases & trades")
        self.directory = Path(directory)
        self._events = queue.Queue()
        self._started = False
        self._closed = False
        self._timer = None
        self._cases = []
        self._rows = []
        self._currency = ""
        self.status = tk.StringVar(value="Cases become available when this run completes. No orders.")
        ttk.Label(self.frame, textvariable=self.status, wraplength=950).pack(fill="x", padx=8, pady=6)
        self.selector = ttk.Combobox(self.frame, state="disabled", width=85)
        self.selector.pack(fill="x", padx=8, pady=6)
        self.selector.bind("<<ComboboxSelected>>", self._select_case)
        self.overview = tk.Text(self.frame, height=5, wrap="word", state="disabled")
        self.overview.pack(fill="x", padx=8)
        panes = ttk.Panedwindow(self.frame, orient="vertical")
        panes.pack(fill="both", expand=True, padx=8, pady=8)
        table = ttk.Frame(panes)
        panes.add(table, weight=2)
        columns = ("ID", "Side", "Entry UTC", "Exit UTC", "Lots", "Gross", "Costs", "Net", "Exit reason")
        self.tree = ttk.Treeview(table, columns=columns, show="headings", height=9, selectmode="browse")
        for column in columns:
            self.tree.heading(column, text=column)
            self.tree.column(column, width=150 if "UTC" in column else 95, minwidth=65, stretch=False)
        ys = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(table, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        ys.pack(side="right", fill="y")
        xs.pack(side="bottom", fill="x")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._select_trade)
        detail_frame = ttk.Frame(panes)
        panes.add(detail_frame, weight=2)
        self.details = tk.Text(detail_frame, height=12, wrap="word", state="disabled")
        scroll = ttk.Scrollbar(detail_frame, orient="vertical", command=self.details.yview)
        self.details.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.details.pack(fill="both", expand=True)
        self.frame.bind("<Destroy>", self._destroy, add=True)

    @staticmethod
    def _text(widget, value):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    def load(self):
        if self._started or self._closed:
            return
        self._started = True
        self.status.set("Checking saved evidence…")

        def worker():
            try:
                self._events.put((load_case_report(self.directory), None))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self._events.put((None, str(exc)))

        threading.Thread(target=worker, daemon=True).start()
        self._timer = self.frame.after(100, self._poll)

    def _poll(self):
        self._timer = None
        if self._closed:
            return
        try:
            result, error = self._events.get_nowait()
        except queue.Empty:
            self._timer = self.frame.after(100, self._poll)
            return
        if error:
            self.status.set(f"Cannot open cases: {error}")
            return
        self.set_report(*result)

    def set_report(self, cases, currency):
        self._cases, self._currency = cases, currency
        self.selector.configure(values=[case_label(c) for c in cases], state="readonly" if cases else "disabled")
        self.status.set("Choose a case, then a trade below. No automatic winner." if cases else
                        "This run has no comparison/campaign cases. See Summary.")
        if cases:
            self.selector.current(0)
            self._select_case()

    def _select_case(self, _event=None):
        index = self.selector.current()
        if not 0 <= index < len(self._cases):
            return
        case = self._cases[index]
        self._text(self.overview, case_overview(case, self._currency))
        self._rows = case["diagnosis"]["rows"]
        for item in self.tree.get_children():
            self.tree.delete(item)
        for i, row in enumerate(self._rows):
            self.tree.insert("", "end", iid=str(i), values=trade_values(row))
        self._text(self.details, "Select a trade to inspect its entry evidence." if self._rows else
                   "No trades in this case. A zero-trade result is retained; it is not a qualified strategy.")

    def _select_trade(self, _event=None):
        selected = self.tree.selection()
        if selected and 0 <= int(selected[0]) < len(self._rows):
            self._text(self.details, trade_detail(self._rows[int(selected[0])], self._currency))

    def _destroy(self, event):
        if event.widget is self.frame:
            self._closed = True
            if self._timer is not None:
                self.frame.after_cancel(self._timer)
                self._timer = None
