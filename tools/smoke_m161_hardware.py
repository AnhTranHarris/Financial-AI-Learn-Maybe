from __future__ import annotations

"""Bounded Windows hardware certification smoke for the M161 native MT5 executor."""

import argparse
from datetime import datetime, time, timezone
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

from dusty.experiment_manifest import (
    BrokerAssumptions,
    ComputeRequest,
    EvaluationPlan,
    EvaluationStage,
    ExperimentManifest,
    ExperimentWindow,
    FeatureRef,
    ManifestOrigin,
)
from dusty.experiment_queue import ExperimentResource
from dusty.mt5lab import MT5TickMode
from dusty.native_mt5_executor import (
    NativeMT5ExperimentExecutor,
    PowerShellTerminalIsolationVerifier,
    SubprocessNativeMT5Runner,
    compile_native_mt5_job,
)


def _fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _parse_date(value: str) -> datetime:
    parsed = datetime.strptime(value, "%Y-%m-%d")
    return parsed.replace(tzinfo=timezone.utc)


def _parse_clock(value: str) -> time:
    return datetime.strptime(value, "%H:%M:%S").time()


def _git_head(repo: Path) -> str:
    completed = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise RuntimeError("cannot resolve repository HEAD")
    return completed.stdout.strip().lower()


def _manifest(
    *,
    expected_head: str,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
    timeout_seconds: int,
) -> ExperimentManifest:
    identity = f"m161-hardware:{expected_head}:{symbol}:{timeframe}:{start.date()}:{end.date()}"
    return ExperimentManifest(
        experiment_id="DD-EXP-M161-HARDWARE-CERT",
        hypothesis_id="HYP-M161-NATIVE-BOUNDARY",
        hypothesis="The local MT5 Strategy Tester can reproduce the bounded Dusty research plan.",
        origin=ManifestOrigin.USER_CARSON,
        proposal_fingerprint=_fp(identity + ":proposal"),
        strategy_fingerprint=_fp(identity + ":strategy"),
        variant_fingerprint=_fp(identity + ":variant"),
        context_fingerprint=_fp(identity + ":context"),
        strategy_ancestry_fingerprints=(),
        source_provenance_fingerprints=(_fp(identity + ":source"),),
        parent_manifest_fingerprints=(),
        software_commit=expected_head,
        dataset_fingerprint=_fp(identity + ":historical-native-mt5"),
        features=(FeatureRef("native_tester_plan", "m161", _fp(identity + ":feature")),),
        broker=BrokerAssumptions(
            profile_fingerprint=_fp(identity + ":broker"),
            cost_model_fingerprint=_fp(identity + ":native-costs"),
            account_currency="USD",
            initial_balance=10_000.0,
            leverage=100,
            execution_model="native_mt5_strategy_tester",
        ),
        seed=161,
        windows=(ExperimentWindow("hardware_certification", start, end),),
        symbols=(symbol.upper(),),
        timeframes=(timeframe.upper(),),
        research_school="native_execution_certification",
        fidelity="native_mt5_hardware_certification",
        evaluation=EvaluationPlan(
            stage=EvaluationStage.A1,
            policy_fingerprint=_fp(identity + ":evaluation"),
            required_metrics=("native_trade_identity",),
            minimum_trades=1,
            walk_forward_required=False,
            cost_stress_required=False,
        ),
        risk_policy_fingerprint=_fp(identity + ":risk"),
        risk_assumptions=(("authority", "research_only"),),
        compute=ComputeRequest(
            resource=ExperimentResource.CPU_RESEARCH,
            max_wall_seconds=timeout_seconds,
            max_ram_mb=4096,
            max_workers=1,
            gpu_allowed=False,
        ),
        expected_outputs=("native_deals.csv", "tester_report.htm"),
        created_at=datetime.now(timezone.utc),
    )


