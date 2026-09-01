from __future__ import annotations

import importlib
import json
import math
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .demo_session import AccountMode


_TERMINAL_NAMES = {"terminal.exe", "terminal64.exe"}


class TerminalDiscoverySource(StrEnum):
    RUNNING_PROCESS = "running_process"
    REGISTRY = "registry"
    STANDARD_LOCATION = "standard_location"
    MANUAL = "manual"


@dataclass(frozen=True, slots=True)
class RunningTerminalProcess:
    process_id: int
    executable_path: str
    portable: bool = False

    def __post_init__(self) -> None:
        if self.process_id <= 0 or not self.executable_path.strip():
            raise ValueError("running terminal process identity is incomplete")


@dataclass(frozen=True, slots=True)
class TerminalInstallation:
    executable_path: str
    sources: tuple[TerminalDiscoverySource, ...]
    running_process_ids: tuple[int, ...] = ()
    portable: bool = False

    def __post_init__(self) -> None:
        path = Path(self.executable_path)
        if path.name.casefold() not in _TERMINAL_NAMES:
            raise ValueError("MT5 terminal executable must be terminal.exe or terminal64.exe")
        if not self.sources or len(set(self.sources)) != len(self.sources):
            raise ValueError("terminal discovery sources must be nonempty and unique")
        if any(pid <= 0 for pid in self.running_process_ids):
            raise ValueError("terminal process identifiers must be positive")

    @property
    def identity_key(self) -> str:
        return os.path.normcase(os.path.abspath(self.executable_path))

    @property
    def display_name(self) -> str:
        state = "running" if self.running_process_ids else "installed"
        short_identity = sha256(self.identity_key.encode("utf-8")).hexdigest()[:8]
        return f"{Path(self.executable_path).parent.name} — {state} — {short_identity}"


ProcessReader = Callable[[], Iterable[RunningTerminalProcess]]
RegistryReader = Callable[[], Iterable[str]]


class WindowsMT5Discovery:
    """Bounded read-only discovery of local MetaTrader terminal executables.

    Discovery is inventory only. It never logs in, launches a terminal, assigns an account,
    or grants trading authority. The selected executable must still be explicitly confirmed
    and probed by the application controller.
    """

    def __init__(
        self,
        *,
        search_roots: Iterable[str | Path] | None = None,
        manual_paths: Iterable[str | Path] = (),
        process_reader: ProcessReader | None = None,
        registry_reader: RegistryReader | None = None,
        environ: Mapping[str, str] | None = None,
        platform_name: str | None = None,
        max_depth: int = 4,
        max_terminals: int = 128,
    ) -> None:
        if max_depth < 0 or max_terminals < 1:
            raise ValueError("terminal discovery bounds are invalid")
        self._environ = dict(os.environ if environ is None else environ)
        self._platform_name = os.name if platform_name is None else platform_name
        self._search_roots = tuple(Path(item) for item in search_roots) if search_roots is not None else None
        self._manual_paths = tuple(Path(item) for item in manual_paths)
        self._process_reader = process_reader or _read_windows_terminal_processes
        self._registry_reader = registry_reader or _read_windows_terminal_registry
        self._max_depth = max_depth
        self._max_terminals = max_terminals

    def discover(self) -> tuple[TerminalInstallation, ...]:
        candidates: dict[str, dict[str, object]] = {}

        def record(
            raw_path: str | Path,
            source: TerminalDiscoverySource,
            *,
            process_id: int | None = None,
            portable: bool = False,
        ) -> None:
            path = _candidate_executable(Path(raw_path))
            if path is None or not path.is_file():
                return
            normalized = os.path.normcase(os.path.abspath(str(path)))
            if normalized not in candidates and len(candidates) >= self._max_terminals:
                return
            row = candidates.setdefault(
                normalized,
                {"path": str(path.resolve()), "sources": set(), "pids": set(), "portable": False},
            )
            cast_sources = row["sources"]
            cast_pids = row["pids"]
            assert isinstance(cast_sources, set) and isinstance(cast_pids, set)
            cast_sources.add(source)
            if process_id is not None:
                cast_pids.add(process_id)
            row["portable"] = bool(row["portable"]) or portable

        for path in self._manual_paths:
            record(path, TerminalDiscoverySource.MANUAL)

        if self._platform_name == "nt" or self._process_reader is not _read_windows_terminal_processes:
            try:
                for process in self._process_reader():
                    record(
                        process.executable_path,
                        TerminalDiscoverySource.RUNNING_PROCESS,
                        process_id=process.process_id,
                        portable=process.portable,
                    )
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
                # A failed process inventory cannot erase filesystem discoveries.
                pass

        if self._platform_name == "nt" or self._registry_reader is not _read_windows_terminal_registry:
            try:
                for path in self._registry_reader():
                    record(path, TerminalDiscoverySource.REGISTRY)
            except (OSError, RuntimeError, ValueError):
                pass

        roots = self._search_roots if self._search_roots is not None else self._default_roots()
        for root in roots:
            for path in _bounded_terminal_scan(root, max_depth=self._max_depth, limit=self._max_terminals):
                record(path, TerminalDiscoverySource.STANDARD_LOCATION)
                if len(candidates) >= self._max_terminals:
                    break
            if len(candidates) >= self._max_terminals:
                break

        results = []
        for row in candidates.values():
            sources = tuple(sorted(row["sources"], key=lambda item: item.value))
            pids = tuple(sorted(row["pids"]))
            results.append(TerminalInstallation(str(row["path"]), sources, pids, bool(row["portable"])))
        return tuple(sorted(results, key=lambda item: item.identity_key))

    def _default_roots(self) -> tuple[Path, ...]:
        if self._platform_name != "nt":
            return ()
        values = (
            self._environ.get("ProgramFiles"),
            self._environ.get("ProgramFiles(x86)"),
            str(Path(self._environ["LOCALAPPDATA"]) / "Programs")
            if self._environ.get("LOCALAPPDATA")
            else None,
        )
        unique: dict[str, Path] = {}
        for value in values:
            if value:
                path = Path(value)
                unique[os.path.normcase(os.path.abspath(str(path)))] = path
        return tuple(unique.values())


