"""Read-only, bounded MT5 history experiments for the local desktop.

The spawned worker owns its MT5 Python connection. It has no order methods, never
launches the Strategy Tester and never issues ModeProof. Completion means software
simulation completed, not strategy qualification. JSON is data, never executable code.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import importlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable
from uuid import uuid4

from .features import completed_feature_bars_from_mt5
from .investment_lab import LaboratoryConfig, LaboratoryRun, run_laboratory_from_bars
from .local_app import RuntimeActionResult, RuntimeSelection
from .local_terminal import account_identity_fingerprint, _account_summary, _symbol_option
from .markets import InstrumentEconomics
from .mt5worker import MT5Bar, _field
from .reviewed_strategies import resolve_research_package
from .research_capital import ResearchCapitalSummary, capital_summary_from_report
from .research_environment import runtime_provenance
from .research_evaluation import FixedEvaluationPlan, require_m15_utc, run_fixed_evaluation
from .broker_cost_observation import observe_recent_costs
from .strategy_catalog import OperatingMode, QualificationBinding


@dataclass(frozen=True, slots=True)
class ResearchSettings:
    history_days: int = 7
    commission_per_lot: float = 0.0  # round trip, account currency, explicitly unverified
    slippage_points: float = 0.0  # total round-trip adverse price allowance
    spread_floor_points: float = 0.0
    fixed_end: datetime | None = None
    holdout_days: int = 0
    cost_source: str = ""

    def __post_init__(self) -> None:
        if type(self.history_days) is not int or not 1 <= self.history_days <= 30:
            raise ValueError("history_days_must_be_between_1_and_30")
        values = (self.commission_per_lot, self.slippage_points, self.spread_floor_points)
        if any(isinstance(v, bool) or not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("research_costs_must_be_finite_and_nonnegative")
        if self.fixed_end is not None:
            require_m15_utc(self.fixed_end)
        if type(self.holdout_days) is not int or not 0 <= self.holdout_days < self.history_days:
            raise ValueError("holdout_days_must_be_zero_or_less_than_history_days")
        if self.holdout_days and self.fixed_end is None:
            raise ValueError("holdout_requires_a_fixed_UTC_end")
        if not isinstance(self.cost_source, str) or len(self.cost_source) > 400 or (
                self.cost_source and not self.cost_source.isprintable()):
            raise ValueError("cost_source_must_be_one_line_up_to_400_characters")

    def bounds(self, now: datetime) -> tuple[datetime, datetime]:
        if now.utcoffset() != timedelta(0):
            raise ValueError("research_clock_requires_UTC")
        end = self.fixed_end or now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
        if end > now:
            raise ValueError("fixed_end_is_in_the_future_wait_until_window_completes")
        return end - timedelta(days=self.history_days), end

    def evaluation_plan(self, start: datetime, end: datetime) -> FixedEvaluationPlan | None:
        if self.fixed_end is not None and (end != self.fixed_end or start != end - timedelta(days=self.history_days)):
            raise ValueError("research_window_differs_from_fixed_settings")
        return FixedEvaluationPlan(start, end - timedelta(days=self.holdout_days), end) if self.holdout_days else None

    def cost_provenance(self) -> dict[str, object]:
        return {"status": "USER_ASSUMPTIONS_NOT_VERIFIED", "source_note": self.cost_source or "NOT_PROVIDED",
                "commission_round_trip_per_lot": self.commission_per_lot,
                "slippage_round_trip_points": self.slippage_points, "spread_floor_points": self.spread_floor_points,
                "zero_commission_assumed": self.commission_per_lot == 0,
                "zero_slippage_assumed": self.slippage_points == 0,
                "spread_basis": "max(configured_floor,availability_bar_proxy_times_point)",
                "fees_and_swaps_complete": False, "verified_broker_schedule": False}


@dataclass(frozen=True, slots=True)
class ResearchJobView:
    state: str = "IDLE"
    message: str = "Ready for read-only MT5 history research"
    run_directory: str = ""
    capital_summary: ResearchCapitalSummary | None = None
    research_scope: str = ""
    capital_label: str = ""

    @property
    def active(self) -> bool:
        return self.state in ("RUNNING", "CANCELLING")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, allow_nan=False,
                      default=lambda v: v.isoformat() if isinstance(v, datetime) else _unsupported(v))


def _unsupported(value: object) -> None:
    raise TypeError(f"unsupported artifact type: {type(value).__name__}")


def _atomic_json(path: Path, value: Any) -> str:
    """Publish a complete file, on the same filesystem; never overwrite another run."""
    content = _json(value).encode("utf-8")
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    with temporary.open("xb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    return sha256(content).hexdigest()


def repository_matches(repository: Path, commit: str) -> bool:
    """Refuse to label modified code as an exact reviewed commit."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository,
                              capture_output=True, text=True, timeout=10, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repository,
                                capture_output=True, text=True, timeout=10, check=True)
        return head.stdout.strip() == commit and not status.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False


