from __future__ import annotations

"""Run one bounded broker-evidence day of the production M165 calibration campaign.

Day 2: requires 10 observations across exactly one prior broker-evidence date and
advances to 20/2 with at most five new round trips.
Day 3: requires 20 observations across exactly two prior broker-evidence dates and
advances to 30/3 with at most five new round trips, at which point M165 must be
CALIBRATED.

The day boundary is derived from the same MetaTrader symbol tick clock used by the
native evidence rather than the workstation wall clock. Every child is a fresh V3
one-shot. If V3 proves an open position after its bounded read-only grace, this
controller may invoke the existing exact-position recovery close once. Broker
history is independently reconciled before custody import. This controller owns no
raw order_send surface and never retries an entry or close.
"""

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any

from dusty.m165_calibration_campaign import (
    calibration_day_policy,
    validate_campaign_progress,
    validate_campaign_start,
)
from dusty.m165_observation_custody import M165ObservationCustody


SYMBOL = "EURUSD"
CHILD_CONFIRMATION = "M165-DEMO-ONE-SHOT"
RECOVERY_CONFIRMATION = "M165-DEMO-RECOVERY-CLOSE"
MAX_EVIDENCE_CLOCK_OFFSET_SECONDS = 18 * 60 * 60


def _confirmation(day: int) -> str:
    return f"M165-DEMO-DAY{day}-BATCH-5"


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


def _custody_rows(database: Path):
    with M165ObservationCustody(database) as store:
        return store.load_all()


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
            raise PermissionError("M165 calibration campaign requires DEMO account")
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            raise RuntimeError(f"positions_get failed: {mt5.last_error()}")
        if len(positions) != 0:
            tickets = [int(getattr(row, "ticket", 0) or 0) for row in positions]
            raise RuntimeError(f"campaign requires flat {symbol}; open positions={tickets}")
    finally:
        mt5.shutdown()


