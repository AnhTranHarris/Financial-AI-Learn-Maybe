from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from dusty.m165_native_history_discovery import discover_native_history


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed with exit {proc.returncode}")
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M165 native history discovery")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--output")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = args.expected_head.strip().lower()
    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    actual = _git(repo, "rev-parse", "HEAD").lower()
    if actual != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    plan_path = Path(args.qualification_plan).resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if plan.get("protocol") != "dusty-m185-production-qualification-bootstrap-v1":
        raise ValueError("qualification plan protocol mismatch")
    plan_source = str(plan.get("source_commit", "")).lower()
    # The plan is immutable evidence created by an earlier exact head. A later
    # discovery head may consume it, but never rewrite its source identity.
    if len(plan_source) != 40:
        raise ValueError("qualification plan source commit is invalid")
    manifests = plan.get("manifests")
    if not isinstance(manifests, list) or not manifests:
        raise ValueError("qualification plan has no candidate manifests")
    symbols = tuple(sorted({str(row["symbol"]).upper() for row in manifests}))

    import MetaTrader5 as mt5

    initialized = False
    try:
        if not mt5.initialize(path=str(Path(args.terminal_path).resolve())):
            raise RuntimeError("MetaTrader5 initialize failed")
        initialized = True
        report = discover_native_history(
            mt5,
            symbols=symbols,
            captured_at=datetime.now(timezone.utc),
            lookback_days=args.lookback_days,
        )
        payload = dict(report.payload)
        payload["fingerprint"] = report.fingerprint
        payload["source_commit"] = expected
        payload["qualification_plan_fingerprint"] = plan.get("plan_fingerprint")
        payload["qualification_plan_source_commit"] = plan_source
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        print(rendered)
        if args.output:
            destination = Path(args.output).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered + "\n", encoding="utf-8")
        return 0 if report.demo_account and report.connected else 2
    finally:
        if initialized:
            mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