def default_research_directory() -> Path:
    base = Path(os.environ["LOCALAPPDATA"]) if os.environ.get("LOCALAPPDATA") else Path.home() / ".local" / "share"
    return base / "DustyDragon" / "research"


class SelectedTerminalHistoryReader:
    """One bounded read, identity checked before AND after, with no symbol/order writes."""

    def __init__(self, module: Any | None = None) -> None:
        self._module = module
        self.cost_observation: dict[str, object] | None = None

    def read(self, selection: RuntimeSelection, start: datetime, end: datetime) -> tuple[tuple[MT5Bar, ...], InstrumentEconomics]:
        if end <= start or end - start > timedelta(days=30):
            raise ValueError("history_window_not_bounded")
        if start.utcoffset() != timedelta(0) or end.utcoffset() != timedelta(0):
            raise ValueError("history_window_requires_UTC")
        validate_research_selection(selection)
        mt5 = self._module if self._module is not None else importlib.import_module("MetaTrader5")
        installation = selection.terminal.installation
        try:
            if not mt5.initialize(installation.executable_path, portable=installation.portable, timeout=10_000):
                raise ValueError("mt5_initialize_failed_open_selected_terminal_and_retry")
            self._verify(mt5, selection)
            info = mt5.symbol_info(selection.symbol.symbol)
            symbol = selection.symbol
            # The current ledger supports linear account-currency tick economics only.
            # Do not silently apply a present-day FX conversion to historical P&L.
            if symbol.custom or symbol.currency_profit != selection.terminal.account.currency:
                raise ValueError("historical_currency_or_custom_symbol_economics_not_supported")
            if min(symbol.point_size, symbol.tick_size, symbol.tick_value, symbol.contract_size) <= 0:
                raise ValueError("broker_point_tick_or_contract_economics_missing")
            if not math.isclose(symbol.tick_value / symbol.tick_size, symbol.contract_size, rel_tol=1e-6):
                raise ValueError("nonlinear_or_converted_tick_economics_not_supported")
            side = resolve_research_package(selection.strategy).spec.direction.value
            order_type = mt5.ORDER_TYPE_BUY if side == "long" else mt5.ORDER_TYPE_SELL
            price = float(getattr(info, "ask" if side == "long" else "bid", 0.0))
            if not math.isfinite(price) or price <= 0:
                raise ValueError("broker_margin_reference_price_unavailable")
            # Read-only calculation; not order_check or order_send.
            margin = mt5.order_calc_margin(order_type, symbol.symbol, symbol.volume_min, price)
            if margin is None or not math.isfinite(margin) or margin <= 0:
                raise ValueError("broker_margin_calculation_unavailable")
            economics = InstrumentEconomics(
                symbol.contract_size, symbol.tick_size, symbol.tick_value,
                symbol.volume_min, symbol.volume_step, symbol.volume_max,
                margin_rate=margin / (price * symbol.contract_size * symbol.volume_min),
                point_size=symbol.point_size,
                stop_level_points=float(getattr(info, "trade_stops_level", 0.0)),
                freeze_level_points=float(getattr(info, "trade_freeze_level", 0.0)),
            )
            rows = mt5.copy_rates_range(symbol.symbol, mt5.TIMEFRAME_M15, start, end)
            if rows is None:
                raise ValueError("mt5_history_unavailable_open_symbol_M15_chart_and_retry")
            if not 50 <= len(rows) <= 3000:
                raise ValueError("mt5_history_requires_50_to_3000_bars_check_chart_history")
            bars = tuple(MT5Bar(
                datetime.fromtimestamp(int(_field(row, "time", 0)), timezone.utc),
                *(float(_field(row, name, index)) for index, name in enumerate(("open", "high", "low", "close"), 1)),
                *(int(_field(row, name, index)) for index, name in enumerate(("tick_volume", "spread", "real_volume"), 5)),
            ) for row in rows)
            validate_history(bars, start, end)
            self.cost_observation = observe_recent_costs(mt5, symbol.symbol, datetime.now(timezone.utc))
            self._verify(mt5, selection)
            return bars, economics
        finally:
            # shutdown disconnects THIS Python bridge; it does not close the terminal.
            mt5.shutdown()

    @staticmethod
    def _verify(mt5: Any, selection: RuntimeSelection) -> None:
        terminal, account = mt5.terminal_info(), mt5.account_info()
        expected = selection.terminal
        if terminal is None or account is None or not getattr(terminal, "connected", False):
            raise ValueError("selected_terminal_disconnected")
        actual_path = getattr(terminal, "path", "")
        normalize = lambda p: os.path.normcase(os.path.abspath(p))
        if not actual_path or normalize(actual_path) != normalize(str(Path(expected.installation.executable_path).parent)):
            raise ValueError("selected_terminal_path_changed")
        if (not expected.data_path or normalize(getattr(terminal, "data_path", "")) != normalize(expected.data_path)
                or str(getattr(terminal, "build", "")) != expected.terminal_build):
            raise ValueError("selected_terminal_environment_changed_reconnect")
        fingerprint = account_identity_fingerprint(account)
        summary = _account_summary(account, mt5)
        if (not expected.account.identity_fingerprint or fingerprint != expected.account.identity_fingerprint
                or summary.mode != expected.account.mode or summary.currency != expected.account.currency):
            raise ValueError("selected_account_changed_reconnect")
        info = mt5.symbol_info(selection.symbol.symbol)
        if info is None or _symbol_option(info) != selection.symbol:
            raise ValueError("selected_symbol_specification_changed_reconnect")


