from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

from dusty.long_running_soak import (
    SoakDisturbanceEvidence,
    SoakDisturbanceKind,
    SoakEvidenceMode,
    SoakRecoveryStatus,
)
from dusty.m200_fault_exercises import (
    exercise_abnormal_broker_condition,
    exercise_data_gap,
    exercise_market_closure,
    exercise_provider_failure,
)
from dusty.m200_soak_evidence import (
    SQLiteSoakEvidenceStore,
    SoakHeartbeatEvidence,
    SoakRunIdentity,
)
from dusty.restart_recovery import RecoveryCheckpoint


def _digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _git_head(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    head = result.stdout.strip().lower()
    if len(head) != 40 or any(ch not in "0123456789abcdef" for ch in head):
        raise RuntimeError("Git did not return an exact SHA-1 HEAD")
    return head


def _git_clean(repo: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return not result.stdout.strip()


def _hash_artifacts(paths: tuple[Path, ...]) -> str:
    rows: list[tuple[str, int, str]] = []
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"required M200 artifact does not exist: {path}")
        payload = path.read_bytes()
        rows.append((str(path.resolve()).lower(), len(payload), sha256(payload).hexdigest()))
    return _digest(tuple(rows))


def _mt5_snapshot(terminal_path: str, symbol: str) -> dict[str, object]:
    import MetaTrader5 as mt5

    if not mt5.initialize(path=terminal_path):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()!r}")
    try:
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        positions = mt5.positions_get()
        orders = mt5.orders_get()
        if terminal is None or account is None or info is None:
            raise RuntimeError("MT5 read-only snapshot is incomplete")

        terminal_identity = _digest((
            str(Path(terminal_path).resolve()).lower(),
            int(getattr(terminal, "build", 0)),
            str(getattr(terminal, "company", "")),
        ))
        account_identity = _digest((
            int(getattr(account, "login", 0)),
            str(getattr(account, "server", "")),
            int(getattr(account, "trade_mode", -1)),
            str(getattr(account, "currency", "")),
            str(getattr(account, "company", "")),
        ))
        state = {
            "connected": bool(getattr(terminal, "connected", False)),
            "terminal_trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
            "tradeapi_disabled": bool(getattr(terminal, "tradeapi_disabled", False)),
            "account_trade_allowed": bool(getattr(account, "trade_allowed", False)),
            "account_trade_expert": bool(getattr(account, "trade_expert", False)),
            "server": str(getattr(account, "server", "")),
            "trade_mode": int(getattr(account, "trade_mode", -1)),
            "balance": float(getattr(account, "balance", 0.0)),
            "equity": float(getattr(account, "equity", 0.0)),
            "margin": float(getattr(account, "margin", 0.0)),
            "margin_free": float(getattr(account, "margin_free", 0.0)),
            "positions_count": None if positions is None else len(positions),
            "orders_count": None if orders is None else len(orders),
            "symbol": symbol.upper(),
            "symbol_trade_mode": int(getattr(info, "trade_mode", -1)),
            "volume_min": float(getattr(info, "volume_min", 0.0)),
            "volume_step": float(getattr(info, "volume_step", 0.0)),
            "bid": None if tick is None else float(tick.bid),
            "ask": None if tick is None else float(tick.ask),
            "tick_time": None if tick is None else int(tick.time),
        }
        if state["positions_count"] is None or state["orders_count"] is None:
            raise RuntimeError(f"MT5 position/order read failed: {mt5.last_error()!r}")
        return {
            "terminal_fingerprint": terminal_identity,
            "account_fingerprint": account_identity,
            "connected": state["connected"],
            "positions_count": state["positions_count"],
            "orders_count": state["orders_count"],
            "state_fingerprint": _digest(state),
            "state": state,
        }
    finally:
        mt5.shutdown()


def _ollama_model_fingerprint(model: str, base_url: str) -> str:
    url = base_url.rstrip("/") + "/api/tags"
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    for row in payload.get("models", []):
        if row.get("name") == model or row.get("model") == model:
            digest = str(row.get("digest", ""))
            if digest.startswith("sha256:"):
                digest = digest[7:]
            if len(digest) == 64 and all(ch in "0123456789abcdefABCDEF" for ch in digest):
                return digest.lower()
            return _digest((model, digest))
    raise RuntimeError(f"Ollama model not found: {model}")


