from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from dusty.m165_broker_forensics import capture_broker_forensics


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only exact-ticket M165/M187 ambiguous-order reconciliation")
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--order-ticket", required=True, type=int)
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--sent-at", required=True, help="timezone-aware ISO-8601 send timestamp")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    sent_at = datetime.fromisoformat(args.sent_at)
    if sent_at.tzinfo is None or sent_at.utcoffset() is None:
        raise ValueError("--sent-at must be timezone-aware")

    import MetaTrader5 as mt5

    if not mt5.initialize(path=str(Path(args.terminal_path).resolve())):
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        account = mt5.account_info()
        terminal = mt5.terminal_info()
        if account is None or terminal is None:
            raise RuntimeError("terminal/account information unavailable")
        demo_mode = int(getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0))
        if int(getattr(account, "trade_mode", -1)) != demo_mode:
            raise PermissionError("connected account is not DEMO")
        snapshot = capture_broker_forensics(
            mt5,
            order_ticket=args.order_ticket,
            symbol=args.symbol,
            sent_at=sent_at,
            captured_at=datetime.now(timezone.utc),
        )
        payload = dict(snapshot.payload)
        payload["forensic_fingerprint"] = snapshot.fingerprint
        payload["account"] = {
            "server": str(getattr(account, "server", "")),
            "mode": "demo",
        }
        payload["terminal"] = {
            "connected": bool(getattr(terminal, "connected", False)),
            "trade_allowed": bool(getattr(terminal, "trade_allowed", False)),
            "tradeapi_disabled": bool(getattr(terminal, "tradeapi_disabled", True)),
        }
    finally:
        mt5.shutdown()

    output = Path(args.output).resolve()
    _atomic_write_json(output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["assessment"] != "broker_execution_evidence_found" else 2


if __name__ == "__main__":
    raise SystemExit(main())
