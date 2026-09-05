from __future__ import annotations

"""M161 deterministic, research-only native MetaTrader 5 experiment executor."""

import csv
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Callable, Protocol, Sequence

from .controlled_evolution import (
    ExperimentOutcome,
    ExperimentOutcomeType,
    InfrastructureFailureKind,
)
from .experiment_manifest import ExperimentManifest
from .mt5lab import MT5TickMode
from .tester_parity import normalize_tester_trades, parse_tester_deals_csv


_MODEL = {
    MT5TickMode.EVERY_TICK: 0,
    MT5TickMode.ONE_MINUTE_OHLC: 1,
    MT5TickMode.OPEN_PRICES: 2,
    MT5TickMode.REAL_TICKS: 4,
}


class NativeMT5FailureKind(StrEnum):
    STRATEGY_FAIL = "STRATEGY_FAIL"
    DATA_FAIL = "DATA_FAIL"
    TERMINAL_FAIL = "TERMINAL_FAIL"
    TESTER_FAIL = "TESTER_FAIL"
    RESOURCE_FAIL = "RESOURCE_FAIL"
    TIMEOUT = "TIMEOUT"
    CONFIG_FAIL = "CONFIG_FAIL"


def _sha256_identity(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _clean(value: str, label: str) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered:
        raise ValueError(f"{label} is required and must be one line")
    return rendered


def _safe_relative(value: str, label: str) -> str:
    rendered = _clean(str(value).replace("\\", "/"), label)
    path = PurePosixPath(rendered)
    if path.is_absolute() or ".." in path.parts or any(part in {"", "."} for part in path.parts):
        raise ValueError(f"{label} must be a safe relative path")
    return path.as_posix()


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


@dataclass(frozen=True, slots=True)
class NativeMT5JobPackage:
    manifest_fingerprint: str
    execution_fingerprint: str
    experiment_id: str
    strategy_fingerprint: str
    terminal_path: str
    terminal_data_root: str
    terminal_binary_sha256: str
    expert_relative_path: str
    expert_binary_sha256: str
    symbol: str
    timeframe: str
    window_label: str
    start: datetime
    end: datetime
    tick_mode: MT5TickMode
    deposit: float
    currency: str
    leverage: int
    execution_mode: int
    timeout_seconds: int
    magic: int
    deviation_points: int
    common_relative_dir: str
    set_file_name: str
    report_relative_path: str
    broker_write_authority: bool = False
    remote_agents_allowed: bool = False
    cloud_agents_allowed: bool = False
    optimization_allowed: bool = False
    exclusive_terminal_required: bool = True

    def __post_init__(self) -> None:
        for name in (
            "manifest_fingerprint",
            "execution_fingerprint",
            "strategy_fingerprint",
            "terminal_binary_sha256",
            "expert_binary_sha256",
        ):
            object.__setattr__(self, name, _sha256_identity(getattr(self, name), name))
        for name in (
            "experiment_id",
            "terminal_path",
            "terminal_data_root",
            "symbol",
            "timeframe",
            "window_label",
            "currency",
        ):
            object.__setattr__(self, name, _clean(getattr(self, name), name))
        object.__setattr__(self, "symbol", self.symbol.upper())
        object.__setattr__(self, "timeframe", self.timeframe.upper())
        object.__setattr__(self, "currency", self.currency.upper())
        object.__setattr__(
            self,
            "expert_relative_path",
            _safe_relative(self.expert_relative_path, "expert path"),
        )
        object.__setattr__(
            self,
            "common_relative_dir",
            _safe_relative(self.common_relative_dir, "common directory"),
        )
        object.__setattr__(
            self,
            "set_file_name",
            _safe_relative(self.set_file_name, "set filename"),
        )
        object.__setattr__(
            self,
            "report_relative_path",
            _safe_relative(self.report_relative_path, "report path"),
        )
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("native MT5 start must be timezone-aware")
        if self.end.tzinfo is None or self.end.utcoffset() is None:
            raise ValueError("native MT5 end must be timezone-aware")
        start = self.start.astimezone(timezone.utc)
        end = self.end.astimezone(timezone.utc)
        if end <= start:
            raise ValueError("native MT5 end must follow start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        if not math.isfinite(self.deposit) or self.deposit <= 0:
            raise ValueError("native MT5 deposit must be finite and positive")
        if not 1 <= self.leverage <= 10000:
            raise ValueError("native MT5 leverage out of range")
        if self.execution_mode < -1 or self.execution_mode > 600_000:
            raise ValueError("native MT5 execution mode out of range")
        if not 1 <= self.timeout_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("native MT5 timeout out of range")
        if not 1 <= self.magic <= 2**63 - 1:
            raise ValueError("native MT5 magic out of range")
        if not 0 <= self.deviation_points <= 1_000_000:
            raise ValueError("native MT5 deviation points out of range")
        if any(
            (
                self.broker_write_authority,
                self.remote_agents_allowed,
                self.cloud_agents_allowed,
                self.optimization_allowed,
            )
        ):
            raise ValueError("M161 is local, single-test, research-only")
        if not self.exclusive_terminal_required:
            raise ValueError("M161 requires exclusive terminal-path ownership")

    @property
    def manifest_relative_path(self) -> str:
        return f"{self.common_relative_dir}/manifest.csv"

    @property
    def deals_relative_path(self) -> str:
        return f"{self.common_relative_dir}/deals.csv"

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-native-mt5-job-v1",
            "manifest_fingerprint": self.manifest_fingerprint,
            "execution_fingerprint": self.execution_fingerprint,
            "experiment_id": self.experiment_id,
            "strategy_fingerprint": self.strategy_fingerprint,
            "terminal": {
                "path": str(Path(self.terminal_path)),
                "data_root": str(Path(self.terminal_data_root)),
                "binary_sha256": self.terminal_binary_sha256,
                "exclusive_required": True,
            },
            "expert": {
                "relative_path": self.expert_relative_path,
                "binary_sha256": self.expert_binary_sha256,
            },
            "binding": {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "window_label": self.window_label,
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "tick_mode": self.tick_mode.value,
            },
            "account": {
                "deposit": float(self.deposit),
                "currency": self.currency,
                "leverage": self.leverage,
                "execution_mode": self.execution_mode,
            },
            "runtime": {
                "timeout_seconds": self.timeout_seconds,
                "magic": self.magic,
                "deviation_points": self.deviation_points,
            },
            "artifacts": {
                "common_manifest": self.manifest_relative_path,
                "common_deals": self.deals_relative_path,
                "set_file_name": self.set_file_name,
                "report_relative_path": self.report_relative_path,
            },
            "authority": {
                "broker_write": False,
                "remote_agents": False,
                "cloud_agents": False,
                "optimization": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def broker_write_authorized(self) -> bool:
        return False


def compile_native_mt5_job(
    manifest: ExperimentManifest,
    *,
    terminal_path: str | Path,
    terminal_data_root: str | Path,
    terminal_binary_sha256: str,
    expert_relative_path: str,
    expert_binary_sha256: str,
    symbol: str,
    timeframe: str,
    window_label: str,
    tick_mode: MT5TickMode,
    execution_mode: int = 0,
    deviation_points: int = 20,
) -> NativeMT5JobPackage:
    symbol_norm = _clean(symbol, "symbol").upper()
    timeframe_norm = _clean(timeframe, "timeframe").upper()
    if symbol_norm not in manifest.symbols or timeframe_norm not in manifest.timeframes:
        raise ValueError("native MT5 binding must be declared by the immutable manifest")
    matching = tuple(
        row
        for row in manifest.windows
        if row.label.strip().lower() == window_label.strip().lower()
    )
    if len(matching) != 1:
        raise ValueError("native MT5 window label must identify exactly one manifest window")
    window = matching[0]
    terminal_hash = _sha256_identity(terminal_binary_sha256, "terminal binary")
    expert_hash = _sha256_identity(expert_binary_sha256, "expert binary")
    binding_identity = _digest(
        {
            "execution_fingerprint": manifest.execution_fingerprint,
            "terminal_binary_sha256": terminal_hash,
            "expert_binary_sha256": expert_hash,
            "symbol": symbol_norm,
            "timeframe": timeframe_norm,
            "window": window.payload,
            "tick_mode": tick_mode.value,
            "execution_mode": execution_mode,
        }
    )
    magic = 667_000_000 + (int(binding_identity[:12], 16) % 100_000_000)
    return NativeMT5JobPackage(
        manifest_fingerprint=manifest.fingerprint,
        execution_fingerprint=manifest.execution_fingerprint,
        experiment_id=manifest.experiment_id,
        strategy_fingerprint=manifest.strategy_fingerprint,
        terminal_path=str(Path(terminal_path)),
        terminal_data_root=str(Path(terminal_data_root)),
        terminal_binary_sha256=terminal_hash,
        expert_relative_path=expert_relative_path,
        expert_binary_sha256=expert_hash,
        symbol=symbol_norm,
        timeframe=timeframe_norm,
        window_label=window.label,
        start=window.start,
        end=window.end,
        tick_mode=tick_mode,
        deposit=manifest.broker.initial_balance,
        currency=manifest.broker.account_currency,
        leverage=manifest.broker.leverage,
        execution_mode=execution_mode,
        timeout_seconds=manifest.compute.max_wall_seconds,
        magic=magic,
        deviation_points=deviation_points,
        common_relative_dir=f"DustyDragon/M161/{binding_identity}",
        set_file_name=f"dusty_m161_{binding_identity}.set",
        report_relative_path=f"DustyReports/m161_{binding_identity}.htm",
    )


def render_native_set(package: NativeMT5JobPackage) -> str:
    """Render deterministic non-optimization input values for DustyResearchEA."""

    manifest_path = package.manifest_relative_path.replace("/", "\\")
    deals_path = package.deals_relative_path.replace("/", "\\")
    magic = str(package.magic)
    deviation = str(package.deviation_points)
    return "\n".join(
        (
            "; Dusty Dragon M161 deterministic Strategy Tester inputs",
            f"InpManifestFile={manifest_path}",
            f"InpDealsFile={deals_path}",
            f"InpStrategyHash={package.strategy_fingerprint}",
            f"InpMagic={magic}||{magic}||1||{magic}||N",
            f"InpDeviationPoints={deviation}||{deviation}||1||{deviation}||N",
            "",
        )
    )


def render_native_tester_ini(package: NativeMT5JobPackage) -> str:
    model = _MODEL[package.tick_mode]
    deposit = format(package.deposit, ".12g")
    expert = (
        PurePosixPath(package.expert_relative_path)
        .with_suffix("")
        .as_posix()
        .replace("/", "\\")
    )
    report = package.report_relative_path.replace("/", "\\")
    return "\n".join(
        (
            "[Experts]",
            "AllowLiveTrading=0",
            "AllowDllImport=0",
            "Enabled=1",
            "",
            "[Tester]",
            f"Expert={expert}",
            f"ExpertParameters={package.set_file_name}",
            f"Symbol={package.symbol}",
            f"Period={package.timeframe}",
            f"Model={model}",
            f"ExecutionMode={package.execution_mode}",
            "Optimization=0",
            f"FromDate={package.start:%Y.%m.%d}",
            f"ToDate={package.end:%Y.%m.%d}",
            f"Deposit={deposit}",
            f"Currency={package.currency}",
            f"Leverage=1:{package.leverage}",
            f"Report={report}",
            "ReplaceReport=1",
            "UseLocal=1",
            "UseRemote=0",
            "UseCloud=0",
            "Visual=0",
            "ShutdownTerminal=1",
            "",
        )
    )


@dataclass(frozen=True, slots=True)
class NativeMT5ProcessResult:
    returncode: int | None
    timed_out: bool
    stdout: str = ""
    stderr: str = ""
    pid: int | None = None
    failure_hint: NativeMT5FailureKind | None = None

    def __post_init__(self) -> None:
        if self.failure_hint is NativeMT5FailureKind.STRATEGY_FAIL:
            raise ValueError("process runner cannot classify trading strategy evidence")
        if not self.timed_out and self.returncode is None:
            raise ValueError("completed process result requires returncode")


class NativeMT5ProcessRunner(Protocol):
    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> NativeMT5ProcessResult:
        ...


class TerminalIsolationVerifier(Protocol):
    def terminal_path_available(self, terminal_path: Path) -> bool:
        ...


class PowerShellTerminalIsolationVerifier:
    """Fail-closed Windows check for another process using the exact terminal binary."""

    _SCRIPT = r"""
$ErrorActionPreference='Stop'
$target=[IO.Path]::GetFullPath($args[0])
$rows=@(Get-CimInstance Win32_Process -Filter "Name='terminal64.exe'")
if(@($rows | Where-Object { -not $_.ExecutablePath }).Count -gt 0) { exit 4 }
$matches=@($rows | Where-Object { [IO.Path]::GetFullPath($_.ExecutablePath) -ieq $target })
if($matches.Count -eq 0) { Write-Output 'AVAILABLE'; exit 0 }
Write-Output 'CONFLICT'; exit 3
""".strip()

    def __init__(self, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> None:
        self._runner = runner

    def terminal_path_available(self, terminal_path: Path) -> bool:
        if os.name != "nt":
            return False
        try:
            completed = self._runner(
                (
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    self._SCRIPT,
                    str(terminal_path),
                ),
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0 and completed.stdout.strip() == "AVAILABLE"


class SubprocessNativeMT5Runner:
    """Bounded runner; timeout cleanup targets only the PID it created and descendants."""

    def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> NativeMT5ProcessResult:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        creationflags = 0
        start_new_session = False
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            start_new_session = True
        process = subprocess.Popen(
            tuple(command),
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=creationflags,
            start_new_session=start_new_session,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            return NativeMT5ProcessResult(
                returncode=int(process.returncode or 0),
                timed_out=False,
                stdout=stdout or "",
                stderr=stderr or "",
                pid=process.pid,
            )
        except subprocess.TimeoutExpired:
            self._terminate_owned_tree(process)
            stdout, stderr = process.communicate()
            return NativeMT5ProcessResult(
                returncode=process.returncode,
                timed_out=True,
                stdout=stdout or "",
                stderr=stderr or "",
                pid=process.pid,
            )

    @staticmethod
    def _terminate_owned_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            try:
                subprocess.run(
                    ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
        else:
            try:
                os.killpg(process.pid, 15)
            except OSError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


@dataclass(frozen=True, slots=True)
class NativeMT5Evidence:
    package_fingerprint: str
    manifest_fingerprint: str
    execution_fingerprint: str
    strategy_fingerprint: str
    manifest_artifact_sha256: str
    tester_config_sha256: str
    tester_set_sha256: str
    tester_report_sha256: str
    deals_artifact_sha256: str
    deal_count: int
    trade_count: int
    native_net_pnl: float

    def __post_init__(self) -> None:
        for name in (
            "package_fingerprint",
            "manifest_fingerprint",
            "execution_fingerprint",
            "strategy_fingerprint",
            "manifest_artifact_sha256",
            "tester_config_sha256",
            "tester_set_sha256",
            "tester_report_sha256",
            "deals_artifact_sha256",
        ):
            object.__setattr__(self, name, _sha256_identity(getattr(self, name), name))
        if self.deal_count < 0 or self.trade_count < 0:
            raise ValueError("native MT5 evidence counts cannot be negative")
        if not math.isfinite(self.native_net_pnl):
            raise ValueError("native MT5 PnL must be finite")


@dataclass(frozen=True, slots=True)
class NativeMT5ExecutionResult:
    package_fingerprint: str
    failure_kind: NativeMT5FailureKind | None
    reason: str
    evidence: NativeMT5Evidence | None = None
    strategy_passed: bool | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "package_fingerprint",
            _sha256_identity(self.package_fingerprint, "package"),
        )
        object.__setattr__(self, "reason", _clean(self.reason, "execution reason"))
        if self.failure_kind is None and self.evidence is None:
            raise ValueError("successful native MT5 execution requires evidence")
        if self.failure_kind is NativeMT5FailureKind.STRATEGY_FAIL:
            if self.evidence is None or self.strategy_passed is not False:
                raise ValueError("strategy failure requires usable evidence and failed verdict")
        elif self.failure_kind is not None:
            if self.evidence is not None or self.strategy_passed is not None:
                raise ValueError("infrastructure failures cannot carry trading evidence")
        elif self.strategy_passed is False:
            raise ValueError("failed strategy verdict requires STRATEGY_FAIL classification")
        if self.evidence is not None and self.evidence.package_fingerprint != self.package_fingerprint:
            raise ValueError("native MT5 evidence/package identity mismatch")

    @property
    def infrastructure_failure(self) -> bool:
        return self.failure_kind not in {None, NativeMT5FailureKind.STRATEGY_FAIL}

    @property
    def strategy_evidence_usable(self) -> bool:
        return self.evidence is not None and not self.infrastructure_failure

    def with_strategy_verdict(self, *, passed: bool, reason: str) -> NativeMT5ExecutionResult:
        if self.infrastructure_failure or self.evidence is None:
            raise ValueError("cannot apply a strategy verdict to infrastructure failure")
        return replace(
            self,
            failure_kind=None if passed else NativeMT5FailureKind.STRATEGY_FAIL,
            reason=_clean(reason, "strategy verdict"),
            strategy_passed=passed,
        )

    def to_experiment_outcome(self, *, subject_fingerprint: str) -> ExperimentOutcome:
        if self.infrastructure_failure:
            assert self.failure_kind is not None
            mapping = {
                NativeMT5FailureKind.DATA_FAIL: InfrastructureFailureKind.DATA,
                NativeMT5FailureKind.RESOURCE_FAIL: InfrastructureFailureKind.RESOURCE,
                NativeMT5FailureKind.TIMEOUT: InfrastructureFailureKind.PROCESS,
                NativeMT5FailureKind.TERMINAL_FAIL: InfrastructureFailureKind.MT5,
                NativeMT5FailureKind.TESTER_FAIL: InfrastructureFailureKind.MT5,
                NativeMT5FailureKind.CONFIG_FAIL: InfrastructureFailureKind.MT5,
            }
            return ExperimentOutcome(
                subject_fingerprint,
                ExperimentOutcomeType.INFRASTRUCTURE_FAILED,
                self.reason,
                infrastructure_kind=mapping[self.failure_kind],
            )
        if self.strategy_passed is None or self.evidence is None:
            raise ValueError("strategy verdict is required before producing M158 outcome")
        evidence = (
            self.evidence.deals_artifact_sha256,
            self.evidence.tester_report_sha256,
            self.evidence.package_fingerprint,
        )
        return ExperimentOutcome(
            subject_fingerprint,
            ExperimentOutcomeType.PASSED
            if self.strategy_passed
            else ExperimentOutcomeType.RESEARCH_FAILED,
            self.reason,
            evidence_fingerprints=evidence,
        )


class NativeMT5ExperimentExecutor:
    def __init__(
        self,
        *,
        common_files_root: str | Path,
        work_root: str | Path,
        runner: NativeMT5ProcessRunner,
        isolation_verifier: TerminalIsolationVerifier,
    ) -> None:
        self.common_files_root = Path(common_files_root)
        self.work_root = Path(work_root)
        self.runner = runner
        self.isolation_verifier = isolation_verifier

    @property
    def broker_write_authorized(self) -> bool:
        return False

    def execute(
        self,
        package: NativeMT5JobPackage,
        *,
        research_manifest_csv: str,
    ) -> NativeMT5ExecutionResult:
        try:
            return self._execute(package, research_manifest_csv=research_manifest_csv)
        except OSError as exc:
            return self._failure(
                package,
                NativeMT5FailureKind.RESOURCE_FAIL,
                f"filesystem_error:{type(exc).__name__}",
            )
        except ValueError as exc:
            return self._failure(
                package,
                NativeMT5FailureKind.CONFIG_FAIL,
                f"validation_error:{exc}",
            )

    def _execute(
        self,
        package: NativeMT5JobPackage,
        *,
        research_manifest_csv: str,
    ) -> NativeMT5ExecutionResult:
        if not research_manifest_csv.strip():
            return self._failure(package, NativeMT5FailureKind.CONFIG_FAIL, "research_manifest_empty")
        header = "trade_id,entry_time,exit_time,side,volume,stop_price,target_price"
        if research_manifest_csv.splitlines()[0].strip() != header:
            return self._failure(
                package,
                NativeMT5FailureKind.CONFIG_FAIL,
                "research_manifest_schema_mismatch",
            )
        reader = csv.DictReader(io.StringIO(research_manifest_csv))
        planned_ids = tuple(str(row.get("trade_id", "")).strip() for row in reader)
        if (
            not planned_ids
            or any(not trade_id for trade_id in planned_ids)
            or len(set(planned_ids)) != len(planned_ids)
        ):
            return self._failure(
                package,
                NativeMT5FailureKind.CONFIG_FAIL,
                "research_manifest_trade_ids_invalid",
            )

        terminal = Path(package.terminal_path)
        data_root = Path(package.terminal_data_root)
        expert = self._expert_path(package, data_root)
        if not terminal.is_file():
            return self._failure(package, NativeMT5FailureKind.TERMINAL_FAIL, "terminal_binary_missing")
        if _file_sha256(terminal) != package.terminal_binary_sha256:
            return self._failure(
                package,
                NativeMT5FailureKind.CONFIG_FAIL,
                "terminal_binary_hash_mismatch",
            )
        if not expert.is_file():
            return self._failure(package, NativeMT5FailureKind.CONFIG_FAIL, "expert_binary_missing")
        if _file_sha256(expert) != package.expert_binary_sha256:
            return self._failure(
                package,
                NativeMT5FailureKind.CONFIG_FAIL,
                "expert_binary_hash_mismatch",
            )
        if not self.isolation_verifier.terminal_path_available(terminal):
            return self._failure(
                package,
                NativeMT5FailureKind.TERMINAL_FAIL,
                "terminal_path_already_running_or_unverifiable",
            )

        common_dir = self.common_files_root.joinpath(
            *PurePosixPath(package.common_relative_dir).parts
        )
        manifest_path = common_dir / "manifest.csv"
        deals_path = common_dir / "deals.csv"
        set_path = data_root / "MQL5" / "Profiles" / "Tester" / package.set_file_name
        report_path = data_root.joinpath(*PurePosixPath(package.report_relative_path).parts)
        run_dir = self.work_root / package.fingerprint
        config_path = run_dir / "tester.ini"

        common_dir.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        set_path.parent.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir(parents=True, exist_ok=True)
        for stale in (deals_path, report_path):
            if stale.exists():
                stale.unlink()

        set_text = render_native_set(package)
        ini_text = render_native_tester_ini(package)
        _write_text_atomic(manifest_path, research_manifest_csv)
        _write_text_atomic(set_path, set_text)
        _write_text_atomic(config_path, ini_text)

        process = self.runner.run(
            (str(terminal), f"/config:{config_path}"),
            cwd=terminal.parent,
            timeout_seconds=float(package.timeout_seconds),
        )
        if process.timed_out:
            return self._failure(package, NativeMT5FailureKind.TIMEOUT, "strategy_tester_timeout")
        if process.failure_hint is not None:
            return self._failure(package, process.failure_hint, "runner_failure_hint")
        if process.returncode != 0:
            return self._failure(
                package,
                NativeMT5FailureKind.TESTER_FAIL,
                f"strategy_tester_exit:{process.returncode}",
            )
        if not report_path.is_file() or report_path.stat().st_size <= 0:
            return self._failure(
                package,
                NativeMT5FailureKind.TESTER_FAIL,
                "tester_report_missing",
            )
        if not deals_path.is_file() or deals_path.stat().st_size <= 0:
            return self._failure(
                package,
                NativeMT5FailureKind.TESTER_FAIL,
                "native_deals_missing",
            )

        deals_text = deals_path.read_text(encoding="utf-8-sig")
        try:
            deals = parse_tester_deals_csv(deals_text)
            trades = normalize_tester_trades(deals)
        except ValueError:
            return self._failure(
                package,
                NativeMT5FailureKind.TESTER_FAIL,
                "native_deals_malformed",
            )
        if any(row.strategy_hash != package.strategy_fingerprint for row in deals):
            return self._failure(
                package,
                NativeMT5FailureKind.CONFIG_FAIL,
                "native_deals_strategy_mismatch",
            )
        observed_ids = tuple(row.trade_id for row in trades)
        if len(observed_ids) != len(planned_ids) or set(observed_ids) != set(planned_ids):
            return self._failure(
                package,
                NativeMT5FailureKind.TESTER_FAIL,
                "native_trade_manifest_mismatch",
            )
        evidence = NativeMT5Evidence(
            package_fingerprint=package.fingerprint,
            manifest_fingerprint=package.manifest_fingerprint,
            execution_fingerprint=package.execution_fingerprint,
            strategy_fingerprint=package.strategy_fingerprint,
            manifest_artifact_sha256=_file_sha256(manifest_path),
            tester_config_sha256=_file_sha256(config_path),
            tester_set_sha256=_file_sha256(set_path),
            tester_report_sha256=_file_sha256(report_path),
            deals_artifact_sha256=_file_sha256(deals_path),
            deal_count=len(deals),
            trade_count=len(trades),
            native_net_pnl=sum(row.net_pnl for row in trades),
        )
        return NativeMT5ExecutionResult(
            package_fingerprint=package.fingerprint,
            failure_kind=None,
            reason="native_mt5_execution_completed",
            evidence=evidence,
        )

    @staticmethod
    def _expert_path(package: NativeMT5JobPackage, data_root: Path) -> Path:
        candidate = data_root / "MQL5" / "Experts"
        for part in PurePosixPath(package.expert_relative_path).parts:
            candidate /= part
        return candidate

    @staticmethod
    def _failure(
        package: NativeMT5JobPackage,
        kind: NativeMT5FailureKind,
        reason: str,
    ) -> NativeMT5ExecutionResult:
        if kind is NativeMT5FailureKind.STRATEGY_FAIL:
            raise ValueError("executor cannot invent strategy failure")
        return NativeMT5ExecutionResult(
            package_fingerprint=package.fingerprint,
            failure_kind=kind,
            reason=reason,
            evidence=None,
        )
