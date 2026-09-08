from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

from dusty.champion_registry import FrozenChampionRegistry
from dusty.m194_native_demo_journal import M194NativeEventKind, SQLiteM194NativeEvidenceJournal
from dusty.m194_native_demo_preflight import assess_native_demo_preflight, capture_native_demo_snapshot
from dusty.m194_native_demo_runtime import M194NativeRunIdentity, record_native_demo_heartbeat, start_native_demo_run


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


def _checked_head(repo: Path, expected: str) -> str:
    head = _git(repo, "rev-parse", "HEAD").lower()
    if head != expected.strip().lower():
        raise RuntimeError(f"M194.1 exact-head mismatch: expected {expected}, got {head}")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("M194.1 native runtime requires a clean worktree")
    return head


def _mt5():
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is required for M194.1 native runtime") from exc
    return mt5


def _capture(args: argparse.Namespace, head: str):
    terminal_path = str(Path(args.terminal_path).resolve())
    snapshot = capture_native_demo_snapshot(
        _mt5(), terminal_path=terminal_path, symbol=args.symbol,
    )
    assessment = assess_native_demo_preflight(
        snapshot, source_commit=head, expected_terminal_path=terminal_path,
    )
    return snapshot, assessment


def _load_identity(journal: SQLiteM194NativeEvidenceJournal, run_id: str) -> M194NativeRunIdentity:
    rows = journal.events(run_id)
    starts = [row for row in rows if row.kind is M194NativeEventKind.RUN_STARTED]
    if len(starts) != 1:
        raise RuntimeError("M194.1 journal requires exactly one RUN_STARTED event")
    start = starts[0]
    payload = start.payload
    identity = M194NativeRunIdentity(
        run_id=start.run_id,
        lane_id=str(payload["lane_id"]),
        champion_fingerprint=start.champion_fingerprint,
        source_commit=start.source_commit,
        preflight_fingerprint=str(payload["preflight_fingerprint"]),
        initial_snapshot_fingerprint=str(payload["snapshot_fingerprint"]),
        terminal_fingerprint=str(payload["terminal_fingerprint"]),
        account_fingerprint=str(payload["account_fingerprint"]),
        symbol_spec_fingerprint=str(payload["symbol_spec_fingerprint"]),
        started_at=start.occurred_at,
    )
    if identity.fingerprint != str(payload["run_identity_fingerprint"]):
        raise RuntimeError("M194.1 persisted run identity fingerprint mismatch")
    return identity


def _payload_base(action: str, head: str, run_id: str) -> dict[str, object]:
    return {
        "protocol": "dusty-m1941-native-runtime-report-v1",
        "action": action,
        "source_commit": head,
        "run_id": run_id,
        "authority": {"broker_write": False, "live_write": False},
    }


def command_start(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    head = _checked_head(repo, args.expected_head)
    registry_path = Path(args.registry).resolve()
    if not registry_path.is_file():
        raise FileNotFoundError(f"M185 registry does not exist: {registry_path}")
    snapshot, assessment = _capture(args, head)
    if assessment.status.value != "ready":
        payload = _payload_base("start", head, args.run_id)
        payload.update({
            "status": "blocked",
            "blockers": list(assessment.blockers),
            "assessment_fingerprint": assessment.fingerprint,
            "snapshot_fingerprint": snapshot.fingerprint,
        })
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2

    registry = FrozenChampionRegistry(registry_path)
    journal = SQLiteM194NativeEvidenceJournal(Path(args.store).resolve())
    try:
        identity = start_native_demo_run(
            registry=registry,
            journal=journal,
            lane_id=args.lane,
            run_id=args.run_id,
            source_commit=head,
            snapshot=snapshot,
            assessment=assessment,
            started_at=datetime.now(timezone.utc),
        )
        payload = _payload_base("start", head, identity.run_id)
        payload.update({
            "status": "started",
            "lane_id": identity.lane_id,
            "champion_fingerprint": identity.champion_fingerprint,
            "run_identity_fingerprint": identity.fingerprint,
            "assessment_fingerprint": assessment.fingerprint,
            "snapshot_fingerprint": snapshot.fingerprint,
            "journal": str(Path(args.store).resolve()),
            "summary": journal.summary(identity.run_id),
        })
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0
    finally:
        registry.close()


def command_heartbeat(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    head = _checked_head(repo, args.expected_head)
    journal = SQLiteM194NativeEvidenceJournal(Path(args.store).resolve())
    identity = _load_identity(journal, args.run_id)
    if identity.source_commit != head:
        raise RuntimeError("M194.1 source commit drift since run start")
    snapshot, assessment = _capture(args, head)
    if assessment.status.value != "ready":
        payload = _payload_base("heartbeat", head, identity.run_id)
        payload.update({
            "status": "blocked",
            "blockers": list(assessment.blockers),
            "assessment_fingerprint": assessment.fingerprint,
            "snapshot_fingerprint": snapshot.fingerprint,
            "summary": journal.summary(identity.run_id),
        })
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    heartbeat = record_native_demo_heartbeat(
        journal=journal,
        identity=identity,
        snapshot=snapshot,
        assessment=assessment,
        observed_at=datetime.now(timezone.utc),
    )
    payload = _payload_base("heartbeat", head, identity.run_id)
    payload.update({
        "status": "recorded",
        "heartbeat_fingerprint": heartbeat,
        "assessment_fingerprint": assessment.fingerprint,
        "snapshot_fingerprint": snapshot.fingerprint,
        "summary": journal.summary(identity.run_id),
    })
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def command_summary(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    head = _checked_head(repo, args.expected_head)
    journal = SQLiteM194NativeEvidenceJournal(Path(args.store).resolve())
    identity = _load_identity(journal, args.run_id)
    payload = _payload_base("summary", head, identity.run_id)
    payload.update({
        "status": "ok",
        "lane_id": identity.lane_id,
        "champion_fingerprint": identity.champion_fingerprint,
        "run_identity_fingerprint": identity.fingerprint,
        "started_at": identity.started_at.isoformat(),
        "summary": journal.summary(identity.run_id),
    })
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="M194.1 native Demo runtime operator")
    sub = root.add_subparsers(dest="command", required=True)
    for name in ("start", "heartbeat", "summary"):
        command = sub.add_parser(name)
        command.add_argument("--repo", required=True)
        command.add_argument("--expected-head", required=True)
        command.add_argument("--store", required=True)
        command.add_argument("--run-id", required=True)
        if name in {"start", "heartbeat"}:
            command.add_argument("--terminal-path", required=True)
            command.add_argument("--symbol", default="EURUSD")
        if name == "start":
            command.add_argument("--registry", required=True)
            command.add_argument("--lane", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    return {
        "start": command_start,
        "heartbeat": command_heartbeat,
        "summary": command_summary,
    }[args.command](args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"M194.1 native runtime failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