@dataclass(frozen=True, slots=True)
class AccountSummary:
    server: str
    company: str
    login_hint: str
    mode: AccountMode
    currency: str
    leverage: float
    balance: float
    equity: float
    profit: float
    margin: float
    margin_free: float
    trade_allowed: bool
    expert_trading_allowed: bool

    def __post_init__(self) -> None:
        if not self.server.strip() or not self.currency.strip() or not self.login_hint.strip():
            raise ValueError("account summary identity is incomplete")
        values = (self.leverage, self.balance, self.equity, self.profit, self.margin, self.margin_free)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("account summary values must be finite")
        if self.leverage <= 0 or self.margin < 0:
            raise ValueError("account summary contains invalid leverage or margin")


@dataclass(frozen=True, slots=True)
class BrokerSymbolOption:
    symbol: str
    category: str
    description: str
    currency_base: str
    currency_profit: str
    digits: int
    point_size: float
    tick_size: float
    tick_value: float
    contract_size: float
    volume_min: float
    volume_step: float
    volume_max: float
    trade_mode: int
    visible: bool
    custom: bool

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("broker symbol name is required")
        values = (
            self.point_size,
            self.tick_size,
            self.tick_value,
            self.contract_size,
            self.volume_min,
            self.volume_step,
            self.volume_max,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("broker symbol economics must be finite and nonnegative")
        if self.digits < 0 or self.volume_max + 1e-12 < self.volume_min:
            raise ValueError("broker symbol precision or volume range is invalid")


@dataclass(frozen=True, slots=True)
class TerminalSnapshot:
    installation: TerminalInstallation
    terminal_build: str
    terminal_version: str
    connected: bool
    data_path: str
    account: AccountSummary
    symbols: tuple[BrokerSymbolOption, ...]
    open_positions: int
    active_orders: int
    recent_deals: int
    captured_at: datetime
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.terminal_build.strip() or not self.terminal_version.strip():
            raise ValueError("terminal build and version are required")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("terminal snapshot timestamp must be timezone-aware")
        if min(self.open_positions, self.active_orders, self.recent_deals) < 0:
            raise ValueError("terminal state counts cannot be negative")
        if len({item.symbol.casefold() for item in self.symbols}) != len(self.symbols):
            raise ValueError("broker symbol inventory must be unique")


class ReadOnlyTerminalSnapshotReader:
    """Read account, symbol and broker state without exposing a write method."""

    def __init__(self, module: Any | None = None, *, max_symbols: int = 5_000) -> None:
        if max_symbols < 1:
            raise ValueError("max_symbols must be positive")
        self._module = module
        self._max_symbols = max_symbols

    @property
    def broker_write_authorized(self) -> bool:
        return False

    def _mt5(self) -> Any:
        if self._module is None:
            self._module = importlib.import_module("MetaTrader5")
        return self._module

    def read(self, installation: TerminalInstallation, *, history_days: int = 30) -> TerminalSnapshot:
        if history_days < 1 or history_days > 3660:
            raise ValueError("history_days must be in [1,3660]")
        mt5 = self._mt5()
        if not mt5.initialize(installation.executable_path, portable=installation.portable):
            error = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
            raise RuntimeError(f"MT5 initialize failed: {error}")
        try:
            terminal = mt5.terminal_info()
            account = mt5.account_info()
            raw_symbols = mt5.symbols_get()
            if terminal is None or account is None or raw_symbols is None:
                error = mt5.last_error() if hasattr(mt5, "last_error") else "unknown"
                raise RuntimeError(f"MT5 terminal/account/symbol inventory unavailable: {error}")
            warnings: list[str] = []
            symbols = tuple(_symbol_option(row) for row in tuple(raw_symbols)[: self._max_symbols])
            if len(raw_symbols) > self._max_symbols:
                warnings.append(f"symbol_inventory_truncated:{self._max_symbols}")
            now = datetime.now(timezone.utc)
            positions = _bounded_sequence_read(mt5, "positions_get", warnings)
            orders = _bounded_sequence_read(mt5, "orders_get", warnings)
            deals = _bounded_sequence_read(
                mt5,
                "history_deals_get",
                warnings,
                now - timedelta(days=history_days),
                now,
            )
            version = mt5.version() if hasattr(mt5, "version") else None
            build = _attr(terminal, "build", None)
            if build is None and version and len(version) > 1:
                build = version[1]
            return TerminalSnapshot(
                installation=installation,
                terminal_build=str(build if build is not None else "unknown"),
                terminal_version=_render_version(version),
                connected=bool(_attr(terminal, "connected", False)),
                data_path=str(_attr(terminal, "data_path", "") or ""),
                account=_account_summary(account, mt5),
                symbols=tuple(sorted(symbols, key=lambda item: (item.category.casefold(), item.symbol.casefold()))),
                open_positions=len(positions),
                active_orders=len(orders),
                recent_deals=len(deals),
                captured_at=now,
                warnings=tuple(warnings),
            )
        finally:
            mt5.shutdown()


def _bounded_terminal_scan(
    root: Path,
    *,
    max_depth: int,
    limit: int,
    maximum_directories: int = 5_000,
) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    found: list[Path] = []
    stack = [(root, 0)]
    visited = 0
    while stack and len(found) < limit and visited < maximum_directories:
        current, depth = stack.pop()
        visited += 1
        try:
            entries = tuple(os.scandir(current))
        except (OSError, PermissionError):
            continue
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False) and entry.name.casefold() in _TERMINAL_NAMES:
                    found.append(Path(entry.path))
                    if len(found) >= limit:
                        break
                elif depth < max_depth and entry.is_dir(follow_symlinks=False):
                    stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return tuple(found)


