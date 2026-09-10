from __future__ import annotations

"""Supervise the certified V2 one-shot Demo operator with broker-history reconciliation.

V3 never owns broker send authority. It invokes the existing V2 operator, preserves
its child receipt, and if V2 stops on an accepted result without an immediate deal
ticket, V3 performs read-only exact-ticket reconciliation before classifying the
run. Explicit broker rejection is terminal and is never mislabeled as ambiguous
acceptance. Ambiguity never grants retry authority.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable

from dusty.m165_broker_forensics import capture_broker_forensics
from dusty.m165_post_send_resolution import PostSendStatus, resolve_post_send_forensics


CONFIRMATION = "M165-DEMO-ONE-SHOT"
RECONCILIATION_ATTEMPTS = 7
RECONCILIATION_INTERVAL_SECONDS = 0.5


def _atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=path.parent, delete=False) as handle:
        handle.write(rendered)
        handle.flush()
        temporary = Path(handle.name)
    temporary.replace(path)


def _parse_time(value: object) -> datetime:
    rendered = str(value).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(rendered)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("receipt timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _entry_execution(receipt: dict[str, object]) -> dict[str, object]:
    entry = receipt.get("entry", {})
    if not isinstance(entry, dict):
        return {}
    execution = entry.get("execution", {})
    return execution if isinstance(execution, dict) else {}


def _entry_order_ticket(receipt: dict[str, object]) -> int:
    return int(_entry_execution(receipt).get("order_ticket", 0) or 0)


def _reconcile_with_grace(
    capture_once: Callable[[], Any],
    *,
    attempts: int = RECONCILIATION_ATTEMPTS,
    interval_seconds: float = RECONCILIATION_INTERVAL_SECONDS,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[Any, Any, int]:
    """Poll broker history only; never resubmit or mutate broker state."""
    if isinstance(attempts, bool) or attempts < 1 or attempts > 20:
        raise ValueError("reconciliation attempts must be between 1 and 20")
    if interval_seconds < 0 or interval_seconds > 2:
        raise ValueError("reconciliation interval must be between 0 and 2 seconds")

    last_forensic = None
    last_resolution = None
    for attempt in range(1, attempts + 1):
        forensic = capture_once()
        resolution = resolve_post_send_forensics(forensic.payload)
        last_forensic = forensic
        last_resolution = resolution
        if resolution.status is PostSendStatus.COMPLETED_PROTECTIVE_CLOSE:
            return forensic, resolution, attempt
        if attempt < attempts:
            sleeper(interval_seconds)
    return last_forensic, last_resolution, attempts


def main() -> int:
    parser = argparse.ArgumentParser(description="Broker-history reconciled M165 Demo round trip supervisor")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--calibration-plan", required=True)
    parser.add_argument("--custody-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--confirm-demo-write", required=True)
    args = parser.parse_args()

    if args.confirm_demo_write != CONFIRMATION:
        raise PermissionError(f"explicit Demo confirmation must equal {CONFIRMATION}")

    repo = Path(args.repo).resolve()
    final_path = Path(args.output).resolve()
    child_path = final_path.with_name(final_path.stem + ".v2-child.json")
    v2 = repo / "tools" / "execute_m165_calibration_roundtrip_v2.py"
    if not v2.is_file():
        raise FileNotFoundError(v2)

    command = [
        sys.executable,
        str(v2),
        "--repo", str(repo),
        "--expected-head", str(args.expected_head),
        "--terminal-path", str(args.terminal_path),
        "--calibration-plan", str(args.calibration_plan),
        "--custody-root", str(args.custody_root),
        "--output", str(child_path),
        "--confirm-demo-write", CONFIRMATION,
    ]
    proc = subprocess.run(command, cwd=repo, check=False)
    if not child_path.is_file():
        raise RuntimeError(f"V2 did not produce a child receipt; exit={proc.returncode}")

    child = json.loads(child_path.read_text(encoding="utf-8"))
    output: dict[str, object] = {
        "protocol": "dusty-m165-calibration-roundtrip-execution-v3",
        "source_commit": str(args.expected_head).strip().lower(),
        "v2_exit_code": int(proc.returncode),
        "v2_child_receipt": str(child_path),
        "v2_status": str(child.get("status", "unknown")),
        "authority": {
            "demo_write": True,
            "live_write": False,
            "promotion": False,
            "retry": False,
        },
        "broker_send_attempted": bool(child.get("broker_send_attempted", False)),
        "started_at": child.get("started_at"),
    }

    if proc.returncode == 0:
        output["status"] = str(child.get("status", "completed"))
        output["reason"] = "V2 completed without requiring supervisory reconciliation"
        output["completed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(final_path, output)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    if str(child.get("status", "")) != "entry_not_filled_no_retry":
        output["status"] = "v2_blocked_no_retry"
        output["reason"] = f"V2 stopped in {child.get('status', 'unknown')}"
        output["completed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(final_path, output)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 3

    execution = _entry_execution(child)
    order_ticket = _entry_order_ticket(child)
    if order_ticket <= 0:
        state = str(execution.get("state", "")).strip().lower()
        retcode = int(execution.get("retcode", 0) or 0)
        comment = str(execution.get("comment", ""))[:256]
        if state == "rejected":
            output["status"] = "entry_rejected_no_retry"
            output["reason"] = f"broker rejected entry before creating an order; retcode={retcode}; comment={comment}"
        else:
            output["status"] = "accepted_without_order_ticket_no_retry"
            output["reason"] = "V2 non-rejected state carried no positive broker order ticket"
        output["completed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(final_path, output)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 3

    sent_at = _parse_time(child.get("entry_preflight", {}).get("checked_at", child.get("started_at")))

    import MetaTrader5 as mt5

    terminal_path = str(Path(args.terminal_path).resolve())
    if not mt5.initialize(path=terminal_path):
        raise RuntimeError(f"MT5 initialize failed for V3 reconciliation: {mt5.last_error()}")
    try:
        def capture_once() -> Any:
            return capture_broker_forensics(
                mt5,
                order_ticket=order_ticket,
                symbol=str(child.get("symbol", "")),
                sent_at=sent_at,
                captured_at=datetime.now(timezone.utc),
            )

        forensic, resolution, reconciliation_attempts = _reconcile_with_grace(capture_once)
    finally:
        mt5.shutdown()

    if forensic is None or resolution is None:
        raise RuntimeError("V3 reconciliation produced no broker state")

    output["forensic_fingerprint"] = forensic.fingerprint
    output["forensic_assessment"] = forensic.payload["assessment"]
    output["reconciliation_attempts"] = reconciliation_attempts
    output["reconciliation_grace_seconds"] = (RECONCILIATION_ATTEMPTS - 1) * RECONCILIATION_INTERVAL_SECONDS
    output["post_send_resolution"] = {
        "status": resolution.status.value,
        "position_id": resolution.position_id,
        "entry_deal_ticket": resolution.entry_deal_ticket,
        "exit_deal_ticket": resolution.exit_deal_ticket,
        "reasons": list(resolution.reasons),
        "authority": {"broker_write": False, "retry": False, "live_write": False},
    }

    if resolution.status is PostSendStatus.COMPLETED_PROTECTIVE_CLOSE:
        output["status"] = "completed_by_broker_protective_close"
        output["reason"] = "exact broker history proves entry and exit deals with no open position"
        code = 0
    elif resolution.status is PostSendStatus.POSITION_OPEN:
        output["status"] = "position_open_governed_close_required"
        output["reason"] = "entry fill remains open after bounded read-only reconciliation grace; no retry is permitted"
        code = 4
    elif resolution.status is PostSendStatus.FILLED_POSITION_MISSING:
        output["status"] = "filled_position_state_incomplete_no_retry"
        output["reason"] = "entry deal is proven but neither open-position nor exit evidence is complete after reconciliation grace"
        code = 4
    elif resolution.status is PostSendStatus.ORDER_ONLY:
        output["status"] = "order_only_no_retry"
        output["reason"] = "broker order evidence exists without independently verified deal evidence after reconciliation grace"
        code = 4
    else:
        output["status"] = "ambiguous_send_no_retry"
        output["reason"] = "no independently verifiable broker execution evidence after reconciliation grace"
        code = 4

    output["completed_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write(final_path, output)
    print(json.dumps(output, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
