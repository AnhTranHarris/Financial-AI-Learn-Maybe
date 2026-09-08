from __future__ import annotations

"""M194.1 read-only native Demo preflight.

This module proves that the exact MT5 terminal/account boundary is suitable for
starting a real M194 Demo evidence run. It never calls ``order_send``, changes
terminal settings, logs into another account, starts a strategy, or grants
broker-write authority.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from .demo_session import AccountMode


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: object, label: str, *, maximum: int = 512) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _git_sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires a full Git SHA")
    return rendered


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if hasattr(value, name):
        return getattr(value, name)
    if isinstance(value, dict):
        return value.get(name, default)
    return default


def _account_mode(raw: object, module: Any) -> AccountMode:
    if raw == getattr(module, "ACCOUNT_TRADE_MODE_DEMO", 0):
        return AccountMode.DEMO
    if raw == getattr(module, "ACCOUNT_TRADE_MODE_CONTEST", 1):
        return AccountMode.CONTEST
    if raw == getattr(module, "ACCOUNT_TRADE_MODE_REAL", 2):
        return AccountMode.REAL
    return AccountMode.UNKNOWN


class NativeDemoPreflightStatus(StrEnum):
    READY = "ready"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class NativeDemoTerminalSnapshot:
    terminal_path: str
    terminal_build: str
    connected: bool
    terminal_trade_allowed: bool
    tradeapi_disabled: bool
    server: str
    login: int
    account_mode: AccountMode
    account_trade_allowed: bool
    account_expert_allowed: bool
    account_currency: str
    leverage: float
    symbol: str
    symbol_spec_fingerprint: str
    captured_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "terminal_path", _text(self.terminal_path, "terminal path"))
        object.__setattr__(self, "terminal_build", _text(self.terminal_build, "terminal build", maximum=64))
        object.__setattr__(self, "server", _text(self.server, "server", maximum=256))
        if self.login <= 0:
            raise ValueError("login must be positive")
        if not isinstance(self.account_mode, AccountMode):
            raise ValueError("account_mode must use AccountMode")
        object.__setattr__(self, "account_currency", _text(self.account_currency, "account currency", maximum=32))
        if self.leverage <= 0:
            raise ValueError("leverage must be positive")
        object.__setattr__(self, "symbol", _text(self.symbol, "symbol", maximum=64).upper())
        fingerprint = str(self.symbol_spec_fingerprint).strip().lower()
        if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
            raise ValueError("symbol_spec_fingerprint requires SHA-256 identity")
        object.__setattr__(self, "symbol_spec_fingerprint", fingerprint)
        object.__setattr__(self, "captured_at", _aware(self.captured_at, "captured_at"))
        for name in (
            "connected",
            "terminal_trade_allowed",
            "tradeapi_disabled",
            "account_trade_allowed",
            "account_expert_allowed",
        ):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be boolean")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m1941-native-demo-terminal-snapshot-v1",
            self.terminal_path,
            self.terminal_build,
            self.connected,
            self.terminal_trade_allowed,
            self.tradeapi_disabled,
            self.server,
            self.login,
            self.account_mode.value,
            self.account_trade_allowed,
            self.account_expert_allowed,
            self.account_currency,
            self.leverage,
            self.symbol,
            self.symbol_spec_fingerprint,
            self.captured_at.isoformat(),
        ))


@dataclass(frozen=True, slots=True)
class NativeDemoPreflightAssessment:
    status: NativeDemoPreflightStatus
    source_commit: str
    expected_terminal_path: str
    snapshot_fingerprint: str
    blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "M194.1 source commit"))
        object.__setattr__(self, "expected_terminal_path", _text(self.expected_terminal_path, "expected terminal path"))
        fingerprint = str(self.snapshot_fingerprint).strip().lower()
        if len(fingerprint) != 64 or any(ch not in "0123456789abcdef" for ch in fingerprint):
            raise ValueError("snapshot_fingerprint requires SHA-256 identity")
        object.__setattr__(self, "snapshot_fingerprint", fingerprint)
        blockers = tuple(sorted({_text(value, "preflight blocker") for value in self.blockers}))
        object.__setattr__(self, "blockers", blockers)
        expected = NativeDemoPreflightStatus.READY if not blockers else NativeDemoPreflightStatus.BLOCKED
        if self.status is not expected:
            raise ValueError("preflight status does not match blockers")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m1941-native-demo-preflight-v1",
            self.status.value,
            self.source_commit,
            self.expected_terminal_path,
            self.snapshot_fingerprint,
            self.blockers,
        ))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False


def assess_native_demo_preflight(
    snapshot: NativeDemoTerminalSnapshot,
    *,
    source_commit: str,
    expected_terminal_path: str,
) -> NativeDemoPreflightAssessment:
    """Fail closed unless both terminal and account boundaries permit DEMO trading."""

    commit = _git_sha(source_commit, "M194.1 source commit")
    expected_path = _text(expected_terminal_path, "expected terminal path")
    blockers: list[str] = []

    if not snapshot.connected:
        blockers.append("terminal_not_connected")
    if Path(snapshot.terminal_path).resolve() != Path(expected_path).resolve():
        blockers.append("terminal_path_mismatch")
    if snapshot.account_mode is not AccountMode.DEMO:
        blockers.append(f"account_mode_not_demo:{snapshot.account_mode.value}")
    if not snapshot.terminal_trade_allowed:
        blockers.append("terminal_trading_disabled")
    if snapshot.tradeapi_disabled:
        blockers.append("external_python_trading_disabled")
    if not snapshot.account_trade_allowed:
        blockers.append("account_trading_disabled")
    if not snapshot.account_expert_allowed:
        blockers.append("expert_trading_disabled")

    blockers_tuple = tuple(sorted(set(blockers)))
    return NativeDemoPreflightAssessment(
        NativeDemoPreflightStatus.READY if not blockers_tuple else NativeDemoPreflightStatus.BLOCKED,
        commit,
        expected_path,
        snapshot.fingerprint,
        blockers_tuple,
    )


def capture_native_demo_snapshot(
    module: Any,
    *,
    terminal_path: str,
    symbol: str,
    captured_at: datetime | None = None,
) -> NativeDemoTerminalSnapshot:
    """Read the exact connected MT5 terminal/account state without broker mutation."""

    path = _text(terminal_path, "terminal path")
    native_symbol = _text(symbol, "symbol", maximum=64).upper()
    if not module.initialize(path):
        error = module.last_error() if hasattr(module, "last_error") else "unknown"
        raise RuntimeError(f"MT5 initialize failed: {error}")
    try:
        terminal = module.terminal_info()
        account = module.account_info()
        spec = module.symbol_info(native_symbol)
        if terminal is None:
            raise RuntimeError("MT5 terminal_info unavailable")
        if account is None:
            raise RuntimeError("MT5 account_info unavailable")
        if spec is None:
            raise RuntimeError(f"MT5 symbol_info unavailable: {native_symbol}")
        spec_payload = {
            key: value
            for key, value in spec._asdict().items()
            if key in {
                "name", "digits", "point", "trade_mode", "trade_contract_size",
                "volume_min", "volume_max", "volume_step", "trade_tick_size",
                "trade_tick_value", "currency_base", "currency_profit", "currency_margin",
            }
        }
        return NativeDemoTerminalSnapshot(
            terminal_path=path,
            terminal_build=str(_attr(terminal, "build", "unknown")),
            connected=bool(_attr(terminal, "connected", False)),
            terminal_trade_allowed=bool(_attr(terminal, "trade_allowed", False)),
            tradeapi_disabled=bool(_attr(terminal, "tradeapi_disabled", True)),
            server=str(_attr(account, "server", "")),
            login=int(_attr(account, "login", 0)),
            account_mode=_account_mode(_attr(account, "trade_mode", None), module),
            account_trade_allowed=bool(_attr(account, "trade_allowed", False)),
            account_expert_allowed=bool(_attr(account, "trade_expert", False)),
            account_currency=str(_attr(account, "currency", "")),
            leverage=float(_attr(account, "leverage", 0.0)),
            symbol=native_symbol,
            symbol_spec_fingerprint=_digest(spec_payload),
            captured_at=captured_at or datetime.now(timezone.utc),
        )
    finally:
        module.shutdown()
