from __future__ import annotations

"""Resume M165 day-one calibration after a safely stopped batch child.

The previously completed child is harvested first without a broker write.  At most
two new V3 round trips are then permitted.  A V3 position-open result may invoke
the existing exact-position recovery-close operator once; broker history is always
reconciled independently before observations enter custody.  This controller owns
no raw order_send surface and never retries an entry or close.
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


CONFIRMATION = "M165-DEMO-DAY1-RESUME-2"
CHILD_CONFIRMATION = "M165-DEMO-ONE-SHOT"
RECOVERY_CONFIRMATION = "M165-DEMO-RECOVERY-CLOSE"
EXPECTED_START_OBSERVATIONS = 4
TARGET_OBSERVATIONS = 10
MAX_NEW_ROUNDTRIPS = 2
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
            raise PermissionError("calibration resume requires DEMO account")
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            raise RuntimeError(f"positions_get failed: {mt5.last_error()}")
        if len(positions) != 0:
            tickets = [int(getattr(row, "ticket", 0) or 0) for row in positions]
            raise RuntimeError(f"resume requires flat {symbol}; open positions={tickets}")
    finally:
        mt5.shutdown()


def _entry_order_ticket(receipt: dict[str, Any]) -> int:
    entry = receipt.get("entry", {})
    execution = entry.get("execution", {}) if isinstance(entry, dict) else {}
    return int(execution.get("order_ticket", 0) or 0) if isinstance(execution, dict) else 0


def _sent_at(receipt: dict[str, Any]) -> str:
    preflight = receipt.get("entry_preflight", {})
    return str(preflight.get("checked_at", "")) if isinstance(preflight, dict) else ""


def _harvest(
    repo: Path,
    *,
    terminal_path: str,
    reconcile: Path,
    extract: Path,
    importer: Path,
    child_receipt: Path,
    database: Path,
    output_dir: Path,
    expected_before: int,
) -> dict[str, object]:
    payload = json.loads(child_receipt.read_text(encoding="utf-8"))
    order_ticket = _entry_order_ticket(payload)
    sent_at = _sent_at(payload)
    if order_ticket <= 0 or not sent_at:
        raise RuntimeError("child receipt lacks exact entry identity")

    output_dir.mkdir(parents=True, exist_ok=True)
    forensics = output_dir / "forensics.json"
    observations = output_dir / "observations.json"
    custody_summary = output_dir / "custody-summary.json"

    forensic_exit = _run(
        repo,
        str(reconcile),
        "--terminal-path", terminal_path,
        "--order-ticket", str(order_ticket),
        "--symbol", SYMBOL,
        "--sent-at", sent_at,
        "--output", str(forensics),
    )
    if forensic_exit not in (0, 2) or not forensics.is_file():
        raise RuntimeError("independent broker-history reconciliation failed")
    forensic_payload = json.loads(forensics.read_text(encoding="utf-8"))
    if forensic_payload.get("assessment") != "broker_execution_evidence_found":
        raise RuntimeError("broker execution evidence is incomplete")

    extract_exit = _run(
        repo,
        str(extract),
        "--terminal-path", terminal_path,
        "--receipt", str(child_receipt),
        "--forensics", str(forensics),
        "--output", str(observations),
    )
    if extract_exit != 0 or not observations.is_file():
        raise RuntimeError("native observation extraction failed")

    import_exit = _run(
        repo,
        str(importer),
        "--input", str(observations),
        "--database", str(database),
        "--summary", str(custody_summary),
    )
    if import_exit != 0 or not custody_summary.is_file():
        raise RuntimeError("custody import failed")

    summary = json.loads(custody_summary.read_text(encoding="utf-8"))
    imported = summary.get("last_import", {})
    inserted = int(imported.get("inserted", -1)) if isinstance(imported, dict) else -1
    duplicates = int(imported.get("duplicates", -1)) if isinstance(imported, dict) else -1
    count = int(summary.get("observation_count", 0) or 0)
    if inserted != 2 or duplicates != 0 or count != expected_before + 2:
        raise RuntimeError("custody did not advance by exactly two unique observations")
    return {
        "order_ticket": order_ticket,
        "forensic_exit": forensic_exit,
        "extract_exit": extract_exit,
        "import_exit": import_exit,
        "ending_observation_count": count,
        "forensics": str(forensics),
        "observations": str(observations),
        "custody_summary": str(custody_summary),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Resume bounded M165 day-one calibration from a completed stopped child")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--custody-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--previous-plan", required=True)
    parser.add_argument("--previous-v3-receipt", required=True)
    parser.add_argument("--previous-child-receipt", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--confirm-demo-resume", required=True)
    args = parser.parse_args()

    if args.confirm_demo_resume != CONFIRMATION:
        raise PermissionError(f"explicit resume confirmation must equal {CONFIRMATION}")

    repo = Path(args.repo).resolve()
    expected = str(args.expected_head).strip().lower()
    terminal_path = str(Path(args.terminal_path).resolve())
    qualification_plan = Path(args.qualification_plan).resolve()
    custody_root = Path(args.custody_root).resolve()
    database = Path(args.database).resolve()
    previous_plan = Path(args.previous_plan).resolve()
    previous_v3 = Path(args.previous_v3_receipt).resolve()
    previous_child = Path(args.previous_child_receipt).resolve()
    output_root = Path(args.output_root).resolve()
    summary_path = Path(args.summary).resolve()

    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")
    for path in (qualification_plan, database, previous_plan, previous_v3, previous_child):
        if not path.is_file():
            raise FileNotFoundError(path)

    planner = repo / "tools" / "plan_m165_calibration_roundtrip.py"
    v3 = repo / "tools" / "execute_m165_calibration_roundtrip_v3.py"
    recovery = repo / "tools" / "recover_m165_open_calibration_position.py"
    reconcile = repo / "tools" / "reconcile_m165_ambiguous_order.py"
    extract = repo / "tools" / "extract_m165_native_observations.py"
    importer = repo / "tools" / "import_m165_observations.py"
    for path in (planner, v3, recovery, reconcile, extract, importer):
        if not path.is_file():
            raise FileNotFoundError(path)

    output_root.mkdir(parents=True, exist_ok=True)
    custody_root.mkdir(parents=True, exist_ok=True)
    start = _custody_summary(database)
    start_count = int(start.get("observation_count", 0) or 0)
    if start_count != EXPECTED_START_OBSERVATIONS:
        raise RuntimeError(f"resume requires custody at {EXPECTED_START_OBSERVATIONS}; found {start_count}")
    if int(start.get("distinct_days", 0) or 0) != 1:
        raise RuntimeError("day-one resume requires exactly one distinct custody day")
    _require_flat_demo(terminal_path, SYMBOL)

    previous_v3_payload = json.loads(previous_v3.read_text(encoding="utf-8"))
    previous_status = str(previous_v3_payload.get("status", ""))
    if previous_status != "position_open_governed_close_required":
        raise RuntimeError("previous V3 receipt is not the expected safely stopped open-position state")

    master: dict[str, object] = {
        "protocol": "dusty-m165-day1-calibration-resume-v2",
        "source_commit": expected,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "target_observations": TARGET_OBSERVATIONS,
        "maximum_new_roundtrips": MAX_NEW_ROUNDTRIPS,
        "starting_custody": start,
        "authority": {
            "demo_write": True,
            "live_write": False,
            "promotion": False,
            "retry": False,
            "raw_order_send": False,
            "maximum_new_entries": MAX_NEW_ROUNDTRIPS,
            "maximum_recovery_closes": MAX_NEW_ROUNDTRIPS,
        },
        "status": "harvesting_previous_child",
        "children": [],
    }
    _atomic_write(summary_path, master)

    try:
        harvested = _harvest(
            repo,
            terminal_path=terminal_path,
            reconcile=reconcile,
            extract=extract,
            importer=importer,
            child_receipt=previous_child,
            database=database,
            output_dir=output_root / "previous-child-harvest",
            expected_before=EXPECTED_START_OBSERVATIONS,
        )
        master["previous_child"] = {
            "plan": str(previous_plan),
            "v3_receipt": str(previous_v3),
            "v2_child_receipt": str(previous_child),
            "status": "completed_and_custodied",
            **harvested,
        }
        master["status"] = "running_new_children"
        _atomic_write(summary_path, master)

        for index in range(1, MAX_NEW_ROUNDTRIPS + 1):
            if _git(repo, "rev-parse", "HEAD").lower() != expected:
                raise RuntimeError("repository HEAD drifted during resume")
            if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
                raise RuntimeError("repository became dirty during resume")

            before = _custody_summary(database)
            before_count = int(before.get("observation_count", 0) or 0)
            if before_count >= TARGET_OBSERVATIONS:
                break
            _require_flat_demo(terminal_path, SYMBOL)

            child_dir = output_root / f"new-roundtrip-{index:02d}"
            child_dir.mkdir(parents=True, exist_ok=True)
            plan = child_dir / "plan.json"
            v3_receipt = child_dir / "v3-receipt.json"
            recovery_receipt = child_dir / "recovery-close.json"

            child_record: dict[str, object] = {
                "roundtrip": index,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "starting_observation_count": before_count,
                "plan": str(plan),
                "v3_receipt": str(v3_receipt),
                "recovery_receipt": str(recovery_receipt),
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
                master["reason"] = f"new roundtrip {index} plan failed"
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
                master["reason"] = f"new roundtrip {index} missing V3 receipt"
                _atomic_write(summary_path, master)
                return 4

            v3_payload = json.loads(v3_receipt.read_text(encoding="utf-8"))
            v3_status = str(v3_payload.get("status", ""))
            child_record["v3_status"] = v3_status
            if v3_exit != 0:
                if v3_status != "position_open_governed_close_required":
                    child_record["status"] = "v3_stopped_no_retry"
                    master["status"] = "stopped_no_retry"
                    master["reason"] = f"new roundtrip {index} V3 stopped outside governed-close state"
                    _atomic_write(summary_path, master)
                    return 4
                recovery_exit = _run(
                    repo,
                    str(recovery),
                    "--repo", str(repo),
                    "--expected-head", expected,
                    "--terminal-path", terminal_path,
                    "--plan", str(plan),
                    "--v3-receipt", str(v3_receipt),
                    "--custody-root", str(custody_root),
                    "--output", str(recovery_receipt),
                    "--confirm-demo-write", RECOVERY_CONFIRMATION,
                )
                child_record["recovery_exit"] = recovery_exit
                if recovery_exit != 0 or not recovery_receipt.is_file():
                    child_record["status"] = "recovery_close_stopped_no_retry"
                    master["status"] = "stopped_no_retry"
                    master["reason"] = f"new roundtrip {index} exact-position recovery did not certify"
                    _atomic_write(summary_path, master)
                    return 4

            child_receipt = Path(str(v3_payload.get("v2_child_receipt", ""))).resolve()
            if not child_receipt.is_file():
                raise RuntimeError(f"new roundtrip {index} V2 child receipt missing")

            harvested = _harvest(
                repo,
                terminal_path=terminal_path,
                reconcile=reconcile,
                extract=extract,
                importer=importer,
                child_receipt=child_receipt,
                database=database,
                output_dir=child_dir / "harvest",
                expected_before=before_count,
            )
            _require_flat_demo(terminal_path, SYMBOL)
            child_record.update(harvested)
            child_record["v2_child_receipt"] = str(child_receipt)
            child_record["status"] = "completed_and_custodied"
            child_record["completed_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write(summary_path, master)

        final = _custody_summary(database)
        master["final_custody"] = final
        master["completed_at"] = datetime.now(timezone.utc).isoformat()
        final_count = int(final.get("observation_count", 0) or 0)
        if final_count != TARGET_OBSERVATIONS:
            master["status"] = "stopped_below_target_no_retry"
            master["reason"] = f"resume ended at {final_count}/{TARGET_OBSERVATIONS} observations"
            _atomic_write(summary_path, master)
            return 4
        master["status"] = "day1_target_complete"
        master["reason"] = "previous child harvested and two bounded new round trips entered durable custody"
        _atomic_write(summary_path, master)
        print(json.dumps(master, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        master["status"] = "exception_stopped_no_retry"
        master["reason"] = str(exc)
        master["completed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(summary_path, master)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
