from __future__ import annotations

"""Read-only inspection of the exact frozen M166 strategy event requirement."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from dusty.m166_research_identity import parameter_fingerprint
from dusty.strategy_estate import load_strategy_estate

PROTOCOL = "dusty-m166-event-requirement-inspector-v2"


def _required_text(value: object, label: str) -> str:
    rendered = str(value or "").strip()
    if not rendered:
        raise RuntimeError(f"missing {label}")
    return rendered


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", required=True)
    parser.add_argument("--strategy-estate", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    identity_path = Path(args.identity).resolve()
    estate_path = Path(args.strategy_estate).resolve()
    output = Path(args.output).resolve()
    identity = json.loads(identity_path.read_text(encoding="utf-8"))
    if not isinstance(identity, dict):
        raise RuntimeError("M166 identity must be a JSON object")

    rows = load_strategy_estate(estate_path)
    recon_fp = _required_text(identity.get("reconstruction_fingerprint"), "reconstruction fingerprint")
    strategy_fp = _required_text(identity.get("strategy_fingerprint"), "strategy fingerprint")
    matches = [row for row in rows if row.fingerprint == recon_fp and row.candidate_spec.strategy_hash == strategy_fp]
    if len(matches) != 1:
        raise RuntimeError("Strategy Estate does not contain exactly one frozen reconstruction")

    reconstruction = matches[0]
    spec = reconstruction.candidate_spec
    if parameter_fingerprint(spec) != identity.get("parameter_fingerprint"):
        raise RuntimeError("frozen strategy parameter identity drift")
    if len(reconstruction.symbols) != 1:
        raise RuntimeError("frozen M166 reconstruction must identify exactly one symbol")

    metadata = identity.get("dataset_metadata")
    if not isinstance(metadata, dict):
        raise RuntimeError("M166 identity dataset_metadata is missing")
    symbol = _required_text(metadata.get("symbol"), "dataset symbol").upper()
    timeframe = _required_text(metadata.get("timeframe"), "dataset timeframe").upper()
    first_bar = _required_text(metadata.get("first_bar_utc"), "dataset first_bar_utc")
    last_bar = _required_text(metadata.get("last_bar_utc"), "dataset last_bar_utc")

    reconstruction_symbol = reconstruction.symbols[0].strip().upper()
    reconstruction_timeframe = reconstruction.timeframe.strip().upper()
    if reconstruction_symbol != symbol:
        raise RuntimeError("frozen reconstruction symbol differs from M166 dataset identity")
    if reconstruction_timeframe != timeframe:
        raise RuntimeError("frozen reconstruction timeframe differs from M166 dataset identity")

    payload = {
        "protocol": PROTOCOL,
        "strategy_fingerprint": strategy_fp,
        "reconstruction_fingerprint": recon_fp,
        "symbol": symbol,
        "timeframe": timeframe,
        "event_exclusion_minutes": int(spec.event_exclusion_minutes),
        "session_filters": list(spec.session_filters),
        "intended_horizon_minutes": int(spec.intended_horizon_minutes),
        "dataset_first_bar_utc": first_bar,
        "dataset_last_bar_utc": last_bar,
        "event_evidence_required": bool(spec.event_exclusion_minutes),
        "reconstruction_actor": reconstruction.actor.value,
        "reconstruction_rules": [
            {"name": rule.name, "value": rule.value, "basis": rule.basis.value}
            for rule in reconstruction.rules
            if "event" in rule.name.casefold() or "news" in rule.name.casefold() or "calendar" in rule.name.casefold()
        ],
        "unresolved_event_rules": [
            value for value in reconstruction.unresolved_source_rules
            if any(token in value.casefold() for token in ("event", "news", "calendar"))
        ],
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "promotion": False,
            "retry": False,
            "risk_override": False,
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    result = payload | {"requirement_fingerprint": sha256(encoded).hexdigest()}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
