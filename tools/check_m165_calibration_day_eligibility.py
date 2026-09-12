from __future__ import annotations

"""Read-only eligibility probe for M165 broker-evidence calibration days."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from dusty.m165_calibration_campaign import calibration_day_policy, validate_campaign_start
from dusty.m165_observation_custody import M165ObservationCustody


PROTOCOL = "dusty-m165-calibration-day-eligibility-v1"
SYMBOL = "EURUSD"
MAX_EVIDENCE_CLOCK_OFFSET_SECONDS = 18 * 60 * 60


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def _native_blockers(*, terminal, account, demo_mode: int, positions, orders) -> list[str]:
    blockers: list[str] = []
    if not bool(getattr(terminal, "connected", False)):
        blockers.append("terminal_not_connected")
    if not bool(getattr(terminal, "trade_allowed", False)):
        blockers.append("terminal_trade_permission_disabled")
    if bool(getattr(terminal, "tradeapi_disabled", True)):
        blockers.append("terminal_trade_api_disabled")
    if int(getattr(account, "trade_mode", -1)) != demo_mode:
        blockers.append("account_not_demo")
    if not bool(getattr(account, "trade_allowed", False)):
        blockers.append("account_trade_permission_disabled")
    if not bool(getattr(account, "trade_expert", False)):
        blockers.append("account_expert_trading_disabled")
    if len(positions) != 0:
        blockers.append("symbol_position_open")
    if len(orders) != 0:
        blockers.append("symbol_order_open")
    return blockers


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M165 calibration day eligibility probe")
    parser.add_argument("--day", required=True, type=int, choices=(2, 3))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    terminal_path = str(Path(args.terminal_path).resolve())
    database = Path(args.database).resolve()
    expected = args.expected_head.strip().lower()
    policy = calibration_day_policy(args.day)

    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")
    if not database.is_file():
        raise FileNotFoundError(database)

    with M165ObservationCustody(database) as store:
        rows = store.load_all()
        custody = store.summary_payload()

    import MetaTrader5 as mt5

    if not mt5.initialize(path=terminal_path):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        terminal = mt5.terminal_info()
        account = mt5.account_info()
        tick = mt5.symbol_info_tick(SYMBOL)
        positions = mt5.positions_get(symbol=SYMBOL)
        orders = mt5.orders_get(symbol=SYMBOL)
        if terminal is None or account is None or tick is None:
            raise RuntimeError("terminal/account/tick evidence unavailable")
        if positions is None or orders is None:
            raise RuntimeError(f"EURUSD broker-state query failed: {mt5.last_error()}")
        time_msc = int(getattr(tick, "time_msc", 0) or 0)
        if time_msc <= 0:
            raise RuntimeError("broker tick lacks positive time_msc")
        broker_instant = datetime.fromtimestamp(time_msc / 1000.0, tz=timezone.utc)
        wall = datetime.now(timezone.utc)
        clock_offset = (broker_instant - wall).total_seconds()
        if abs(clock_offset) > MAX_EVIDENCE_CLOCK_OFFSET_SECONDS:
            raise RuntimeError(f"broker evidence clock offset is implausible: {clock_offset:.3f}s")

        demo_mode = int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0))
        native_blockers = _native_blockers(
            terminal=terminal,
            account=account,
            demo_mode=demo_mode,
            positions=positions,
            orders=orders,
        )
        native_ready = not native_blockers
    finally:
        mt5.shutdown()

    reason = "eligible"
    eligible = False
    try:
        validate_campaign_start(rows, policy=policy, campaign_date=broker_instant.date())
        if not native_ready:
            reason = ",".join(native_blockers)
        else:
            eligible = True
    except Exception as exc:  # report the fail-closed eligibility reason without mutating state
        reason = str(exc)

    result = {
        "protocol": PROTOCOL,
        "source_commit": expected,
        "day_number": args.day,
        "status": "eligible" if eligible else "not_eligible",
        "eligible": eligible,
        "reason": reason,
        "symbol": SYMBOL,
        "broker_evidence_clock": broker_instant.isoformat(),
        "broker_evidence_date": broker_instant.date().isoformat(),
        "broker_evidence_clock_offset_seconds": clock_offset,
        "custody": {
            "observation_count": int(custody.get("observation_count", 0) or 0),
            "distinct_days": int(custody.get("distinct_days", 0) or 0),
            "sides": list(custody.get("sides", [])),
            "calibration_status": str((custody.get("calibration") or {}).get("status", "")),
        },
        "native": {
            "connected": bool(getattr(terminal, "connected", False)),
            "terminal_trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
            "tradeapi_disabled": bool(getattr(terminal, "tradeapi_disabled", True)),
            "account_demo": int(getattr(account, "trade_mode", -1)) == demo_mode,
            "account_trade_allowed": bool(getattr(account, "trade_allowed", False)),
            "account_trade_expert": bool(getattr(account, "trade_expert", False)),
            "positions": len(positions),
            "orders": len(orders),
            "blockers": native_blockers,
        },
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "new_entry": False,
            "recovery_close": False,
            "retry": False,
            "promotion": False,
        },
    }

    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