def _append_once(store: SQLiteSoakEvidenceStore, evidence: SoakDisturbanceEvidence) -> None:
    if evidence.kind not in {row.kind for row in store.disturbances()}:
        store.append_disturbance(evidence)


def _record_heartbeat(
    store: SQLiteSoakEvidenceStore,
    *,
    terminal_path: str,
    symbol: str,
    artifact_paths: tuple[Path, ...],
) -> SoakHeartbeatEvidence:
    snapshot = _mt5_snapshot(terminal_path, symbol)
    if not snapshot["connected"]:
        raise RuntimeError("M200 heartbeat requires a connected Demo terminal")
    previous = store.latest_heartbeat()
    heartbeat = SoakHeartbeatEvidence(
        observed_at=datetime.now(timezone.utc),
        process_id=os.getpid(),
        terminal_connected=True,
        terminal_fingerprint=str(snapshot["terminal_fingerprint"]),
        account_fingerprint=str(snapshot["account_fingerprint"]),
        positions_count=int(snapshot["positions_count"]),
        orders_count=int(snapshot["orders_count"]),
        state_fingerprint=str(snapshot["state_fingerprint"]),
        artifact_fingerprint=_hash_artifacts(artifact_paths),
        previous_record_fingerprint=None if previous is None else previous.fingerprint,
    )
    store.append_heartbeat(heartbeat)
    return heartbeat