def validate_history(bars: tuple[MT5Bar, ...], start: datetime, end: datetime) -> None:
    if not 50 <= len(bars) <= 3000:
        raise ValueError("history_requires_50_to_3000_bars")
    previous = None
    for bar in bars:
        if not start <= bar.at <= end or (previous is not None and bar.at <= previous):
            raise ValueError("history_outside_window_duplicate_or_out_of_order")
        if bar.at.minute % 15 or bar.at.second or bar.at.microsecond:
            raise ValueError("history_not_M15_aligned")
        prices = (bar.open, bar.high, bar.low, bar.close)
        if any(not math.isfinite(v) or v <= 0 for v in prices) or not bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high:
            raise ValueError("history_invalid_OHLC")
        if min(bar.tick_volume, bar.spread, bar.real_volume) < 0:
            raise ValueError("history_negative_volume_or_spread")
        previous = bar.at


def validate_research_selection(selection: RuntimeSelection) -> None:
    resolve_research_package(selection.strategy)
    terminal = selection.terminal
    expected = QualificationBinding(terminal.installation.identity_key, terminal.account.server,
                                    terminal.account.mode, selection.symbol.symbol,
                                    selection.strategy.strategy_hash, selection.binding.code_commit)
    if selection.mode is not OperatingMode.BACKTEST or selection.binding != expected:
        raise ValueError("research_selection_binding_mismatch_or_unsupported_mode")
    if not terminal.connected or selection.symbol not in terminal.symbols:
        raise ValueError("research_selection_not_in_connected_terminal_inventory")


