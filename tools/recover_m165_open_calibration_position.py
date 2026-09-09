from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from dusty.artifact_vault import ResearchArtifactVault
from dusty.demo_execution import DemoMT5ExecutionAdapter
from dusty.demo_session import DemoSession, MT5IdentityProbe
from dusty.execution_lifecycle import SQLiteExecutionLedger
from dusty.experience import TradeSide
from dusty.m187_calibration_bridge import M187CalibrationExecutionBridge, M187CalibrationPermit
from dusty.m194_native_demo_preflight import NativeDemoPreflightStatus, assess_native_demo_preflight, capture_native_demo_snapshot
from dusty.position_actions import MT5PositionActionPreflightAdapter, PositionActionIntent, PositionActionKind

CONFIRMATION = "M165-DEMO-RECOVERY-CLOSE"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def _atomic_write_json(path: Path, payload: object) -> None:
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


def _deal_payload(row: object) -> dict[str, object]:
    return {
        "ticket": int(getattr(row, "ticket", 0) or 0),
        "order": int(getattr(row, "order", 0) or 0),
        "position_id": int(getattr(row, "position_id", 0) or 0),
        "time_msc": int(getattr(row, "time_msc", 0) or 0),
        "type": int(getattr(row, "type", -1)),
        "entry": int(getattr(row, "entry", -1)),
        "reason": int(getattr(row, "reason", -1)),
        "price": float(getattr(row, "price", 0.0) or 0.0),
        "volume": float(getattr(row, "volume", 0.0) or 0.0),
        "commission": float(getattr(row, "commission", 0.0) or 0.0),
        "fee": float(getattr(row, "fee", 0.0) or 0.0),
        "swap": float(getattr(row, "swap", 0.0) or 0.0),
        "comment": str(getattr(row, "comment", ""))[:128],
    }


def _position_deals(mt5: object, terminal_path: str, position_id: int, symbol: str) -> list[object]:
    if not mt5.initialize(path=terminal_path):
        raise RuntimeError(f"MT5 initialize failed during position reconciliation: {mt5.last_error()}")
    try:
        rows = mt5.history_deals_get(position=position_id)
        if rows is None:
            raise RuntimeError(f"history_deals_get(position=...) failed: {mt5.last_error()}")
        return [row for row in rows if str(getattr(row, "symbol", "")).upper() == symbol.upper() and int(getattr(row, "type", -1)) in (0, 1)]
    finally:
        mt5.shutdown()


def _current_position(mt5: object, terminal_path: str, position_id: int) -> object | None:
    if not mt5.initialize(path=terminal_path):
        raise RuntimeError(f"MT5 initialize failed during position read: {mt5.last_error()}")
    try:
        rows = mt5.positions_get(ticket=position_id)
        if rows is None:
            raise RuntimeError(f"positions_get(ticket=...) failed: {mt5.last_error()}")
        if len(rows) > 1:
            raise RuntimeError("broker returned multiple positions for exact ticket")
        return rows[0] if len(rows) == 1 else None
    finally:
        mt5.shutdown()


def _result_payload(result: object) -> dict[str, object]:
    return {
        "intent_hash": result.intent_hash,
        "state": result.state.value,
        "retcode": int(result.retcode),
        "order_ticket": int(result.order_ticket),
        "deal_ticket": int(result.deal_ticket),
        "comment": str(result.comment)[:256],
    }


