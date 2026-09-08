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
from dusty.execution_lifecycle import ExecutionState, SQLiteExecutionLedger
from dusty.experience import TradeSide
from dusty.m165_calibration_execution import load_calibration_execution_binding
from dusty.m165_runtime_execution import build_runtime_execution_envelope
from dusty.m187_calibration_bridge import M187CalibrationExecutionBridge, M187CalibrationPermit
from dusty.m194_native_demo_preflight import (
    NativeDemoPreflightStatus,
    assess_native_demo_preflight,
    capture_native_demo_snapshot,
)
from dusty.order_intent import MT5PreflightAdapter, OrderIntent
from dusty.position_actions import (
    MT5PositionActionPreflightAdapter,
    PositionActionIntent,
    PositionActionKind,
)
from dusty.strategy_v3 import OrderStyle


CONFIRMATION = "M165-DEMO-ONE-SHOT"
QUOTE_TOLERANCE_TICKS = 3


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


def _execution_rows(rows: object, symbol: str) -> list[object]:
    if rows is None:
        return []
    return [
        row
        for row in rows
        if str(getattr(row, "symbol", "")).upper() == symbol and getattr(row, "type", None) in (0, 1)
    ]


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


def _read_position_and_deals(mt5: object, terminal_path: str, symbol: str, *, order_ticket: int, deal_ticket: int) -> tuple[int, object | None, list[object]]:
    if not mt5.initialize(path=terminal_path):
        raise RuntimeError("MT5 initialize failed during post-entry resolution")
    try:
        now = datetime.now(timezone.utc)
        rows = mt5.history_deals_get(now - timedelta(minutes=10), now + timedelta(seconds=2), group=symbol)
        execution = _execution_rows(rows, symbol)
        matches = [
            row for row in execution
            if (deal_ticket > 0 and int(getattr(row, "ticket", 0) or 0) == deal_ticket)
            or (order_ticket > 0 and int(getattr(row, "order", 0) or 0) == order_ticket)
        ]
        position_ids = {int(getattr(row, "position_id", 0) or 0) for row in matches if int(getattr(row, "position_id", 0) or 0) > 0}
        if len(position_ids) != 1:
            return 0, None, execution
        position_id = next(iter(position_ids))
        positions = mt5.positions_get(ticket=position_id)
        position = positions[0] if positions and len(positions) == 1 else None
        return position_id, position, execution
    finally:
        mt5.shutdown()


def _position_deals(mt5: object, terminal_path: str, position_id: int, symbol: str) -> list[object]:
    if not mt5.initialize(path=terminal_path):
        raise RuntimeError("MT5 initialize failed during position-history capture")
    try:
        rows = mt5.history_deals_get(position=position_id)
        return _execution_rows(rows, symbol)
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


def _aligned_price_down(price: float, tick_size: float) -> float:
    ticks = math.floor((float(price) / float(tick_size)) + 1e-9)
    return ticks * float(tick_size)