def _request_payload(selection: RuntimeSelection, settings: ResearchSettings, start: datetime, end: datetime) -> dict[str, Any]:
    package = resolve_research_package(selection.strategy)
    terminal = selection.terminal
    plan = settings.evaluation_plan(start, end)
    return {
        "schema": 3, "mode": "READ_ONLY_HISTORY_SIMULATION", "promotion_eligible": False,
        "cost_provenance": settings.cost_provenance(),
        "evaluation_plan": plan.payload() if plan else None,
        "evaluation_plan_fingerprint": plan.fingerprint if plan else None,
        "runtime": runtime_provenance(), "research_scope": selection.research_scope,
        "code_commit": selection.binding.code_commit, "binding": selection.binding.fingerprint,
        "package_fingerprint": package.fingerprint, "strategy": asdict(package.spec),
        "feature_config": asdict(package.features), "cognition_policy": asdict(package.cognition),
        "symbol": asdict(selection.symbol), "timeframe": "M15", "start": start, "end": end,
        "settings": asdict(settings), "snapshot_at": terminal.captured_at,
        "account_fingerprint": terminal.account.identity_fingerprint,
        "account_currency": terminal.account.currency, "account_mode": terminal.account.mode,
        "terminal_identity_hash": sha256(terminal.installation.identity_key.encode()).hexdigest(),
        "data_path_hash": sha256(terminal.data_path.encode()).hexdigest(),
        "terminal_build": terminal.terminal_build,
        "growth_starting_balance": terminal.account.balance,
        "minimum_lot_test_equity": 100_000.0, "growth_risk_fraction": 0.0025,
    }


