from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping

from .local_app import LocalApplicationView


class CodexTaskKind(StrEnum):
    REPORT = "report"
    DEVELOPMENT = "development"


@dataclass(frozen=True, slots=True)
class CodexSafetyContext:
    human_confirmed: bool
    repository_clean: bool
    trading_runtime_active: bool


@dataclass(frozen=True, slots=True)
class CodexRequest:
    kind: CodexTaskKind
    instruction: str
    support_bundle: Mapping[str, object]
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not self.instruction.strip():
            raise ValueError("Codex request instruction is required")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("Codex timeout must be in [1,3600] seconds")


@dataclass(frozen=True, slots=True)
class CodexResult:
    accepted: bool
    return_code: int
    output: str
    error: str
    command_mode: CodexTaskKind


Runner = Callable[..., subprocess.CompletedProcess[str]]


class CodexCLIReporter:
    """Least-privilege local Codex bridge with no credential or trading interface.

    Report requests are ephemeral and read-only. Development requests are explicit, require a
    clean repository and human confirmation, and are refused while the connected trading runtime
    is active. The bridge inherits an already-configured Codex login and never accepts an API key.
    """

    def __init__(
        self,
        repository: str | Path,
        *,
        codex_executable: str | None = None,
        runner: Runner = subprocess.run,
        maximum_bundle_bytes: int = 128_000,
    ) -> None:
        repo = Path(repository).resolve()
        if not repo.is_dir() or not (repo / ".git").exists():
            raise ValueError("Codex bridge repository must be a Git worktree")
        if maximum_bundle_bytes < 1_024:
            raise ValueError("Codex bundle limit is too small")
        self._repository = repo
        self._codex_executable = codex_executable
        self._runner = runner
        self._maximum_bundle_bytes = maximum_bundle_bytes

    @property
    def available(self) -> bool:
        return self._resolve_executable() is not None

    @property
    def repository(self) -> Path:
        return self._repository

    def run(self, request: CodexRequest, safety: CodexSafetyContext) -> CodexResult:
        executable = self._resolve_executable()
        if executable is None:
            return CodexResult(False, -1, "", "codex_cli_not_found", request.kind)
        if request.kind is CodexTaskKind.DEVELOPMENT:
            reasons = []
            if not safety.human_confirmed:
                reasons.append("development_not_human_confirmed")
            if not safety.repository_clean:
                reasons.append("repository_not_clean")
            if safety.trading_runtime_active:
                reasons.append("trading_runtime_active")
            if reasons:
                return CodexResult(False, -1, "", "|".join(reasons), request.kind)

        bundle = _bounded_bundle(request.support_bundle, self._maximum_bundle_bytes)
        sandbox = "read-only" if request.kind is CodexTaskKind.REPORT else "workspace-write"
        command = [
            executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--sandbox",
            sandbox,
            request.instruction.strip(),
        ]
        try:
            completed = self._runner(
                command,
                cwd=self._repository,
                input=bundle,
                text=True,
                capture_output=True,
                check=False,
                timeout=request.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return CodexResult(False, -1, "", "codex_request_timed_out", request.kind)
        except OSError as exc:
            return CodexResult(False, -1, "", f"codex_launch_failed:{type(exc).__name__}", request.kind)
        output = completed.stdout[-2_000_000:]
        error = completed.stderr[-100_000:]
        return CodexResult(completed.returncode == 0, int(completed.returncode), output, error, request.kind)

    def _resolve_executable(self) -> str | None:
        if self._codex_executable:
            candidate = Path(self._codex_executable)
            return str(candidate) if candidate.is_file() else shutil.which(self._codex_executable)
        return shutil.which("codex")


def build_support_bundle(view: LocalApplicationView, *, code_commit: str) -> dict[str, object]:
    if len(code_commit) != 40 or any(character not in "0123456789abcdefABCDEF" for character in code_commit):
        raise ValueError("Codex support bundle requires an exact code commit")
    terminal = view.terminal
    account = terminal.account if terminal is not None else None
    selection = {
        "symbol": view.selected_symbol.symbol if view.selected_symbol else None,
        "strategy_id": view.selected_strategy.strategy_id if view.selected_strategy else None,
        "strategy_hash": view.selected_strategy.strategy_hash if view.selected_strategy else None,
        "mode": view.selected_mode.value,
    }
    terminal_summary: dict[str, object] | None = None
    if terminal is not None and account is not None:
        terminal_summary = {
            "terminal_identity_hash": sha256(terminal.installation.identity_key.encode("utf-8")).hexdigest(),
            "terminal_build": terminal.terminal_build,
            "terminal_version": terminal.terminal_version,
            "connected": terminal.connected,
            "server": account.server,
            "broker_company": account.company,
            "account_mode": account.mode.value,
            "currency": account.currency,
            "balance": account.balance,
            "equity": account.equity,
            "margin": account.margin,
            "margin_free": account.margin_free,
            "trade_allowed": account.trade_allowed,
            "expert_trading_allowed": account.expert_trading_allowed,
            "open_positions": terminal.open_positions,
            "active_orders": terminal.active_orders,
            "recent_deals": terminal.recent_deals,
            "warnings": terminal.warnings,
            "captured_at": terminal.captured_at.isoformat(),
        }
    return {
        "schema": "dusty-codex-support-v1",
        "code_commit": code_commit,
        "selection": selection,
        "terminal": terminal_summary,
        "mode_gates": {
            row.mode.value: {"available": row.available, "reasons": row.reasons}
            for row in view.mode_gates
        },
        "runtime": {
            "configured": view.runtime_configured,
            "active": view.runtime_active,
            "new_entries_halted": view.new_entries_halted,
            "last_message": view.last_message,
        },
    }


_SENSITIVE_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "credential",
    "authorization",
    "auth_json",
    "private_key",
)


def sanitize_payload(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if any(fragment in key.casefold() for fragment in _SENSITIVE_FRAGMENTS):
                result[key] = "[REDACTED]"
            else:
                result[key] = sanitize_payload(raw_value)
        return result
    if isinstance(value, (tuple, list, set, frozenset)):
        return [sanitize_payload(item) for item in value]
    if isinstance(value, Path):
        return os.fspath(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _bounded_bundle(payload: Mapping[str, object], maximum_bytes: int) -> str:
    sanitized = sanitize_payload(payload)
    rendered = json.dumps(sanitized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(rendered.encode("utf-8")) > maximum_bytes:
        raise ValueError("Codex support bundle exceeds configured size limit")
    return rendered