def _research_plan(
    *,
    start: datetime,
    end: datetime,
    entry_clock: time,
    exit_clock: time,
    volume: float,
    stop_price: float,
) -> str:
    entry = datetime.combine(start.date(), entry_clock, tzinfo=timezone.utc)
    exit_at = datetime.combine(start.date(), exit_clock, tzinfo=timezone.utc)
    if not start <= entry < exit_at < end:
        raise ValueError("certification entry/exit times must lie inside the tester window")
    if volume <= 0 or stop_price <= 0:
        raise ValueError("certification volume and stop price must be positive")
    return (
        "trade_id,entry_time,exit_time,side,volume,stop_price,target_price\n"
        f"m161-cert-1,{entry:%Y.%m.%d %H:%M:%S},{exit_at:%Y.%m.%d %H:%M:%S},"
        f"long,{volume:.8g},{stop_price:.12g},0\n"
    )


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--terminal-data-root", required=True)
    parser.add_argument("--common-files-root", required=True)
    parser.add_argument("--expert-relative-path", required=True)
    parser.add_argument("--terminal-sha256", required=True)
    parser.add_argument("--expert-sha256", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--native-symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--from-date", default="2026-08-31")
    parser.add_argument("--to-date", default="2026-09-01")
    parser.add_argument("--entry-time", default="10:00:00")
    parser.add_argument("--exit-time", default="11:00:00")
    parser.add_argument("--volume", type=float, default=0.01)
    parser.add_argument("--stop-price", type=float, default=0.1)
    parser.add_argument("--timeout-seconds", type=int, default=180)
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    report_path = Path(args.report).resolve()
    expected_head = args.expected_head.strip().lower()
    payload: dict[str, object] = {
        "protocol": "dusty-m161-hardware-certification-v1",
        "passed": False,
        "expected_head": expected_head,
        "broker_write_authorized": False,
        "strategy_verdict": None,
        "promotion_authority": False,
    }

    try:
        actual_head = _git_head(repo)
        payload["actual_head"] = actual_head
        if actual_head != expected_head:
            raise RuntimeError("repository HEAD does not match expected hardware certification SHA")
        if len(expected_head) != 40 or any(ch not in "0123456789abcdef" for ch in expected_head):
            raise ValueError("expected-head must be a full 40-character Git SHA")

        start = _parse_date(args.from_date)
        end = _parse_date(args.to_date)
        if end <= start:
            raise ValueError("to-date must follow from-date")
        entry_clock = _parse_clock(args.entry_time)
        exit_clock = _parse_clock(args.exit_time)
        symbol = args.native_symbol.strip().upper()
        timeframe = args.timeframe.strip().upper()
        if not symbol or not timeframe:
            raise ValueError("native symbol and timeframe are required")
        if not 30 <= args.timeout_seconds <= 3600:
            raise ValueError("hardware certification timeout must be between 30 and 3600 seconds")

        manifest = _manifest(
            expected_head=expected_head,
            symbol=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            timeout_seconds=args.timeout_seconds,
        )
        package = compile_native_mt5_job(
            manifest,
            terminal_path=Path(args.terminal_path),
            terminal_data_root=Path(args.terminal_data_root),
            terminal_binary_sha256=args.terminal_sha256,
            expert_relative_path=args.expert_relative_path,
            expert_binary_sha256=args.expert_sha256,
            symbol=symbol,
            timeframe=timeframe,
            window_label="hardware_certification",
            tick_mode=MT5TickMode.EVERY_TICK,
            execution_mode=0,
            deviation_points=20,
        )
        plan = _research_plan(
            start=start,
            end=end,
            entry_clock=entry_clock,
            exit_clock=exit_clock,
            volume=args.volume,
            stop_price=args.stop_price,
        )
        executor = NativeMT5ExperimentExecutor(
            common_files_root=Path(args.common_files_root),
            work_root=Path(args.work_root),
            runner=SubprocessNativeMT5Runner(),
            isolation_verifier=PowerShellTerminalIsolationVerifier(),
        )
        result = executor.execute(package, research_manifest_csv=plan)
        payload.update(
            {
                "package_fingerprint": package.fingerprint,
                "manifest_fingerprint": package.manifest_fingerprint,
                "execution_fingerprint": package.execution_fingerprint,
                "strategy_fingerprint": package.strategy_fingerprint,
                "native_symbol": package.symbol,
                "timeframe": package.timeframe,
                "window": [package.start.isoformat(), package.end.isoformat()],
                "terminal_path": package.terminal_path,
                "terminal_data_root": package.terminal_data_root,
                "terminal_sha256": package.terminal_binary_sha256,
                "expert_relative_path": package.expert_relative_path,
                "expert_sha256": package.expert_binary_sha256,
                "failure_kind": result.failure_kind.value if result.failure_kind else None,
                "reason": result.reason,
            }
        )
        if result.evidence is None:
            _write_report(report_path, payload)
            print(json.dumps(payload, sort_keys=True))
            return 2

        evidence = result.evidence
        payload.update(
            {
                "passed": True,
                "deal_count": evidence.deal_count,
                "trade_count": evidence.trade_count,
                "native_net_pnl": evidence.native_net_pnl,
                "manifest_artifact_sha256": evidence.manifest_artifact_sha256,
                "tester_config_sha256": evidence.tester_config_sha256,
                "tester_set_sha256": evidence.tester_set_sha256,
                "tester_report_sha256": evidence.tester_report_sha256,
                "deals_artifact_sha256": evidence.deals_artifact_sha256,
            }
        )
        _write_report(report_path, payload)
        print(json.dumps(payload, sort_keys=True))
        return 0
    except Exception as exc:  # hardware boundary converts unexpected errors to fail-closed report
        payload.update(
            {
                "failure_kind": "HARNESS_FAIL",
                "reason": f"{type(exc).__name__}:{exc}",
            }
        )
        _write_report(report_path, payload)
        print(json.dumps(payload, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
