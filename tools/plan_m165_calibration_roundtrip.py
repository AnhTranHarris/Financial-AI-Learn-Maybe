from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import subprocess

from dusty.demo_session import DemoSession, MT5IdentityProbe, SessionIdentity
from dusty.experience import TradeSide
from dusty.m165_calibration_roundtrip import (
    CalibrationPlanningPolicy,
    build_native_envelope,
    tighten_calibration_loss_budget,
)
from dusty.m185_production_qualification import ProductionQualificationManifest, ProductionQualificationPlan
from dusty.m194_native_demo_preflight import (
    NativeDemoPreflightStatus,
    assess_native_demo_preflight,
    capture_native_demo_snapshot,
)
from dusty.order_intent import MT5PreflightAdapter, OrderIntent
from dusty.strategy_v3 import OrderStyle


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed with exit {proc.returncode}")
    return proc.stdout.strip()


def _load_verified_plan(path: Path) -> tuple[ProductionQualificationPlan, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol") != "dusty-m185-production-qualification-bootstrap-v1":
        raise ValueError("qualification plan protocol mismatch")
    rows = payload.get("manifests")
    if not isinstance(rows, list) or not rows:
        raise ValueError("qualification plan has no candidate manifests")
    manifests: list[ProductionQualificationManifest] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("qualification manifest must be an object")
        manifest = ProductionQualificationManifest(
            source_commit=raw["source_commit"],
            estate_sha256=raw["estate_sha256"],
            reconstruction_fingerprint=raw["reconstruction_fingerprint"],
            proposal_fingerprint=raw["proposal_fingerprint"],
            strategy_hash=raw["strategy_hash"],
            source_id=raw["source_id"],
            source_url=raw["source_url"],
            source_content_sha256=raw["source_content_sha256"],
            source_family_fingerprint=raw["source_family_fingerprint"],
            title=raw["title"],
            symbol=raw["symbol"],
            timeframe=raw["timeframe"],
            lane_id=raw["lane_id"],
            source_claim_complete=bool(raw["source_claim_complete"]),
            hypothesis_rule_count=raw["hypothesis_rule_count"],
            required_stages=tuple(raw["required_stages"]),
            created_at=datetime.fromisoformat(raw["created_at"]),
            schema_version=raw.get("schema_version", 1),
        )
        if raw.get("manifest_fingerprint") != manifest.fingerprint:
            raise ValueError("qualification manifest fingerprint mismatch")
        manifests.append(manifest)
    created_values = {row.created_at for row in manifests}
    if len(created_values) != 1:
        raise ValueError("qualification plan manifests must share one created_at")
    plan = ProductionQualificationPlan(
        source_commit=str(payload.get("source_commit", "")).strip().lower(),
        estate_sha256=str(payload.get("estate_sha256", "")).strip().lower(),
        manifests=tuple(manifests),
        created_at=next(iter(created_values)),
    )
    expected_fp = str(payload.get("plan_fingerprint", "")).strip().lower()
    if expected_fp != plan.fingerprint:
        raise ValueError("qualification plan fingerprint mismatch")
    if payload.get("candidate_lane_count") != len(plan.manifests) or payload.get("status") != "planned":
        raise ValueError("qualification plan metadata mismatch")
    return plan, expected_fp


def _symbol_spec_fingerprint(spec: object) -> str:
    payload = {
        key: value
        for key, value in spec._asdict().items()
        if key in {
            "name", "digits", "point", "trade_mode", "trade_contract_size",
            "volume_min", "volume_max", "volume_step", "trade_tick_size",
            "trade_tick_value", "currency_base", "currency_profit", "currency_margin",
        }
    }
    return _digest(payload)


def _round_down_to_tick(price: float, tick_size: float) -> float:
    ticks = math.floor((price / tick_size) + 1e-9)
    return ticks * tick_size


def _filling_modes(mt5: object) -> tuple[int, ...]:
    rows: list[int] = []
    for name in ("ORDER_FILLING_FOK", "ORDER_FILLING_IOC", "ORDER_FILLING_RETURN"):
        if hasattr(mt5, name):
            value = int(getattr(mt5, name))
            if value not in rows:
                rows.append(value)
    if not rows:
        raise RuntimeError("MetaTrader5 exposes no supported filling mode constants")
    return tuple(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only planner for one guarded M165 calibration round trip")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = args.expected_head.strip().lower()
    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    plan, plan_fp = _load_verified_plan(Path(args.qualification_plan).resolve())
    symbol = args.symbol.strip().upper()
    matches = tuple(sorted((row for row in plan.manifests if row.symbol == symbol), key=lambda row: (row.lane_id, row.reconstruction_fingerprint)))
    if not matches:
        raise ValueError(f"qualification plan has no lane for {symbol}")
    manifest = matches[0]

    import MetaTrader5 as mt5

    terminal_path = str(Path(args.terminal_path).resolve())
    captured_at = datetime.now(timezone.utc)
    snapshot = capture_native_demo_snapshot(mt5, terminal_path=terminal_path, symbol=symbol, captured_at=captured_at)
    assessment = assess_native_demo_preflight(snapshot, source_commit=expected, expected_terminal_path=terminal_path)
    if assessment.status is not NativeDemoPreflightStatus.READY:
        payload = {
            "protocol": "dusty-m165-calibration-roundtrip-plan-v1",
            "status": "blocked",
            "blockers": list(assessment.blockers),
            "source_commit": expected,
            "qualification_plan_fingerprint": plan_fp,
            "authority": {"broker_write": False, "live_write": False, "promotion": False},
        }
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        print(rendered)
        Path(args.output).resolve().write_text(rendered + "\n", encoding="utf-8")
        return 2

    if not mt5.initialize(path=terminal_path):
        raise RuntimeError("MetaTrader5 initialize failed for calibration planning")
    try:
        account = mt5.account_info()
        spec = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        terminal = mt5.terminal_info()
        if account is None or spec is None or tick is None or terminal is None:
            raise RuntimeError("native account/symbol/tick/terminal data unavailable")
        equity = float(getattr(account, "equity", 0.0))
        minimum_volume = float(getattr(spec, "volume_min", 0.0))
        volume_step = float(getattr(spec, "volume_step", 0.0))
        point = float(getattr(spec, "point", 0.0))
        tick_size = float(getattr(spec, "trade_tick_size", 0.0) or point)
        stops_level = int(getattr(spec, "trade_stops_level", 0) or 0)
        freeze_level = int(getattr(spec, "trade_freeze_level", 0) or 0)
        spec_fp = _symbol_spec_fingerprint(spec)
        if spec_fp != snapshot.symbol_spec_fingerprint:
            raise RuntimeError("symbol specification drifted after READY preflight")
        identity = SessionIdentity(
            terminal_path=terminal_path,
            terminal_build=str(getattr(terminal, "build", snapshot.terminal_build)),
            server=str(getattr(account, "server", snapshot.server)),
            login=int(getattr(account, "login", snapshot.login)),
            account_mode=snapshot.account_mode,
            account_currency=str(getattr(account, "currency", snapshot.account_currency)),
            leverage=float(getattr(account, "leverage", snapshot.leverage)),
            trade_allowed=bool(getattr(account, "trade_allowed", False)),
            expert_trading_allowed=bool(getattr(account, "trade_expert", False)),
            margin_mode=int(getattr(account, "margin_mode", -1)),
            symbol_spec_fingerprint=spec_fp,
            captured_at=captured_at,
        )
        reference_price = float(getattr(tick, "ask", 0.0))
    finally:
        mt5.shutdown()

    policy = CalibrationPlanningPolicy()
    envelope = build_native_envelope(
        symbol=symbol,
        equity=equity,
        minimum_volume=minimum_volume,
        volume_step=volume_step,
        point=point,
        trade_tick_size=tick_size,
        stops_level_points=stops_level,
        freeze_level_points=freeze_level,
        policy=policy,
    )
    session = DemoSession(identity)
    probe = MT5IdentityProbe(mt5, terminal_path=terminal_path, symbol_spec_fingerprint=spec_fp)
    preflight_adapter = MT5PreflightAdapter(mt5, session, probe.read_connected)
    filling_modes = _filling_modes(mt5)

    selected = None
    rejected: list[dict[str, object]] = []
    for distance in envelope.stop_distance_candidates:
        stop_price = _round_down_to_tick(reference_price - distance, tick_size)
        if stop_price <= 0 or stop_price >= reference_price:
            continue
        for filling in filling_modes:
            now = datetime.now(timezone.utc)
            discovery_intent = OrderIntent(
                strategy_hash=manifest.strategy_hash,
                session_fingerprint=session.identity.fingerprint,
                symbol=symbol,
                side=TradeSide.LONG,
                volume=envelope.minimum_volume,
                reference_price=reference_price,
                stop_price=stop_price,
                target_price=None,
                approved_risk_fraction=float(policy.normal_risk_fraction),
                allowed_loss=envelope.loss_ceiling_cash,
                pm_approved=True,
                growth_multiplier=1.0,
                risk_approved=True,
                guardian_approved=True,
                created_at=now,
                expires_at=now + timedelta(minutes=2),
                filling_mode=filling,
                order_style=OrderStyle.MARKET,
            )
            discovery_preflight = preflight_adapter.check(discovery_intent, at=now)
            if discovery_preflight.passed:
                actual_fraction = discovery_preflight.loss_at_stop / equity if equity > 0 else math.inf
                if actual_fraction <= float(policy.normal_risk_fraction) + 1e-12:
                    try:
                        executable_loss = tighten_calibration_loss_budget(
                            observed_loss=discovery_preflight.loss_at_stop,
                            discovery_ceiling=envelope.loss_ceiling_cash,
                        )
                    except ValueError:
                        executable_loss = 0.0
                    if executable_loss > 0:
                        executable_intent = OrderIntent(
                            strategy_hash=manifest.strategy_hash,
                            session_fingerprint=session.identity.fingerprint,
                            symbol=symbol,
                            side=TradeSide.LONG,
                            volume=envelope.minimum_volume,
                            reference_price=reference_price,
                            stop_price=stop_price,
                            target_price=None,
                            approved_risk_fraction=actual_fraction,
                            allowed_loss=executable_loss,
                            pm_approved=True,
                            growth_multiplier=1.0,
                            risk_approved=True,
                            guardian_approved=True,
                            created_at=now,
                            expires_at=now + timedelta(minutes=2),
                            filling_mode=filling,
                            order_style=OrderStyle.MARKET,
                        )
                        executable_preflight = preflight_adapter.check(executable_intent, at=datetime.now(timezone.utc))
                        if executable_preflight.passed and executable_preflight.loss_at_stop <= executable_loss + 1e-9:
                            selected = (executable_intent, executable_preflight, distance, actual_fraction)
                            break
                        rejected.append({
                            "distance": distance,
                            "filling_mode": filling,
                            "reasons": ["executable_rebind_preflight_failed", *executable_preflight.reasons],
                            "loss_at_stop": executable_preflight.loss_at_stop,
                        })
                        continue
            rejected.append({
                "distance": distance,
                "filling_mode": filling,
                "reasons": list(discovery_preflight.reasons) or ["measured_loss_not_executable"],
                "loss_at_stop": discovery_preflight.loss_at_stop,
            })
        if selected is not None:
            break

    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if selected is None:
        payload = {
            "protocol": "dusty-m165-calibration-roundtrip-plan-v1",
            "status": "blocked",
            "blockers": ["no_broker_valid_minimum_lot_stop_geometry_within_normal_risk"],
            "source_commit": expected,
            "qualification_plan_fingerprint": plan_fp,
            "qualification_manifest_fingerprint": manifest.fingerprint,
            "native_envelope": envelope.payload,
            "rejected_probe_count": len(rejected),
            "rejected_probes": rejected,
            "authority": {"broker_write": False, "live_write": False, "promotion": False},
        }
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        output.write_text(rendered + "\n", encoding="utf-8")
        print(rendered)
        return 2

    intent, preflight, distance, actual_fraction = selected
    plan_payload = {
        "protocol": "dusty-m165-calibration-roundtrip-plan-v1",
        "status": "planned",
        "source_commit": expected,
        "qualification_plan_fingerprint": plan_fp,
        "qualification_plan_source_commit": plan.source_commit,
        "qualification_manifest_fingerprint": manifest.fingerprint,
        "lane_id": manifest.lane_id,
        "strategy_hash": manifest.strategy_hash,
        "symbol": symbol,
        "side": "long",
        "session_fingerprint": session.identity.fingerprint,
        "native_preflight_assessment_fingerprint": assessment.fingerprint,
        "native_snapshot_fingerprint": snapshot.fingerprint,
        "symbol_spec_fingerprint": spec_fp,
        "native_envelope": envelope.payload,
        "selected": {
            "intent_hash": intent.intent_hash,
            "client_tag": intent.client_tag,
            "volume_lots": intent.volume,
            "reference_price": intent.reference_price,
            "stop_price": intent.stop_price,
            "stop_distance": distance,
            "filling_mode": intent.filling_mode,
            "loss_at_stop": preflight.loss_at_stop,
            "actual_risk_fraction": actual_fraction,
            "approved_risk_fraction": intent.approved_risk_fraction,
            "discovery_loss_ceiling_cash": envelope.loss_ceiling_cash,
            "execution_allowed_loss_cash": intent.allowed_loss,
            "required_margin": preflight.required_margin,
            "checked_price": preflight.checked_price,
            "created_at": intent.created_at.isoformat(),
            "expires_at": intent.expires_at.isoformat(),
        },
        "rejected_probe_count_before_selection": len(rejected),
        "authority": {
            "broker_write": False,
            "live_write": False,
            "promotion": False,
            "execution_bridge_invoked": False,
        },
    }
    plan_payload["plan_fingerprint"] = _digest(plan_payload)
    rendered = json.dumps(plan_payload, indent=2, sort_keys=True)
    output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