def execute_research(selection: RuntimeSelection, settings: ResearchSettings, directory: Path,
                     start: datetime, end: datetime, *, reader: SelectedTerminalHistoryReader | None = None) -> dict[str, Any]:
    """Testable engine body. Caller provides a new run directory and frozen request."""
    validate_research_selection(selection)
    package = resolve_research_package(selection.strategy)
    plan = settings.evaluation_plan(start, end)
    # Validate the caller's declared plan against the ex-ante, hashed request before acquisition.
    if settings.fixed_end is not None:
        settings.bounds(datetime.now(timezone.utc))
    frozen = json.loads((directory / "request.json").read_text(encoding="utf-8"))
    expected = json.loads(_json(_request_payload(selection, settings, start, end)))
    if frozen != expected:
        raise ValueError("frozen_request_does_not_match_worker_configuration")
    history_reader = reader or SelectedTerminalHistoryReader()
    bars, economics = history_reader.read(selection, start, end)
    validate_history(bars, start, end)
    hashes = {"bars.json": _atomic_json(directory / "bars.json", [asdict(bar) for bar in bars])}
    config = LaboratoryConfig(
        feature_config=package.features, cognition_policy=package.cognition,
        growth_starting_equity=selection.terminal.account.balance,
        commission_per_lot=settings.commission_per_lot,
        spread_price=settings.spread_floor_points * economics.point_size,
        expected_slippage_price=settings.slippage_points * economics.point_size,
    )
    completed = completed_feature_bars_from_mt5(bars)
    evaluation = None
    if plan is not None:
        run, evaluation = run_fixed_evaluation(package.compiled, completed, symbol=selection.symbol.symbol,
                                              economics=economics, config=config, plan=plan)
    else:
        run = run_laboratory_from_bars(package.compiled, completed, symbol=selection.symbol.symbol,
                                      economics=economics, config=config)
    report = {"schema": 3, "runtime": runtime_provenance(),
              "cost_provenance": settings.cost_provenance(),
              "broker_cost_observation": getattr(history_reader, "cost_observation", None),
              "evaluation": evaluation,
              "economics": asdict(economics), "config": asdict(config), "laboratory": asdict(run)}
    capital = capital_summary_from_report(report, currency=selection.terminal.account.currency,
                                         symbol=selection.symbol.symbol)
    report["capital_summary"] = asdict(capital)
    hashes["report.json"] = _atomic_json(directory / "report.json", report)
    # Native manifests remain proposals inside the report, never native execution evidence.
    reasons: dict[str, int] = {}
    for trace in run.cognition:
        decision = trace.decision.value
        reasons[decision] = reasons.get(decision, 0) + 1
    summary = _summary(run, selection, bars, reasons, capital)
    cost = report["broker_cost_observation"]
    cost_status = cost["status"] if cost else "NO_NATIVE_COST_OBSERVATION"
    cost_text = (f"Cost inputs: USER ASSUMPTIONS — NOT VERIFIED. Source note: {settings.cost_source or 'not provided'}\n"
                 f"Recent broker cost observation: {cost_status}; "
                 f"execution deals: {cost['execution_deals'] if cost else 0}. "
                 "Observed costs never replace the frozen simulation inputs.\n")
    if evaluation is not None:
        parts = evaluation["segments"]
        summary = ("HISTORICAL HOLDOUT COMPLETED — NOT CERTIFIED\n"
                   "Prior exposure UNKNOWN: this is not proof of untouched out-of-sample data.\n"
                   f"Fixed UTC acquisition: {start.isoformat()} to {end.isoformat()}\n"
                   f"Holdout starts: {plan.holdout_start.isoformat()} (availability time)\n"
                   + "\n".join(f"{name.title()}: {part['observed_bars']} observed bars; "
                       f"minimum-lot P&L {part['minimum_lot_net_pnl']:.2f}; growth {part['growth_trades']} trades, "
                       f"P&L {part['growth_net_pnl']:.2f} {selection.terminal.account.currency}; "
                       f"drawdown {part['growth_drawdown']:.2%}." for name, part in parts.items())
                   + "\nIndependent flat-start simulations; no capital/position carryover or parameter tuning. "
                   f"The last {package.spec.exit_plan.max_hold_steps} observations of each segment prohibit new entries.\n"
                   + cost_text + "\nHOLDOUT DETAILS (preferred balance applies to holdout only):\n" + summary)
    else:
        summary += "\n\n" + cost_text
    return {"state": "COMPLETED", "message": summary, "promotion_eligible": False,
            "request_sha256": sha256((directory / "request.json").read_bytes()).hexdigest(),
            "artifact_sha256": hashes, "completed_at": datetime.now(timezone.utc)}


def _summary(run: LaboratoryRun, selection: RuntimeSelection, bars: tuple[MT5Bar, ...], decisions: dict[str, int],
             capital: ResearchCapitalSummary) -> str:
    minimum, growth = run.minimum_lot_backtest, run.growth_backtest
    gap_count = sum(right.at - left.at > timedelta(minutes=15) for left, right in zip(bars, bars[1:]))
    return (
        f"RESEARCH COMPLETED — NOT CERTIFIED\n{selection.symbol.symbol} / M15 / {selection.strategy.title}\n"
        f"Actual history: {bars[0].at.isoformat()} to {bars[-1].at.isoformat()}\n"
        f"{len(bars)} source bars; {len(bars)-1} confirmed in acquisition; "
        f"{run.bar_count} analyzed in this stage; {gap_count} acquisition gaps (not filled).\n"
        f"Minimum-lot simulation: {minimum.trade_count} trades, P&L {minimum.net_pnl:.2f}\n"
        f"Growth simulation: {growth.trade_count} trades, P&L {growth.net_pnl:.2f} "
        f"{selection.terminal.account.currency}, max marked drawdown {growth.max_drawdown_fraction:.2%}\n"
        f"Hypothetical growth starting balance: {growth.starting_equity:.2f} from the connection snapshot; "
        "not a current portfolio allocation.\n"
        f"Broker minimum lot: {capital.minimum_lot:g}; snapshot checked {selection.terminal.captured_at.isoformat()}\n"
        f"{capital.display()}\nGrowth rejection counts: {dict(capital.rejection_counts)}\n"
        "Sizing estimate = minimum-lot planned loss / effective requested risk for each sized setup. "
        "It includes the recorded stop, spread proxy and assumed commission/slippage, but excludes "
        "margin constraints, other positions and unmodeled costs/gap losses. It is NOT a rerun "
        "at that balance, a capital target, or a change to risk limits.\n"
        f"Cognition decisions: {decisions}\nSpread basis: {', '.join(run.spread_cost_bases) or 'no entries'}\n\n"
        "LIMITATIONS: same-window research, no out-of-sample claim. Seed hypotheses are not online-"
        "discovered strategies or trained forecasts. Current symbol economics/margin are historical proxies. "
        "Commission/slippage are user assumptions; swaps, fees, historical broker changes and intrabar "
        "tick paths are not fully modeled. Gaps can be closures or missing history; no coverage claim. "
        "No native indicator/tester parity. A positive P&L does not unlock Demo or Live. "
        "No orders sent or positions managed."
    )