def _verify_sha256(value: object, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser(description="One-shot governed recovery close for an already-proven M165 Demo position")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--v3-receipt", required=True)
    parser.add_argument("--custody-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--confirm-demo-write", required=True)
    args = parser.parse_args()

    if args.confirm_demo_write != CONFIRMATION:
        raise PermissionError(f"explicit recovery confirmation must equal {CONFIRMATION}")

    repo = Path(args.repo).resolve()
    expected = args.expected_head.strip().lower()
    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    output_path = Path(args.output).resolve()
    plan = json.loads(Path(args.plan).resolve().read_text(encoding="utf-8"))
    v3 = json.loads(Path(args.v3_receipt).resolve().read_text(encoding="utf-8"))

    if v3.get("protocol") != "dusty-m165-calibration-roundtrip-execution-v3":
        raise ValueError("unexpected V3 receipt protocol")
    if v3.get("status") != "position_open_governed_close_required":
        raise ValueError("V3 receipt does not authorize recovery-close workflow")
    resolution = v3.get("post_send_resolution", {})
    if resolution.get("status") != "position_open":
        raise ValueError("V3 receipt does not prove an open position")
    position_id = int(resolution.get("position_id", 0) or 0)
    entry_deal_ticket = int(resolution.get("entry_deal_ticket", 0) or 0)
    if position_id <= 0 or entry_deal_ticket <= 0:
        raise ValueError("V3 receipt lacks positive position/entry-deal identity")
    if bool(v3.get("authority", {}).get("retry", True)):
        raise PermissionError("V3 receipt unexpectedly grants retry authority")

    if plan.get("protocol") != "dusty-m165-calibration-roundtrip-plan-v1" or plan.get("status") != "planned":
        raise ValueError("unexpected calibration plan")
    plan_fp = _verify_sha256(plan.get("plan_fingerprint"), "plan fingerprint")
    if plan_fp != str(v3.get("plan_fingerprint", plan_fp)).strip().lower() and "plan_fingerprint" in v3:
        raise ValueError("V3 receipt plan identity drift")
    q_plan = _verify_sha256(plan.get("qualification_plan_fingerprint"), "qualification plan")
    q_manifest = _verify_sha256(plan.get("qualification_manifest_fingerprint"), "qualification manifest")
    strategy = _verify_sha256(plan.get("strategy_hash"), "strategy")
    symbol_spec = _verify_sha256(plan.get("symbol_spec_fingerprint"), "symbol specification")
    session_fp = _verify_sha256(plan.get("session_fingerprint"), "Demo session")
    symbol = str(plan.get("symbol", "")).strip().upper()
    selected = plan.get("selected", {})
    volume = float(selected.get("volume_lots", 0.0) or 0.0)
    filling_mode = int(selected.get("filling_mode", -1))
    maximum_loss = float(selected.get("execution_allowed_loss_cash", 0.0) or 0.0)
    if symbol != "EURUSD" or not math.isclose(volume, 0.01, rel_tol=1e-12, abs_tol=1e-12) or filling_mode < 0 or maximum_loss <= 0:
        raise ValueError("recovery plan is outside the bounded EURUSD 0.01 calibration contract")

    started_at = datetime.now(timezone.utc)
    output: dict[str, object] = {
        "protocol": "dusty-m165-calibration-recovery-close-v1",
        "source_commit": expected,
        "position_id": position_id,
        "entry_deal_ticket": entry_deal_ticket,
        "plan_fingerprint": plan_fp,
        "status": "validating",
        "authority": {"demo_write": True, "live_write": False, "promotion": False, "retry": False, "new_entry": False},
        "broker_send_attempted": False,
        "started_at": started_at.isoformat(),
    }

    import MetaTrader5 as mt5

    terminal_path = str(Path(args.terminal_path).resolve())
    snapshot = capture_native_demo_snapshot(mt5, terminal_path=terminal_path, symbol=symbol, captured_at=started_at)
    assessment = assess_native_demo_preflight(snapshot, source_commit=expected, expected_terminal_path=terminal_path)
    output["native_preflight_assessment_fingerprint"] = assessment.fingerprint
    if assessment.status is not NativeDemoPreflightStatus.READY:
        output.update(status="native_preflight_blocked", reason=str(assessment.blockers), completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        return 3
    if snapshot.symbol_spec_fingerprint != symbol_spec:
        output.update(status="symbol_spec_drift", reason="native symbol specification drift", completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        return 3

    probe = MT5IdentityProbe(mt5, terminal_path=terminal_path, symbol_spec_fingerprint=symbol_spec)
    identity = probe.read()
    if identity.fingerprint != session_fp:
        output.update(status="session_drift", reason="Demo session identity drift", completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        return 3
    session = DemoSession(identity)

    before_deals = _position_deals(mt5, terminal_path, position_id, symbol)
    output["position_deals_before"] = [_deal_payload(row) for row in before_deals]
    entry_matches = [row for row in before_deals if int(getattr(row, "ticket", 0) or 0) == entry_deal_ticket and int(getattr(row, "entry", -1)) == 0]
    if len(entry_matches) != 1:
        output.update(status="entry_evidence_drift", reason="exact entry deal no longer reconciles", completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        return 3

    position = _current_position(mt5, terminal_path, position_id)
    if position is None:
        exits = [row for row in before_deals if int(getattr(row, "entry", -1)) in (1, 2, 3)]
        if exits:
            output.update(
                status="already_closed_no_send",
                reason="broker history proves the position closed before recovery execution",
                broker_send_attempted=False,
                exit_deal_ticket=int(getattr(exits[-1], "ticket", 0) or 0),
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            _atomic_write_json(output_path, output)
            print(json.dumps(output, indent=2, sort_keys=True))
            return 0
        output.update(status="position_missing_without_exit_evidence", reason="no open position and no exit deal", completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        return 3

    if str(getattr(position, "symbol", "")).upper() != symbol:
        output.update(status="position_symbol_drift", reason="exact position symbol drift", completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        return 3
    if int(getattr(position, "type", -1)) != 0:
        output.update(status="position_side_drift", reason="expected exact long position", completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        return 3
    current_volume = float(getattr(position, "volume", 0.0) or 0.0)
    if not math.isclose(current_volume, volume, rel_tol=1e-12, abs_tol=1e-12):
        output.update(status="position_volume_drift", reason="position volume no longer equals exact 0.01-lot calibration volume", position_volume=current_volume, completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        return 3

    close_created = datetime.now(timezone.utc)
    close_intent = PositionActionIntent(
        strategy_hash=strategy,
        session_fingerprint=session_fp,
        kind=PositionActionKind.FULL_CLOSE,
        symbol=symbol,
        side=TradeSide.LONG,
        position_ticket=position_id,
        pending_order_ticket=0,
        current_volume=current_volume,
        action_volume=current_volume,
        current_stop=float(getattr(position, "sl", 0.0) or 0.0),
        new_stop=0.0,
        target_price=float(getattr(position, "tp", 0.0) or 0.0),
        pm_approved=True,
        risk_approved=True,
        guardian_approved=True,
        created_at=close_created,
        expires_at=close_created + timedelta(minutes=2),
        filling_mode=filling_mode,
    )
    close_adapter = MT5PositionActionPreflightAdapter(mt5, session, probe.read_connected)
    close_preflight = close_adapter.check(close_intent, at=close_created)
    output["close_preflight"] = {
        "passed": close_preflight.passed,
        "reasons": list(close_preflight.reasons),
        "request_price": float(close_preflight.request_dict().get("price", 0.0)) if close_preflight.request else 0.0,
        "intent_hash": close_intent.intent_hash,
    }
    if not close_preflight.passed:
        output.update(status="full_close_preflight_failed_no_retry", reason=str(close_preflight.reasons), completed_at=datetime.now(timezone.utc).isoformat())
        _atomic_write_json(output_path, output)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 3

    custody_root = Path(args.custody_root).resolve()
    custody_root.mkdir(parents=True, exist_ok=True)
    vault = ResearchArtifactVault(custody_root / "artifact-vault")
    ledger = SQLiteExecutionLedger(custody_root / "execution-ledger.sqlite3")
    producer = _digest(("dusty-m165-recovery-close-v1", expected, position_id, plan_fp))
    adapter = DemoMT5ExecutionAdapter(mt5, session, probe.read_connected, ledger)
    bridge = M187CalibrationExecutionBridge(vault=vault, session=session, adapter=adapter, producer_fingerprint=producer)
    try:
        close_permit = M187CalibrationPermit(
            q_plan,
            q_manifest,
            strategy,
            symbol_spec,
            session_fp,
            symbol,
            close_intent.intent_hash,
            "full_close",
            current_volume,
            maximum_loss,
            2,
            close_created,
            close_created + timedelta(minutes=2),
        )
        output["broker_send_attempted"] = True
        receipt = bridge.execute(
            preflight=close_preflight,
            permit=close_permit,
            current_symbol_spec_fingerprint=symbol_spec,
            at=datetime.now(timezone.utc),
        )
        output["close"] = {
            "permit_fingerprint": close_permit.fingerprint,
            "admission_fingerprint": receipt.admission.fingerprint,
            "admission_artifact_record_fingerprint": receipt.admission_artifact_record_fingerprint,
            "execution": _result_payload(receipt.execution),
        }
    finally:
        ledger.close()
        vault.close()

    after_deals = _position_deals(mt5, terminal_path, position_id, symbol)
    output["position_deals_after"] = [_deal_payload(row) for row in after_deals]
    remaining = _current_position(mt5, terminal_path, position_id)
    exits = [row for row in after_deals if int(getattr(row, "entry", -1)) in (1, 2, 3)]
    if remaining is None and exits:
        output.update(
            status="completed",
            reason="broker history proves exact position fully closed",
            exit_deal_ticket=int(getattr(exits[-1], "ticket", 0) or 0),
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        _atomic_write_json(output_path, output)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0

    output.update(
        status="close_unresolved_no_retry",
        reason="close send occurred but broker history does not yet prove the exact position closed",
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    _atomic_write_json(output_path, output)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
