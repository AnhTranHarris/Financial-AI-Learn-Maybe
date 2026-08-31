from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
from typing import Any, Callable


class AccountMode(StrEnum):
    DEMO = "demo"
    CONTEST = "contest"
    REAL = "real"
    UNKNOWN = "unknown"


class SessionFault(StrEnum):
    TERMINAL_UNAVAILABLE = "terminal_unavailable"
    TERMINAL_DRIFT = "terminal_drift"
    ACCOUNT_DRIFT = "account_drift"
    MODE_DRIFT = "mode_drift"
    PERMISSION_LOSS = "permission_loss"
    SYMBOL_SPEC_DRIFT = "symbol_spec_drift"


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    terminal_path: str
    terminal_build: str
    server: str
    login: int
    account_mode: AccountMode
    account_currency: str
    leverage: float
    trade_allowed: bool
    expert_trading_allowed: bool
    margin_mode: int
    symbol_spec_fingerprint: str
    captured_at: datetime

    def __post_init__(self) -> None:
        if not all((self.terminal_path.strip(), self.terminal_build.strip(), self.server.strip(), self.account_currency.strip(), self.symbol_spec_fingerprint.strip())):
            raise ValueError("session identity is incomplete")
        if self.login <= 0 or self.leverage <= 0:
            raise ValueError("session login/leverage must be positive")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("session capture timestamp must be timezone-aware")

    @property
    def fingerprint(self) -> str:
        payload = {
            "terminal_path": self.terminal_path,
            "terminal_build": self.terminal_build,
            "server": self.server,
            "login": self.login,
            "account_mode": self.account_mode.value,
            "account_currency": self.account_currency,
            "leverage": self.leverage,
            "trade_allowed": self.trade_allowed,
            "expert_trading_allowed": self.expert_trading_allowed,
            "margin_mode": self.margin_mode,
            "symbol_spec_fingerprint": self.symbol_spec_fingerprint,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SessionVerification:
    valid: bool
    latched: bool
    faults: tuple[SessionFault, ...]


class DemoSession:
    """A verified session may only lose authority. Faults are permanent for this object."""

    def __init__(self, identity: SessionIdentity) -> None:
        if identity.account_mode is not AccountMode.DEMO:
            raise ValueError("DemoSession requires a verified demo account")
        if not identity.trade_allowed or not identity.expert_trading_allowed:
            raise ValueError("DemoSession requires account and expert trading permission")
        self.identity = identity
        self._faults: set[SessionFault] = set()

    @property
    def faults(self) -> tuple[SessionFault, ...]:
        return tuple(sorted(self._faults, key=lambda item: item.value))

    @property
    def broker_write_authorized(self) -> bool:
        return not self._faults

    def latch(self, fault: SessionFault) -> None:
        self._faults.add(fault)

    def verify(self, current: SessionIdentity) -> SessionVerification:
        if self._faults:
            return SessionVerification(False, True, self.faults)
        expected = self.identity
        if current.account_mode is not AccountMode.DEMO:
            self.latch(SessionFault.MODE_DRIFT)
        if current.login != expected.login or current.server != expected.server or current.account_currency != expected.account_currency:
            self.latch(SessionFault.ACCOUNT_DRIFT)
        if current.terminal_path != expected.terminal_path or current.terminal_build != expected.terminal_build:
            self.latch(SessionFault.TERMINAL_DRIFT)
        if not current.trade_allowed or not current.expert_trading_allowed:
            self.latch(SessionFault.PERMISSION_LOSS)
        if current.symbol_spec_fingerprint != expected.symbol_spec_fingerprint:
            self.latch(SessionFault.SYMBOL_SPEC_DRIFT)
        return SessionVerification(not self._faults, bool(self._faults), self.faults)


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if hasattr(value, name):
        return getattr(value, name)
    if isinstance(value, dict):
        return value.get(name, default)
    return default


def _account_mode(raw: object, module: Any) -> AccountMode:
    demo = getattr(module, "ACCOUNT_TRADE_MODE_DEMO", 0)
    contest = getattr(module, "ACCOUNT_TRADE_MODE_CONTEST", 1)
    real = getattr(module, "ACCOUNT_TRADE_MODE_REAL", 2)
    if raw == demo:
        return AccountMode.DEMO
    if raw == contest:
        return AccountMode.CONTEST
    if raw == real:
        return AccountMode.REAL
    return AccountMode.UNKNOWN


class MT5IdentityProbe:
    """Reads identity either on an already-open MT5 connection or in a self-managed read call."""

    def __init__(
        self,
        module: Any,
        *,
        terminal_path: str,
        symbol_spec_fingerprint: str | Callable[[], str],
    ) -> None:
        if not terminal_path.strip():
            raise ValueError("terminal path is required")
        self.module = module
        self.terminal_path = terminal_path
        self._symbol_spec_fingerprint = symbol_spec_fingerprint

    def read_connected(self) -> SessionIdentity:
        account = self.module.account_info()
        terminal = self.module.terminal_info() if hasattr(self.module, "terminal_info") else None
        if account is None:
            raise RuntimeError("MT5 account_info unavailable")
        build = _attr(terminal, "build", None)
        if build is None and hasattr(self.module, "version"):
            version = self.module.version()
            build = version[1] if version and len(version) > 1 else "unknown"
        fingerprint = self._symbol_spec_fingerprint() if callable(self._symbol_spec_fingerprint) else self._symbol_spec_fingerprint
        return SessionIdentity(
            terminal_path=self.terminal_path,
            terminal_build=str(build if build is not None else "unknown"),
            server=str(_attr(account, "server", "")),
            login=int(_attr(account, "login", 0)),
            account_mode=_account_mode(_attr(account, "trade_mode", None), self.module),
            account_currency=str(_attr(account, "currency", "")),
            leverage=float(_attr(account, "leverage", 0.0)),
            trade_allowed=bool(_attr(account, "trade_allowed", False)),
            expert_trading_allowed=bool(_attr(account, "trade_expert", False)),
            margin_mode=int(_attr(account, "margin_mode", -1)),
            symbol_spec_fingerprint=str(fingerprint),
            captured_at=datetime.now(timezone.utc),
        )

    def read(self) -> SessionIdentity:
        if not self.module.initialize(self.terminal_path):
            error = self.module.last_error() if hasattr(self.module, "last_error") else "unknown"
            raise RuntimeError(f"MT5 initialize failed: {error}")
        try:
            return self.read_connected()
        finally:
            self.module.shutdown()


def make_mt5_identity_reader(
    module: Any,
    *,
    terminal_path: str,
    symbol_spec_fingerprint: str | Callable[[], str],
) -> Callable[[], SessionIdentity]:
    return MT5IdentityProbe(
        module,
        terminal_path=terminal_path,
        symbol_spec_fingerprint=symbol_spec_fingerprint,
    ).read