def _research_worker(selection: RuntimeSelection, settings: ResearchSettings, directory: Path,
                     start: datetime, end: datetime, repository: Path) -> None:
    try:
        if not repository_matches(repository, selection.binding.code_commit):
            raise ValueError("repository_changed_restart_required")
        result = execute_research(selection, settings, directory, start, end)
        if not repository_matches(repository, selection.binding.code_commit):
            raise ValueError("repository_changed_during_research")
    except Exception as exc:
        # Do not persist raw vendor exception strings (may contain login/path details).
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        result = {"state": "FAILED", "message": message, "promotion_eligible": False}
    _atomic_json(directory / "result.json", result)


class LocalResearchRuntime:
    """Single-flight process coordinator. Cancel only our worker, never MT5 itself.

    No implicit resume: an orphaned request without a valid completed result is not a
    successful run. The next Start always creates a new UUID, retaining old evidence.
    """
    configured = True

    def __init__(self, repository: Path, *, output_directory: Path | None = None,
                 settings: ResearchSettings = ResearchSettings(), timeout_seconds: float = 180,
                 context: Any = None, code_checker: Callable[[Path, str], bool] = repository_matches) -> None:
        if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 600:
            raise ValueError("research_timeout_must_be_between_1_and_600_seconds")
        self.repository = repository.resolve()
        self.output_directory = (output_directory or default_research_directory()).resolve()
        if self.output_directory == self.repository or self.repository in self.output_directory.parents:
            raise ValueError("research_outputs_must_be_outside_repository")
        self.settings = settings
        self.timeout_seconds = timeout_seconds
        self._context = context or multiprocessing.get_context("spawn")
        self._code_checker = code_checker
        self._process: Any = None
        self._view = ResearchJobView()
        self._started = 0.0
        self._cancel_reason = ""
        self._request_hash = ""
        self._research_scope = ""

    @property
    def active(self) -> bool:
        return self.poll().active

    def supports(self, selection: RuntimeSelection) -> bool:
        try:
            validate_research_selection(selection)
            return True
        except ValueError:
            return False

    def start(self, selection: RuntimeSelection) -> RuntimeActionResult:
        if self.active:
            return RuntimeActionResult(False, "research_already_active")
        if not self.supports(selection):
            return RuntimeActionResult(False, "strategy_has_no_reviewed_backtest_package")
        if not selection.terminal.account.identity_fingerprint or selection.terminal.account.balance <= 0:
            return RuntimeActionResult(False, "reconnect_required_for_account_identity_and_positive_balance")
        if not self._code_checker(self.repository, selection.binding.code_commit):
            return RuntimeActionResult(False, "repository_dirty_or_changed_restart_required")
        now = datetime.now(timezone.utc)
        try:
            start, end = self.settings.bounds(now)
        except ValueError as exc:
            return RuntimeActionResult(False, str(exc))
        directory = self.output_directory / uuid4().hex
        try:
            directory.mkdir(parents=True, exist_ok=False)
            self._request_hash = _atomic_json(directory / "request.json", _request_payload(selection, self.settings, start, end))
            self._research_scope = selection.research_scope
            process = self._context.Process(target=_research_worker,
                args=(selection, self.settings, directory, start, end, self.repository), daemon=True)
            process.start()
        except Exception as exc:
            self._view = ResearchJobView("FAILED", f"research_start_failed:{type(exc).__name__}", str(directory))
            return RuntimeActionResult(False, self._view.message)
        self._process = process
        self._started = time.monotonic()
        self._cancel_reason = ""
        self._view = ResearchJobView("RUNNING", "Reading MT5 history and running bounded research…", str(directory))
        return RuntimeActionResult(True, self._view.message)

    def poll(self) -> ResearchJobView:
        process = self._process
        if process is None:
            return self._view
        if process.is_alive():
            if time.monotonic() - self._started > self.timeout_seconds and not self._cancel_reason:
                self._cancel("research_timeout")
            return self._view
        process.join(timeout=0)
        exitcode = process.exitcode
        process.close()
        self._process = None
        directory = Path(self._view.run_directory)
        if self._cancel_reason:
            state = "TIMED_OUT" if self._cancel_reason == "research_timeout" else "CANCELLED"
            self._view = ResearchJobView(state, self._cancel_reason, str(directory))
            # A completion racing cancellation is superseded, never reported as a pass.
            _atomic_json(directory / "result.json", {"state": state, "message": self._cancel_reason, "promotion_eligible": False})
            return self._view
        try:
            result = read_research_result(directory, expected_request_hash=self._request_hash)
            if exitcode != 0:
                raise ValueError("research_worker_exited_abnormally")
            capital = None
            if result["state"] == "COMPLETED":
                request = json.loads((directory / "request.json").read_text(encoding="utf-8"))
                report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
                capital = capital_summary_from_report(report, currency=request["account_currency"],
                                                     symbol=request["symbol"]["symbol"])
            label = "HISTORICAL HOLDOUT ONLY" if capital is not None and report.get("evaluation") else ""
            self._view = ResearchJobView(result["state"], result["message"], str(directory), capital, self._research_scope, label)
        except (OSError, ValueError, KeyError, TypeError):
            self._view = ResearchJobView("FAILED", "research_worker_failed_or_artifact_integrity_error", str(directory))
        return self._view

    def _cancel(self, reason: str) -> RuntimeActionResult:
        if self._process is not None and self._process.is_alive():
            self._cancel_reason = reason
            self._view = ResearchJobView("CANCELLING", "Stopping research worker; no broker orders involved", self._view.run_directory)
            self._process.terminate()
            return RuntimeActionResult(True, self._view.message)
        self.poll()
        return RuntimeActionResult(True, "no_research_active_no_broker_orders_managed")

    def stop_new_entries(self) -> RuntimeActionResult:
        return self._cancel("research_cancelled_by_user")

    def emergency_halt(self) -> RuntimeActionResult:
        return self._cancel("research_halted_by_user")


def read_research_result(directory: Path, *, expected_request_hash: str | None = None) -> dict[str, Any]:
    result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
    if result.get("state") not in ("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT") or result.get("promotion_eligible") is not False:
        raise ValueError("invalid_research_result")
    if not isinstance(result.get("message"), str):
        raise ValueError("invalid_research_message")
    if result["state"] == "COMPLETED":
        actual = sha256((directory / "request.json").read_bytes()).hexdigest()
        if actual != result.get("request_sha256") or (expected_request_hash and expected_request_hash != actual):
            raise ValueError("research_request_hash_mismatch")
        hashes = result.get("artifact_sha256", {})
        if set(hashes) != {"bars.json", "report.json"}:
            raise ValueError("research_artifact_set_mismatch")
        for filename, digest in hashes.items():
            if sha256((directory / filename).read_bytes()).hexdigest() != digest:
                raise ValueError("research_artifact_hash_mismatch")
    return result
