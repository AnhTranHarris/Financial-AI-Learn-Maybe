from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

from dusty.m194_native_demo_preflight import assess_native_demo_preflight, capture_native_demo_snapshot


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout.strip()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="M194.1 read-only native Coinexx Demo preflight")
    root.add_argument("--repo", required=True)
    root.add_argument("--expected-head", required=True)
    root.add_argument("--terminal-path", required=True)
    root.add_argument("--symbol", default="EURUSD")
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    repo = Path(args.repo).resolve()
    terminal_path = str(Path(args.terminal_path).resolve())

    actual_head = _git(repo, "rev-parse", "HEAD")
    if actual_head != args.expected_head.strip().lower():
        raise RuntimeError(f"HEAD mismatch: expected {args.expected_head}, got {actual_head}")
    dirty = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    if dirty:
        raise RuntimeError("repository worktree must be clean for native M194.1 evidence")

    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is required for native M194.1 validation") from exc

    snapshot = capture_native_demo_snapshot(mt5, terminal_path=terminal_path, symbol=args.symbol)
    assessment = assess_native_demo_preflight(
        snapshot,
        source_commit=actual_head,
        expected_terminal_path=terminal_path,
    )
    payload = {
        "protocol": "dusty-m1941-native-demo-preflight-report-v1",
        "status": assessment.status.value,
        "blockers": list(assessment.blockers),
        "source_commit": assessment.source_commit,
        "assessment_fingerprint": assessment.fingerprint,
        "snapshot_fingerprint": snapshot.fingerprint,
        "terminal": {
            "path": snapshot.terminal_path,
            "build": snapshot.terminal_build,
            "connected": snapshot.connected,
            "trade_allowed": snapshot.terminal_trade_allowed,
            "tradeapi_disabled": snapshot.tradeapi_disabled,
        },
        "account": {
            "server": snapshot.server,
            "login": snapshot.login,
            "mode": snapshot.account_mode.value,
            "trade_allowed": snapshot.account_trade_allowed,
            "expert_allowed": snapshot.account_expert_allowed,
            "currency": snapshot.account_currency,
            "leverage": snapshot.leverage,
        },
        "symbol": {
            "name": snapshot.symbol,
            "spec_fingerprint": snapshot.symbol_spec_fingerprint,
        },
        "authority": {
            "broker_write": False,
            "live_write": False,
        },
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if assessment.status.value == "ready" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"M194.1 native validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