def _candidate_executable(path: Path) -> Path | None:
    if path.name.casefold() in _TERMINAL_NAMES:
        return path
    if path.is_dir():
        for name in ("terminal64.exe", "terminal.exe"):
            candidate = path / name
            if candidate.is_file():
                return candidate
    return None


def _read_windows_terminal_processes() -> tuple[RunningTerminalProcess, ...]:
    if os.name != "nt":
        return ()
    script = (
        "$ErrorActionPreference='Stop';"
        "@(Get-CimInstance Win32_Process | Where-Object {"
        "$_.Name -ieq 'terminal64.exe' -or $_.Name -ieq 'terminal.exe'"
        "} | Select-Object ProcessId,ExecutablePath,CommandLine) | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return ()
    payload = json.loads(completed.stdout)
    rows = payload if isinstance(payload, list) else [payload]
    results = []
    for row in rows:
        path = str(row.get("ExecutablePath") or "")
        process_id = int(row.get("ProcessId") or 0)
        command_line = str(row.get("CommandLine") or "")
        if path and process_id > 0:
            results.append(RunningTerminalProcess(process_id, path, "/portable" in command_line.casefold()))
    return tuple(results)


def _read_windows_terminal_registry() -> tuple[str, ...]:
    if os.name != "nt":
        return ()
    import winreg  # type: ignore[import-not-found]

    results: set[str] = set()
    roots = (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE)
    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for root in roots:
        for view in views:
            try:
                with winreg.OpenKey(root, uninstall, 0, winreg.KEY_READ | view) as parent:
                    index = 0
                    while True:
                        try:
                            subkey_name = winreg.EnumKey(parent, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(parent, subkey_name) as subkey:
                                display_name = str(_registry_value(winreg, subkey, "DisplayName") or "")
                                install_location = str(_registry_value(winreg, subkey, "InstallLocation") or "")
                                display_icon = str(_registry_value(winreg, subkey, "DisplayIcon") or "")
                        except OSError:
                            continue
                        if "metatrader 5" not in display_name.casefold():
                            continue
                        for value in (install_location, display_icon.split(",", 1)[0].strip('"')):
                            if value:
                                candidate = _candidate_executable(Path(value))
                                if candidate is not None:
                                    results.add(str(candidate))
            except OSError:
                continue
    return tuple(sorted(results, key=str.casefold))


def _registry_value(module: Any, key: Any, name: str) -> object | None:
    try:
        return module.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if hasattr(value, name):
        return getattr(value, name)
    if isinstance(value, Mapping):
        return value.get(name, default)
    return default


def _mode(raw: object, module: Any) -> AccountMode:
    if raw == getattr(module, "ACCOUNT_TRADE_MODE_DEMO", 0):
        return AccountMode.DEMO
    if raw == getattr(module, "ACCOUNT_TRADE_MODE_CONTEST", 1):
        return AccountMode.CONTEST
    if raw == getattr(module, "ACCOUNT_TRADE_MODE_REAL", 2):
        return AccountMode.REAL
    return AccountMode.UNKNOWN


def _login_hint(raw: object) -> str:
    text = str(raw or "")
    return f"••••{text[-4:]}" if text else "unknown"


def _account_summary(row: Any, module: Any) -> AccountSummary:
    return AccountSummary(
        server=str(_attr(row, "server", "") or "unknown"),
        company=str(_attr(row, "company", "") or ""),
        login_hint=_login_hint(_attr(row, "login", "")),
        mode=_mode(_attr(row, "trade_mode", None), module),
        currency=str(_attr(row, "currency", "") or "unknown"),
        leverage=float(_attr(row, "leverage", 0.0) or 0.0),
        balance=float(_attr(row, "balance", 0.0) or 0.0),
        equity=float(_attr(row, "equity", 0.0) or 0.0),
        profit=float(_attr(row, "profit", 0.0) or 0.0),
        margin=float(_attr(row, "margin", 0.0) or 0.0),
        margin_free=float(_attr(row, "margin_free", 0.0) or 0.0),
        trade_allowed=bool(_attr(row, "trade_allowed", False)),
        expert_trading_allowed=bool(_attr(row, "trade_expert", False)),
    )


def _symbol_option(row: Any) -> BrokerSymbolOption:
    point = float(_attr(row, "point", 0.0) or 0.0)
    return BrokerSymbolOption(
        symbol=str(_attr(row, "name", "") or ""),
        category=str(_attr(row, "path", "") or "Broker symbols"),
        description=str(_attr(row, "description", "") or ""),
        currency_base=str(_attr(row, "currency_base", "") or ""),
        currency_profit=str(_attr(row, "currency_profit", "") or ""),
        digits=int(_attr(row, "digits", 0) or 0),
        point_size=point,
        tick_size=float(_attr(row, "trade_tick_size", 0.0) or 0.0),
        tick_value=float(_attr(row, "trade_tick_value", 0.0) or 0.0),
        contract_size=float(_attr(row, "trade_contract_size", 0.0) or 0.0),
        volume_min=float(_attr(row, "volume_min", 0.0) or 0.0),
        volume_step=float(_attr(row, "volume_step", 0.0) or 0.0),
        volume_max=float(_attr(row, "volume_max", 0.0) or 0.0),
        trade_mode=int(_attr(row, "trade_mode", -1) or 0),
        visible=bool(_attr(row, "visible", False)),
        custom=bool(_attr(row, "custom", False)),
    )


def _bounded_sequence_read(module: Any, name: str, warnings: list[str], *args: object) -> Sequence[object]:
    if not hasattr(module, name):
        warnings.append(f"mt5_read_unsupported:{name}")
        return ()
    result = getattr(module, name)(*args)
    if result is None:
        warnings.append(f"mt5_read_failed:{name}")
        return ()
    return tuple(result)


def _render_version(version: object) -> str:
    if isinstance(version, (tuple, list)):
        return ".".join(str(item) for item in version)
    return str(version if version is not None else "unknown")