def _write_blocked(output_path: Path, output: dict[str, object], *, status: str, reason: str) -> int:
    output["status"] = status
    output["reason"] = reason
    output["broker_send_attempted"] = False
    output["completed_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_write_json(output_path, output)
    print(json.dumps(output, indent=2, sort_keys=True))
    return 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Fresh-quote one-shot M165 calibration round trip through the M187 Demo boundary")
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
    expected = args.expected_head.strip().lower()
    output_path = Path(args.output).resolve()
    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    plan_payload = json.loads(Path(args.calibration_plan).resolve().read_text(encoding="utf-8"))
    started_at = datetime.now(timezone.utc)
    binding = load_calibration_execution_binding(plan_payload, expected_source_commit=expected, now=started_at)
    terminal_path = str(Path(args.terminal_path).resolve())

    output: dict[str, object] = {
        "protocol": "dusty-m165-calibration-roundtrip-execution-v2",
        "status": "validating",
        "source_commit": expected,
        "binding_fingerprint": binding.fingerprint,
        "plan_fingerprint": binding.plan_fingerprint,
        "planning_intent_hash": binding.intent_hash,
        "qualification_plan_fingerprint": binding.qualification_plan_fingerprint,
        "qualification_manifest_fingerprint": binding.qualification_manifest_fingerprint,
        "lane_id": binding.lane_id,
        "strategy_hash": binding.strategy_hash,
        "symbol": binding.symbol,
        "authority": {"demo_write": True, "live_write": False, "promotion": False, "retry": False},
        "broker_send_attempted": False,
        "started_at": started_at.isoformat(),
    }

    import MetaTrader5 as mt5

    snapshot = capture_native_demo_snapshot(mt5, terminal_path=terminal_path, symbol=binding.symbol, captured_at=started_at)
    assessment = assess_native_demo_preflight(snapshot, source_commit=expected, expected_terminal_path=terminal_path)
    output["native_preflight_assessment_fingerprint"] = assessment.fingerprint
    if assessment.status is not NativeDemoPreflightStatus.READY:
        return _write_blocked(output_path, output, status="native_preflight_blocked", reason=str(assessment.blockers))
    if snapshot.symbol_spec_fingerprint != binding.symbol_spec_fingerprint:
        return _write_blocked(output_path, output, status="symbol_spec_drift", reason="native symbol specification drifted since plan")

    probe = MT5IdentityProbe(mt5, terminal_path=terminal_path, symbol_spec_fingerprint=binding.symbol_spec_fingerprint)
    identity = probe.read()
    if identity.fingerprint != binding.session_fingerprint:
        return _write_blocked(output_path, output, status="session_drift", reason="Demo session identity drifted since plan")
    session = DemoSession(identity)

    if not mt5.initialize(path=terminal_path):
        return _write_blocked(output_path, output, status="runtime_quote_unavailable", reason="MT5 initialize failed for fresh runtime envelope")
    try:
        account = mt5.account_info()
        spec = mt5.symbol_info(binding.symbol)
        tick = mt5.symbol_info_tick(binding.symbol)
        if account is None or spec is None or tick is None:
            return _write_blocked(output_path, output, status="runtime_quote_unavailable", reason="account/symbol/tick unavailable")
        equity = float(getattr(account, "equity", 0.0) or 0.0)
        tick_size = float(getattr(spec, "trade_tick_size", 0.0) or getattr(spec, "point", 0.0) or 0.0)
        current_reference = float(getattr(tick, "ask", 0.0) or 0.0)
        if not all(math.isfinite(v) and v > 0 for v in (equity, tick_size, current_reference)):
            return _write_blocked(output_path, output, status="runtime_quote_invalid", reason="fresh equity/tick geometry invalid")
        planned_distance = binding.reference_price - binding.stop_price
        fresh_stop = _aligned_price_down(current_reference - planned_distance, tick_size)
        if fresh_stop <= 0 or fresh_stop >= current_reference:
            return _write_blocked(output_path, output, status="runtime_stop_invalid", reason="fresh stop cannot preserve planned native distance")
        worst_reference = current_reference + QUOTE_TOLERANCE_TICKS * tick_size
        profit = mt5.order_calc_profit(
            mt5.ORDER_TYPE_BUY,
            binding.symbol,
            binding.volume_lots,
            worst_reference,
            fresh_stop,
        )
        if profit is None or not math.isfinite(float(profit)):
            return _write_blocked(output_path, output, status="runtime_loss_unavailable", reason="broker worst-case loss calculation unavailable")
        worst_case_loss = max(0.0, -float(profit))
        if worst_case_loss <= 0:
            return _write_blocked(output_path, output, status="runtime_loss_invalid", reason="broker worst-case loss was not positive")
    finally:
        mt5.shutdown()

    try:
        runtime = build_runtime_execution_envelope(
            symbol=binding.symbol,
            volume_lots=binding.volume_lots,
            tick_size=tick_size,
            planned_reference_price=binding.reference_price,
            planned_stop_price=binding.stop_price,
            current_reference_price=current_reference,
            current_equity=equity,
            worst_case_loss_cash=worst_case_loss,
            discovery_loss_ceiling_cash=binding.discovery_loss_ceiling_cash,
            quote_tolerance_ticks=QUOTE_TOLERANCE_TICKS,
        )
    except ValueError as exc:
        return _write_blocked(output_path, output, status="runtime_risk_blocked", reason=str(exc))

    runtime_created = datetime.now(timezone.utc)
    runtime_expires = min(binding.expires_at, runtime_created + timedelta(minutes=2))
    if runtime_expires <= runtime_created:
        return _write_blocked(output_path, output, status="runtime_binding_expired", reason="plan expired before fresh runtime binding")

    intent = OrderIntent(
        strategy_hash=binding.strategy_hash,
        session_fingerprint=binding.session_fingerprint,
        symbol=binding.symbol,
        side=TradeSide.LONG,
        volume=binding.volume_lots,
        reference_price=runtime.current_reference_price,
        stop_price=fresh_stop,
        target_price=None,
        approved_risk_fraction=runtime.actual_risk_fraction,
        allowed_loss=runtime.maximum_execution_loss_cash,
        pm_approved=True,
        growth_multiplier=1.0,
        risk_approved=True,
        guardian_approved=True,
        created_at=runtime_created,
        expires_at=runtime_expires,
        filling_mode=binding.filling_mode,
        order_style=OrderStyle.MARKET,
    )

    output["runtime_envelope"] = {
        "quote_tolerance_ticks": runtime.quote_tolerance_ticks,
        "tick_size": runtime.tick_size,
        "planned_stop_distance": runtime.stop_distance,
        "fresh_reference_price": runtime.current_reference_price,
        "fresh_stop_price": fresh_stop,
        "worst_case_reference_price": runtime.worst_case_reference_price,
        "maximum_execution_loss_cash": runtime.maximum_execution_loss_cash,
        "actual_risk_fraction": runtime.actual_risk_fraction,
        "current_equity": runtime.current_equity,
        "runtime_intent_hash": intent.intent_hash,
        "authority": {"broker_write": False, "live_write": False, "promotion": False, "retry": False},
    }

    preflight_adapter = MT5PreflightAdapter(mt5, session, probe.read_connected)
    entry_checked_at = datetime.now(timezone.utc)
    entry_preflight = preflight_adapter.check(intent, at=entry_checked_at)
    output["entry_preflight"] = {
        "checked_at": entry_checked_at.isoformat(),
        "passed": entry_preflight.passed,
        "reasons": list(entry_preflight.reasons),
        "bid": entry_preflight.bid,
        "ask": entry_preflight.ask,
        "request_price": float(entry_preflight.request_dict().get("price", 0.0)),
        "loss_at_stop": entry_preflight.loss_at_stop,
        "allowed_loss": intent.allowed_loss,
        "required_margin": entry_preflight.required_margin,
        "intent_hash": intent.intent_hash,
    }
    if not entry_preflight.passed:
        return _write_blocked(output_path, output, status="fresh_entry_preflight_blocked", reason=str(entry_preflight.reasons))
    if entry_preflight.loss_at_stop > runtime.maximum_execution_loss_cash + 1e-9:
        return _write_blocked(output_path, output, status="fresh_loss_exceeds_runtime_envelope", reason="fresh native loss exceeded bounded quote tolerance")
    if not all(math.isfinite(value) and value > 0 for value in (entry_preflight.bid, entry_preflight.ask)):
        return _write_blocked(output_path, output, status="fresh_quote_incomplete", reason="entry preflight did not preserve complete bid/ask")

    custody_root = Path(args.custody_root).resolve()
    custody_root.mkdir(parents=True, exist_ok=True)
    vault = ResearchArtifactVault(custody_root / "artifact-vault")
    ledger = SQLiteExecutionLedger(custody_root / "execution-ledger.sqlite3")
    producer = _digest(("dusty-m165-calibration-roundtrip-operator-v2", expected))
    adapter = DemoMT5ExecutionAdapter(mt5, session, probe.read_connected, ledger)
    bridge = M187CalibrationExecutionBridge(vault=vault, session=session, adapter=adapter, producer_fingerprint=producer)

    try:
        valid_until = min(runtime_expires, entry_checked_at + timedelta(minutes=2))
        if valid_until <= entry_checked_at:
            return _write_blocked(output_path, output, status="entry_permit_expired", reason="entry permit would already be expired")
        entry_permit = M187CalibrationPermit(
            binding.qualification_plan_fingerprint,
            binding.qualification_manifest_fingerprint,
            binding.strategy_hash,
            binding.symbol_spec_fingerprint,
            binding.session_fingerprint,
            binding.symbol,
            intent.intent_hash,
            "open",
            binding.volume_lots,
            runtime.maximum_execution_loss_cash,
            1,
            entry_checked_at,
            valid_until,
        )
        output["broker_send_attempted"] = True
        entry_receipt = bridge.execute(
            preflight=entry_preflight,
            permit=entry_permit,
            current_symbol_spec_fingerprint=binding.symbol_spec_fingerprint,
            at=datetime.now(timezone.utc),
        )
        output["entry"] = {
            "permit_fingerprint": entry_permit.fingerprint,
            "admission_fingerprint": entry_receipt.admission.fingerprint,
            "admission_artifact_record_fingerprint": entry_receipt.admission_artifact_record_fingerprint,
            "execution": _result_payload(entry_receipt.execution),
        }

        if entry_receipt.execution.state is not ExecutionState.FILLED or entry_receipt.execution.deal_ticket <= 0:
            output["status"] = "entry_not_filled_no_retry"
            output["completed_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(output_path, output)
            print(json.dumps(output, indent=2, sort_keys=True))
            return 3

        position_id, position, recent_deals = _read_position_and_deals(
            mt5,
            terminal_path,
            binding.symbol,
            order_ticket=entry_receipt.execution.order_ticket,
            deal_ticket=entry_receipt.execution.deal_ticket,
        )
        output["entry_resolution"] = {
            "position_id": position_id,
            "recent_matching_deals": [
                _deal_payload(row)
                for row in recent_deals
                if int(getattr(row, "ticket", 0) or 0) == entry_receipt.execution.deal_ticket
                or int(getattr(row, "order", 0) or 0) == entry_receipt.execution.order_ticket
            ],
        }
        if position_id <= 0:
            output["status"] = "entry_position_unresolved_no_retry"
            output["completed_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(output_path, output)
            print(json.dumps(output, indent=2, sort_keys=True))
            return 3

        if position is None:
            deals = _position_deals(mt5, terminal_path, position_id, binding.symbol)
            output["position_deals"] = [_deal_payload(row) for row in deals]
            if len(deals) >= 2:
                output["status"] = "completed_by_broker_protective_close"
                output["completed_at"] = datetime.now(timezone.utc).isoformat()
                _atomic_write_json(output_path, output)
                print(json.dumps(output, indent=2, sort_keys=True))
                return 0
            output["status"] = "position_missing_without_complete_close_evidence"
            output["completed_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(output_path, output)
            print(json.dumps(output, indent=2, sort_keys=True))
            return 3

        current_volume = float(getattr(position, "volume", 0.0) or 0.0)
        if not math.isclose(current_volume, binding.volume_lots, rel_tol=1e-12, abs_tol=1e-12):
            output["status"] = "position_volume_drift_no_close"
            output["position_volume"] = current_volume
            output["completed_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(output_path, output)
            print(json.dumps(output, indent=2, sort_keys=True))
            return 3

        close_created = datetime.now(timezone.utc)
        close_intent = PositionActionIntent(
            strategy_hash=binding.strategy_hash,
            session_fingerprint=binding.session_fingerprint,
            kind=PositionActionKind.FULL_CLOSE,
            symbol=binding.symbol,
            side=TradeSide.LONG,
            position_ticket=position_id,
            pending_order_ticket=0,
            current_volume=current_volume,
            action_volume=current_volume,
            current_stop=float(getattr(position, "sl", 0.0) or fresh_stop),
            new_stop=0.0,
            target_price=float(getattr(position, "tp", 0.0) or 0.0),
            pm_approved=True,
            risk_approved=True,
            guardian_approved=True,
            created_at=close_created,
            expires_at=close_created + timedelta(minutes=2),
            filling_mode=binding.filling_mode,
        )
        close_adapter = MT5PositionActionPreflightAdapter(mt5, session, probe.read_connected)
        close_preflight = close_adapter.check(close_intent, at=close_created)
        if not close_preflight.passed:
            output["status"] = "full_close_preflight_failed_no_retry"
            output["close_reasons"] = list(close_preflight.reasons)
            output["completed_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(output_path, output)
            print(json.dumps(output, indent=2, sort_keys=True))
            return 3

        close_permit = M187CalibrationPermit(
            binding.qualification_plan_fingerprint,
            binding.qualification_manifest_fingerprint,
            binding.strategy_hash,
            binding.symbol_spec_fingerprint,
            binding.session_fingerprint,
            binding.symbol,
            close_intent.intent_hash,
            "full_close",
            current_volume,
            runtime.maximum_execution_loss_cash,
            2,
            close_created,
            close_created + timedelta(minutes=2),
        )
        close_receipt = bridge.execute(
            preflight=close_preflight,
            permit=close_permit,
            current_symbol_spec_fingerprint=binding.symbol_spec_fingerprint,
            at=datetime.now(timezone.utc),
        )
        output["close"] = {
            "permit_fingerprint": close_permit.fingerprint,
            "admission_fingerprint": close_receipt.admission.fingerprint,
            "admission_artifact_record_fingerprint": close_receipt.admission_artifact_record_fingerprint,
            "request_price": float(close_preflight.request_dict().get("price", 0.0)),
            "execution": _result_payload(close_receipt.execution),
        }
        if close_receipt.execution.state is not ExecutionState.CLOSED:
            output["status"] = "full_close_not_confirmed_no_retry"
            output["completed_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write_json(output_path, output)
            print(json.dumps(output, indent=2, sort_keys=True))
            return 3

        deals = _position_deals(mt5, terminal_path, position_id, binding.symbol)
        output["position_deals"] = [_deal_payload(row) for row in deals]
        output["status"] = "completed"
        output["completed_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write_json(output_path, output)
        print(json.dumps(output, indent=2, sort_keys=True))
        return 0
    finally:
        ledger.close()
        vault.close()


if __name__ == "__main__":
    raise SystemExit(main())
