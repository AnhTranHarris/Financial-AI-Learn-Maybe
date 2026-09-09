from __future__ import annotations

"""Run a bounded sequential M165 day-one calibration batch through V3.

This controller owns no raw broker-send surface. A single explicit batch consent
authorizes at most three distinct one-shot V3 round trips. Each child receives a
fresh plan, must reconcile to a completed broker lifecycle, must yield exactly two
new custody observations, and must leave EURUSD flat before the next child starts.
Any ambiguity, duplicate, count drift, or nonzero child result stops the batch.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from dusty.m165_observation_custody import M165ObservationCustody


CONFIRMATION = "M165-DEMO-DAY1-BATCH-3"
CHILD_CONFIRMATION = "M165-DEMO-ONE-SHOT"
MAX_ROUNDTRIPS = 3
TARGET_OBSERVATIONS = 10
EXPECTED_START_OBSERVATIONS = 4
SYMBOL = "EURUSD"


def _atomic_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def _run(repo: Path, *args: str) -> int:
    return int(subprocess.run([sys.executable, *args], cwd=repo, check=False).returncode)


def _custody_summary(database: Path) -> dict[str, object]:
    with M165ObservationCustody(database) as store:
        return store.summary_payload()


def _require_flat_demo(terminal_path: str, symbol: str) -> None:
    import MetaTrader5 as mt5

    if not mt5.initialize(path=terminal_path):
        raise RuntimeError(f"MT5 initialize failed during flat-state check: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError("account_info unavailable during flat-state check")
        demo_mode = int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0))
        if int(getattr(account, "trade_mode", -1)) != demo_mode:
            raise PermissionError("calibration batch requires DEMO account")
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            raise RuntimeError(f"positions_get failed: {mt5.last_error()}")
        if len(positions) != 0:
            tickets = [int(getattr(row, "ticket", 0) or 0) for row in positions]
            raise RuntimeError(f"batch requires flat {symbol}; open positions={tickets}")
    finally:
        mt5.shutdown()


def _entry_order_ticket(receipt: dict[str, Any]) -> int:
    entry = receipt.get("entry", {})
    execution = entry.get("execution", {}) if isinstance(entry, dict) else {}
    return int(execution.get("order_ticket", 0) or 0) if isinstance(execution, dict) else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Bounded M165 day-one calibration batch through V3")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--custody-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--confirm-demo-batch", required=True)
    args = parser.parse_args()

    if args.confirm_demo_batch != CONFIRMATION:
        raise PermissionError(f"explicit batch confirmation must equal {CONFIRMATION}")

    repo = Path(args.repo).resolve()
    expected = str(args.expected_head).strip().lower()
    terminal_path = str(Path(args.terminal_path).resolve())
    qualification_plan = Path(args.qualification_plan).resolve()
    custody_root = Path(args.custody_root).resolve()
    database = Path(args.database).resolve()
    output_root = Path(args.output_root).resolve()
    summary_path = Path(args.summary).resolve()

    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")
    if not qualification_plan.is_file():
        raise FileNotFoundError(qualification_plan)
    if not database.is_file():
        raise FileNotFoundError(database)

    planner = repo / "tools" / "plan_m165_calibration_roundtrip.py"
    v3 = repo / "tools" / "execute_m165_calibration_roundtrip_v3.py"
    reconcile = repo / "tools" / "reconcile_m165_ambiguous_order.py"
    extract = repo / "tools" / "extract_m165_native_observations.py"
    importer = repo / "tools" / "import_m165_observations.py"
    for path in (planner, v3, reconcile, extract, importer):
        if not path.is_file():
            raise FileNotFoundError(path)

    output_root.mkdir(parents=True, exist_ok=True)
    custody_root.mkdir(parents=True, exist_ok=True)
    start = _custody_summary(database)
    start_count = int(start.get("observation_count", 0) or 0)
    start_days = int(start.get("distinct_days", 0) or 0)
    if start_count != EXPECTED_START_OBSERVATIONS:
        raise RuntimeError(f"day-one batch requires custody at {EXPECTED_START_OBSERVATIONS}; found {start_count}")
    if start_days != 1:
        raise RuntimeError(f"day-one batch requires one distinct day; found {start_days}")
    if set(start.get("sides", [])) != {"buy", "sell"}:
        raise RuntimeError("day-one batch requires existing BUY and SELL custody")

    master: dict[str, object] = {
        "protocol": "dusty-m165-day1-calibration-batch-v1",
        "source_commit": expected,
        "symbol": SYMBOL,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "maximum_roundtrips": MAX_ROUNDTRIPS,
        "target_observations": TARGET_OBSERVATIONS,
        "starting_custody": start,
        "children": [],
        "authority": {
            "demo_write": True,
            "live_write": False,
            "promotion": False,
            "retry": False,
            "raw_order_send": False,
        },
        "status": "running",
    }
    _atomic_write(summary_path, master)

    try:
        for index in range(1, MAX_ROUNDTRIPS + 1):
            if _git(repo, "rev-parse", "HEAD").lower() != expected:
                raise RuntimeError("repository HEAD drifted during calibration batch")
            if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
                raise RuntimeError("repository became dirty during calibration batch")

            before = _custody_summary(database)
            before_count = int(before.get("observation_count", 0) or 0)
            if before_count >= TARGET_OBSERVATIONS:
                break
            _require_flat_demo(terminal_path, SYMBOL)

            child_dir = output_root / f"roundtrip-{index:02d}"
            child_dir.mkdir(parents=True, exist_ok=True)
            plan = child_dir / "plan.json"
            v3_receipt = child_dir / "v3-receipt.json"
            forensics = child_dir / "forensics.json"
            observations = child_dir / "observations.json"
            custody_summary = child_dir / "custody-summary.json"

            child_record: dict[str, object] = {
                "roundtrip": index,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "starting_observation_count": before_count,
                "plan": str(plan),
                "v3_receipt": str(v3_receipt),
                "forensics": str(forensics),
                "observations": str(observations),
                "custody_summary": str(custody_summary),
                "status": "planning",
            }
            master["children"].append(child_record)  # type: ignore[union-attr]
            _atomic_write(summary_path, master)

            plan_exit = _run(
                repo,
                str(planner),
                "--repo", str(repo),
                "--expected-head", expected,
                "--terminal-path", terminal_path,
                "--qualification-plan", str(qualification_plan),
                "--symbol", SYMBOL,
                "--output", str(plan),
            )
            child_record["plan_exit"] = plan_exit
            if plan_exit != 0:
                child_record["status"] = "plan_failed_no_send"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} plan failed"
                _atomic_write(summary_path, master)
                return 3

            v3_exit = _run(
                repo,
                str(v3),
                "--repo", str(repo),
                "--expected-head", expected,
                "--terminal-path", terminal_path,
                "--calibration-plan", str(plan),
                "--custody-root", str(custody_root),
                "--output", str(v3_receipt),
                "--confirm-demo-write", CHILD_CONFIRMATION,
            )
            child_record["v3_exit"] = v3_exit
            if not v3_receipt.is_file():
                child_record["status"] = "missing_v3_receipt_no_retry"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} did not produce V3 receipt"
                _atomic_write(summary_path, master)
                return 4

            v3_payload = json.loads(v3_receipt.read_text(encoding="utf-8"))
            child_record["v3_status"] = str(v3_payload.get("status", "unknown"))
            if v3_exit != 0 or str(v3_payload.get("status", "")) not in {"completed", "completed_by_broker_protective_close"}:
                child_record["status"] = "v3_stopped_no_retry"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} V3 did not certify completion"
                _atomic_write(summary_path, master)
                return 4

            child_receipt = Path(str(v3_payload.get("v2_child_receipt", ""))).resolve()
            if not child_receipt.is_file():
                raise RuntimeError(f"roundtrip {index} V2 child receipt missing")
            child_payload = json.loads(child_receipt.read_text(encoding="utf-8"))
            order_ticket = _entry_order_ticket(child_payload)
            if order_ticket <= 0:
                raise RuntimeError(f"roundtrip {index} has no positive entry order ticket")
            entry_preflight = child_payload.get("entry_preflight", {})
            sent_at = str(entry_preflight.get("checked_at", "")) if isinstance(entry_preflight, dict) else ""
            if not sent_at:
                raise RuntimeError(f"roundtrip {index} has no entry preflight timestamp")
            child_record["entry_order_ticket"] = order_ticket
            child_record["v2_child_receipt"] = str(child_receipt)

            forensic_exit = _run(
                repo,
                str(reconcile),
                "--terminal-path", terminal_path,
                "--order-ticket", str(order_ticket),
                "--symbol", SYMBOL,
                "--sent-at", sent_at,
                "--output", str(forensics),
            )
            child_record["forensic_exit"] = forensic_exit
            if forensic_exit not in (0, 2) or not forensics.is_file():
                child_record["status"] = "forensics_failed_no_retry"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} independent forensics failed"
                _atomic_write(summary_path, master)
                return 4
            forensic_payload = json.loads(forensics.read_text(encoding="utf-8"))
            if forensic_payload.get("assessment") != "broker_execution_evidence_found":
                child_record["status"] = "forensics_incomplete_no_retry"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} lacks independent execution evidence"
                _atomic_write(summary_path, master)
                return 4

            extract_exit = _run(
                repo,
                str(extract),
                "--terminal-path", terminal_path,
                "--receipt", str(child_receipt),
                "--forensics", str(forensics),
                "--output", str(observations),
            )
            child_record["extract_exit"] = extract_exit
            if extract_exit != 0 or not observations.is_file():
                child_record["status"] = "observation_extraction_failed"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} observation extraction failed"
                _atomic_write(summary_path, master)
                return 4

            import_exit = _run(
                repo,
                str(importer),
                "--input", str(observations),
                "--database", str(database),
                "--summary", str(custody_summary),
            )
            child_record["import_exit"] = import_exit
            if import_exit != 0 or not custody_summary.is_file():
                child_record["status"] = "custody_import_failed"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} custody import failed"
                _atomic_write(summary_path, master)
                return 4

            after = json.loads(custody_summary.read_text(encoding="utf-8"))
            expected_after = before_count + 2
            imported = after.get("last_import", {})
            inserted = int(imported.get("inserted", -1)) if isinstance(imported, dict) else -1
            duplicates = int(imported.get("duplicates", -1)) if isinstance(imported, dict) else -1
            after_count = int(after.get("observation_count", 0) or 0)
            if inserted != 2 or duplicates != 0 or after_count != expected_after:
                child_record["status"] = "custody_count_drift"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} custody did not advance by exactly two unique observations"
                _atomic_write(summary_path, master)
                return 4

            _require_flat_demo(terminal_path, SYMBOL)
            child_record["status"] = "completed_and_custodied"
            child_record["completed_at"] = datetime.now(timezone.utc).isoformat()
            child_record["ending_observation_count"] = after_count
            _atomic_write(summary_path, master)

        final = _custody_summary(database)
        final_count = int(final.get("observation_count", 0) or 0)
        master["final_custody"] = final
        master["completed_at"] = datetime.now(timezone.utc).isoformat()
        if final_count != TARGET_OBSERVATIONS:
            master["status"] = "stopped_below_target_no_retry"
            master["reason"] = f"batch ended at {final_count}/{TARGET_OBSERVATIONS} observations"
            _atomic_write(summary_path, master)
            return 4
        master["status"] = "day1_target_complete"
        master["reason"] = "three bounded round trips completed and each pair entered durable custody"
        _atomic_write(summary_path, master)
        print(json.dumps(master, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        master["status"] = "failed_closed_no_retry"
        master["reason"] = f"{type(exc).__name__}: {exc}"
        master["completed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(summary_path, master)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