def _broker_evidence_clock(terminal_path: str, symbol: str) -> tuple[date, str, float]:
    import MetaTrader5 as mt5

    wall = datetime.now(timezone.utc)
    if not mt5.initialize(path=terminal_path):
        raise RuntimeError(f"MT5 initialize failed during broker-clock read: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        if account is None:
            raise RuntimeError("account_info unavailable during broker-clock read")
        demo_mode = int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0))
        if int(getattr(account, "trade_mode", -1)) != demo_mode:
            raise PermissionError("broker-clock read requires DEMO account")
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"symbol_info_tick failed: {mt5.last_error()}")
        time_msc = int(getattr(tick, "time_msc", 0) or 0)
        if time_msc <= 0:
            raise RuntimeError("broker tick lacks positive time_msc")
    finally:
        mt5.shutdown()
    instant = datetime.fromtimestamp(time_msc / 1000.0, tz=timezone.utc)
    offset = (instant - wall).total_seconds()
    if abs(offset) > MAX_EVIDENCE_CLOCK_OFFSET_SECONDS:
        raise RuntimeError(f"broker evidence clock offset is implausible: {offset:.3f}s")
    return instant.date(), instant.isoformat(), offset


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
    campaign_date: date,
    policy,
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

    extracted = json.loads(observations.read_text(encoding="utf-8"))
    obs_rows = extracted.get("observations", [])
    if not isinstance(obs_rows, list) or len(obs_rows) != 2:
        raise RuntimeError("each M165 round trip must extract exactly two observations")
    observed_dates = {
        datetime.fromisoformat(str(row["observed_at"])).astimezone(timezone.utc).date()
        for row in obs_rows
    }
    if observed_dates != {campaign_date}:
        raise RuntimeError("extracted observations are outside the authorized broker-evidence date")

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

    validate_campaign_progress(
        _custody_rows(database),
        policy=policy,
        campaign_date=campaign_date,
        expected_observations=count,
    )

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
    parser = argparse.ArgumentParser(description="Run a bounded M165 production calibration broker-evidence day")
    parser.add_argument("--day", required=True, type=int, choices=(2, 3))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--custody-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--confirm-demo-day", required=True)
    args = parser.parse_args()

    policy = calibration_day_policy(args.day)
    confirmation = _confirmation(args.day)
    if args.confirm_demo_day != confirmation:
        raise PermissionError(f"explicit campaign confirmation must equal {confirmation}")

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
    for path in (qualification_plan, database):
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

    campaign_date, evidence_clock_at_start, clock_offset_seconds = _broker_evidence_clock(terminal_path, SYMBOL)
    validate_campaign_start(_custody_rows(database), policy=policy, campaign_date=campaign_date)
    _require_flat_demo(terminal_path, SYMBOL)
    output_root.mkdir(parents=True, exist_ok=True)
    custody_root.mkdir(parents=True, exist_ok=True)

    start = _custody_summary(database)
    master: dict[str, object] = {
        "protocol": "dusty-m165-calibration-day-controller-v2",
        "source_commit": expected,
        "day_number": policy.day_number,
        "campaign_broker_evidence_date": campaign_date.isoformat(),
        "broker_evidence_clock_at_start": evidence_clock_at_start,
        "broker_evidence_clock_offset_seconds": clock_offset_seconds,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "symbol": SYMBOL,
        "starting_custody": start,
        "target_observations": policy.target_observations,
        "target_distinct_days": policy.target_distinct_days,
        "maximum_roundtrips": policy.maximum_roundtrips,
        "authority": {
            "demo_write": True,
            "live_write": False,
            "promotion": False,
            "retry": False,
            "raw_order_send": False,
            "maximum_new_entries": policy.maximum_roundtrips,
            "maximum_recovery_closes": policy.maximum_roundtrips,
        },
        "status": "running",
        "children": [],
    }
    _atomic_write(summary_path, master)

    try:
        for index in range(1, policy.maximum_roundtrips + 1):
            if _git(repo, "rev-parse", "HEAD").lower() != expected:
                raise RuntimeError("repository HEAD drifted during campaign")
            if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
                raise RuntimeError("repository became dirty during campaign")
            current_date, current_clock, current_offset = _broker_evidence_clock(terminal_path, SYMBOL)
            if current_date != campaign_date:
                master["status"] = "stopped_no_retry"
                master["reason"] = "broker evidence date changed before next child; no send performed"
                master["broker_evidence_clock_at_stop"] = current_clock
                master["broker_evidence_clock_offset_seconds_at_stop"] = current_offset
                _atomic_write(summary_path, master)
                return 3

            before = _custody_summary(database)
            before_count = int(before.get("observation_count", 0) or 0)
            if before_count >= policy.target_observations:
                break
            _require_flat_demo(terminal_path, SYMBOL)

            child_dir = output_root / f"roundtrip-{index:02d}"
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
            children = master["children"]
            assert isinstance(children, list)
            children.append(child_record)
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

            post_plan_date, post_plan_clock, _ = _broker_evidence_clock(terminal_path, SYMBOL)
            if post_plan_date != campaign_date:
                child_record["status"] = "broker_date_changed_after_plan_no_send"
                master["status"] = "stopped_no_retry"
                master["reason"] = "broker evidence date changed after planning; entry not sent"
                master["broker_evidence_clock_at_stop"] = post_plan_clock
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
                master["reason"] = f"roundtrip {index} missing V3 receipt"
                _atomic_write(summary_path, master)
                return 4

            v3_payload = json.loads(v3_receipt.read_text(encoding="utf-8"))
            v3_status = str(v3_payload.get("status", ""))
            child_record["v3_status"] = v3_status
            if v3_exit != 0:
                if v3_status != "position_open_governed_close_required":
                    child_record["status"] = "v3_stopped_no_retry"
                    master["status"] = "stopped_no_retry"
                    master["reason"] = f"roundtrip {index} V3 stopped outside governed-close state"
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
                    child_record["status"] = "recovery_stopped_no_retry"
                    master["status"] = "stopped_no_retry"
                    master["reason"] = f"roundtrip {index} governed recovery close did not complete"
                    _atomic_write(summary_path, master)
                    return 5
                recovery_payload = json.loads(recovery_receipt.read_text(encoding="utf-8"))
                recovery_status = str(recovery_payload.get("status", ""))
                child_record["recovery_status"] = recovery_status
                if recovery_status not in ("completed", "already_closed_no_send"):
                    child_record["status"] = "recovery_unresolved_no_retry"
                    master["status"] = "stopped_no_retry"
                    master["reason"] = f"roundtrip {index} recovery ended in {recovery_status}"
                    _atomic_write(summary_path, master)
                    return 5
            elif v3_status not in ("completed_by_broker_protective_close", "completed"):
                child_record["status"] = "unexpected_v3_success_state"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} unexpected V3 zero-exit state {v3_status}"
                _atomic_write(summary_path, master)
                return 4

            child_receipt = Path(str(v3_payload.get("v2_child_receipt", ""))).resolve()
            if not child_receipt.is_file():
                child_record["status"] = "missing_child_receipt_no_retry"
                master["status"] = "stopped_no_retry"
                master["reason"] = f"roundtrip {index} missing immutable V2 child receipt"
                _atomic_write(summary_path, master)
                return 4

            _require_flat_demo(terminal_path, SYMBOL)
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
                campaign_date=campaign_date,
                policy=policy,
            )
            child_record.update(
                status="completed_and_custodied",
                completed_at=datetime.now(timezone.utc).isoformat(),
                v2_child_receipt=str(child_receipt),
                **harvested,
            )
            _atomic_write(summary_path, master)

        final = _custody_summary(database)
        final_count = int(final.get("observation_count", 0) or 0)
        final_days = int(final.get("distinct_days", 0) or 0)
        if final_count != policy.target_observations or final_days != policy.target_distinct_days:
            raise RuntimeError("campaign ended without exact observation/day target")
        validate_campaign_progress(
            _custody_rows(database),
            policy=policy,
            campaign_date=campaign_date,
            expected_observations=policy.target_observations,
        )
        calibration = final.get("calibration", {})
        calibration_status = str(calibration.get("status", "")) if isinstance(calibration, dict) else ""
        expected_status = "calibrated" if policy.day_number == 3 else "insufficient"
        if calibration_status != expected_status:
            raise RuntimeError(
                f"M165 day {policy.day_number} expected calibration status {expected_status}; found {calibration_status}"
            )
        _require_flat_demo(terminal_path, SYMBOL)
        ending_date, ending_clock, ending_offset = _broker_evidence_clock(terminal_path, SYMBOL)
        if ending_date != campaign_date:
            raise RuntimeError("broker evidence date changed before campaign finalization")
        master.update(
            status=f"day{policy.day_number}_target_complete",
            reason="bounded broker-evidence calibration day completed and entered durable custody",
            completed_at=datetime.now(timezone.utc).isoformat(),
            broker_evidence_clock_at_end=ending_clock,
            broker_evidence_clock_offset_seconds_at_end=ending_offset,
            final_custody=final,
        )
        _atomic_write(summary_path, master)
        print(json.dumps(master, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        master["status"] = "stopped_no_retry"
        master["reason"] = str(exc)
        master["stopped_at"] = datetime.now(timezone.utc).isoformat()
        try:
            master["current_custody"] = _custody_summary(database)
        except Exception as custody_exc:
            master["custody_read_error"] = str(custody_exc)
        _atomic_write(summary_path, master)
        print(json.dumps(master, indent=2, sort_keys=True))
        return 6


if __name__ == "__main__":
    raise SystemExit(main())