def command_start(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    if not _git_clean(repo):
        raise RuntimeError("M200 start requires a clean repository")
    head = _git_head(repo)
    if args.expected_head and head != args.expected_head.lower():
        raise RuntimeError(f"M200 exact-head mismatch: expected {args.expected_head}, got {head}")
    artifacts = tuple(Path(value).resolve() for value in args.artifact)
    baseline = _hash_artifacts(artifacts)
    snapshot = _mt5_snapshot(args.terminal_path, args.symbol)
    if not snapshot["connected"]:
        raise RuntimeError("M200 start requires connected MT5")
    if int(snapshot["state"]["trade_mode"]) != 0:
        raise RuntimeError("M200 native soak is Demo-only")

    now = datetime.now(timezone.utc)
    identity = SoakRunIdentity(
        run_id=args.run_id,
        source_commit=head,
        terminal_fingerprint=str(snapshot["terminal_fingerprint"]),
        account_fingerprint=str(snapshot["account_fingerprint"]),
        started_at=now,
        baseline_artifact_fingerprint=baseline,
    )
    store = SQLiteSoakEvidenceStore(args.store)
    try:
        store.initialize(identity)
        heartbeat = _record_heartbeat(
            store,
            terminal_path=args.terminal_path,
            symbol=args.symbol,
            artifact_paths=artifacts,
        )
        print(json.dumps({
            "action": "start",
            "run_id": identity.run_id,
            "source_commit": identity.source_commit,
            "started_at": identity.started_at.isoformat(),
            "heartbeat_fingerprint": heartbeat.fingerprint,
            "store_integrity_ok": store.integrity_ok(),
            "broker_write_authority": False,
        }, indent=2))
    finally:
        store.close()


def command_step(args: argparse.Namespace) -> None:
    repo = Path(args.repo).resolve()
    if not _git_clean(repo):
        raise RuntimeError("M200 step requires a clean repository")
    artifacts = tuple(Path(value).resolve() for value in args.artifact)
    store = SQLiteSoakEvidenceStore(args.store)
    try:
        identity = store.identity()
        head = _git_head(repo)
        if head != identity.source_commit:
            raise RuntimeError("M200 source commit drift during soak")
        previous = store.latest_heartbeat()
        current = _record_heartbeat(
            store,
            terminal_path=args.terminal_path,
            symbol=args.symbol,
            artifact_paths=artifacts,
        )

        # A second independent initialize/shutdown cycle proves that the Python
        # MT5 boundary can reconnect without any broker write.
        reconnect = _mt5_snapshot(args.terminal_path, args.symbol)
        if (
            reconnect["connected"]
            and reconnect["terminal_fingerprint"] == identity.terminal_fingerprint
            and reconnect["account_fingerprint"] == identity.account_fingerprint
        ):
            _append_once(store, SoakDisturbanceEvidence(
                SoakDisturbanceKind.MT5_RECONNECT,
                current.observed_at,
                SoakRecoveryStatus.RECOVERED,
                _digest(("mt5-reconnect", current.fingerprint, reconnect["state_fingerprint"])),
                SoakEvidenceMode.OBSERVED,
            ))
        else:
            raise RuntimeError("MT5 reconnect changed terminal/account identity")

        if previous is not None and previous.process_id != current.process_id:
            source_identity = sha256(identity.source_commit.encode("ascii")).hexdigest()
            session_identity = _digest((identity.terminal_fingerprint, identity.account_fingerprint))
            checkpoint = RecoveryCheckpoint(
                source_commit=source_identity,
                session_fingerprint=session_identity,
                execution_intent_hashes=(),
                created_at=previous.observed_at,
            )
            checkpoint.validate_runtime(
                source_commit=source_identity,
                session_fingerprint=session_identity,
            )
            _append_once(store, SoakDisturbanceEvidence(
                SoakDisturbanceKind.PROCESS_RESTART,
                current.observed_at,
                SoakRecoveryStatus.RECOVERED,
                _digest(("m190-process-restart", checkpoint.fingerprint, current.fingerprint)),
                SoakEvidenceMode.OBSERVED,
            ))

        if args.exercise_controlled:
            model_fp = _ollama_model_fingerprint(args.ollama_model, args.ollama_url)
            for evidence in (
                exercise_provider_failure(
                    at=current.observed_at,
                    provider_id="ollama-qwen",
                    model_identity_fingerprint=model_fp,
                ),
                exercise_data_gap(at=current.observed_at),
                exercise_market_closure(at=current.observed_at),
                exercise_abnormal_broker_condition(at=current.observed_at),
            ):
                _append_once(store, evidence)

        print(json.dumps({
            "action": "step",
            "observed_at": current.observed_at.isoformat(),
            "heartbeat_count": len(store.heartbeats()),
            "process_restart_observed": store.process_restart_observed(),
            "disturbances": [
                {"kind": row.kind.value, "mode": row.mode.value, "status": row.recovery_status.value}
                for row in store.disturbances()
            ],
            "store_integrity_ok": store.integrity_ok(),
            "broker_write_authority": False,
        }, indent=2))
    finally:
        store.close()


def command_summary(args: argparse.Namespace) -> None:
    store = SQLiteSoakEvidenceStore(args.store)
    try:
        identity = store.identity()
        rows = store.heartbeats()
        disturbances = store.disturbances()
        ended_at = rows[-1].observed_at if rows else identity.started_at
        duration = max(0.0, (ended_at - identity.started_at).total_seconds())
        print(json.dumps({
            "action": "summary",
            "run_id": identity.run_id,
            "source_commit": identity.source_commit,
            "started_at": identity.started_at.isoformat(),
            "ended_at": ended_at.isoformat(),
            "duration_seconds": duration,
            "heartbeat_count": len(rows),
            "distinct_process_ids": len({row.process_id for row in rows}),
            "process_restart_observed": store.process_restart_observed(),
            "disturbances": [
                {
                    "kind": row.kind.value,
                    "mode": row.mode.value,
                    "recovery_status": row.recovery_status.value,
                    "fingerprint": row.fingerprint,
                }
                for row in disturbances
            ],
            "store_integrity_ok": store.integrity_ok(),
            "artifact_matches_baseline": bool(rows) and rows[-1].artifact_fingerprint == identity.baseline_artifact_fingerprint,
            "last_positions_count": None if not rows else rows[-1].positions_count,
            "last_orders_count": None if not rows else rows[-1].orders_count,
            "broker_write_authority": False,
        }, indent=2))
    finally:
        store.close()


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="M200 read-only prolonged Demo soak evidence runner")
    sub = root.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--repo", required=True)
    common.add_argument("--store", required=True)
    common.add_argument("--terminal-path", required=True)
    common.add_argument("--symbol", default="EURUSD")
    common.add_argument("--artifact", action="append", required=True)

    start = sub.add_parser("start", parents=[common])
    start.add_argument("--run-id", required=True)
    start.add_argument("--expected-head")
    start.set_defaults(func=command_start)

    step = sub.add_parser("step", parents=[common])
    step.add_argument("--exercise-controlled", action="store_true")
    step.add_argument("--ollama-model", default="qwen3.5:4b")
    step.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    step.set_defaults(func=command_step)

    summary = sub.add_parser("summary")
    summary.add_argument("--store", required=True)
    summary.set_defaults(func=command_summary)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        args.func(args)
    except Exception as exc:
        print(f"M200 ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
