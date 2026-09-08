from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path

from dusty.broker_calibration import BrokerExecutionObservation, TradeSide
from dusty.m165_observation_custody import M165ObservationCustody


def _row(payload: dict[str, object], broker: str, symbol: str) -> BrokerExecutionObservation:
    observation = BrokerExecutionObservation(
        broker_profile_fingerprint=broker,
        symbol=symbol,
        side=TradeSide(str(payload["side"])),
        observed_at=datetime.fromisoformat(str(payload["observed_at"])),
        point_size=float(payload["point_size"]),
        bid=float(payload["bid"]),
        ask=float(payload["ask"]),
        requested_price=float(payload["requested_price"]),
        fill_price=float(payload["fill_price"]),
        volume_lots=float(payload["volume_lots"]),
        commission=float(payload["commission"]),
        fee=float(payload.get("fee", 0.0)),
        swap=float(payload.get("swap", 0.0)),
        evidence_fingerprint=str(payload["evidence_fingerprint"]),
    )
    expected = str(payload["fingerprint"]).strip().lower()
    if observation.fingerprint != expected:
        raise ValueError("observation fingerprint mismatch; custody import rejected")
    return observation


def main() -> int:
    parser = argparse.ArgumentParser(description="Import validated M165 native observations into durable local custody")
    parser.add_argument("--input", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--summary", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if payload.get("protocol") != "dusty-m165-native-observation-extraction-v1":
        raise ValueError("unsupported observation extraction protocol")
    authority = payload.get("authority", {})
    if not isinstance(authority, dict) or any(bool(authority.get(key, False)) for key in ("broker_write", "live_write", "retry", "promotion")):
        raise PermissionError("observation artifact contains unexpected authority")
    broker = str(payload["broker_profile_fingerprint"]).strip().lower()
    symbol = str(payload["symbol"]).strip().upper()
    raw_rows = payload.get("observations", [])
    if not isinstance(raw_rows, list) or not raw_rows:
        raise ValueError("validated observation rows required")
    rows = tuple(_row(dict(row), broker, symbol) for row in raw_rows if isinstance(row, dict))
    if len(rows) != len(raw_rows):
        raise ValueError("every observation row must be a mapping")

    with M165ObservationCustody(args.database) as store:
        result = store.import_rows(rows)
        summary = store.summary_payload()
    summary["last_import"] = {
        "inserted": result.inserted,
        "duplicates": result.duplicates,
        "total": result.total,
        "source": str(Path(args.input).resolve()),
    }
    output = Path(args.summary)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
