from __future__ import annotations

"""Read-only inspection of the exact frozen M166 strategy event requirement."""

import argparse
from hashlib import sha256
import json
from pathlib import Path

from dusty.m166_research_identity import parameter_fingerprint
from dusty.strategy_estate import load_strategy_estate

PROTOCOL = "dusty-m166-event-requirement-inspector-v1"


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
    rows = load_strategy_estate(estate_path)
    recon_fp = str(identity.get("reconstruction_fingerprint", ""))
    strategy_fp = str(identity.get("strategy_fingerprint", ""))
    matches = [row for row in rows if row.fingerprint == recon_fp and row.candidate_spec.strategy_hash == strategy_fp]
    if len(matches) != 1:
        raise RuntimeError("Strategy Estate does not contain exactly one frozen reconstruction")
    spec = matches[0].candidate_spec
    if parameter_fingerprint(spec) != identity.get("parameter_fingerprint"):
        raise RuntimeError("frozen strategy parameter identity drift")

    payload = {
        "protocol": PROTOCOL,
        "strategy_fingerprint": strategy_fp,
        "reconstruction_fingerprint": recon_fp,
        "symbol": spec.symbol,
        "timeframe": spec.timeframe,
        "event_exclusion_minutes": int(spec.event_exclusion_minutes),
        "session_filters": list(spec.session_filters),
        "intended_horizon_minutes": int(spec.intended_horizon_minutes),
        "dataset_first_bar_utc": identity.get("dataset_metadata", {}).get("first_bar_utc"),
        "dataset_last_bar_utc": identity.get("dataset_metadata", {}).get("last_bar_utc"),
        "event_evidence_required": bool(spec.event_exclusion_minutes),
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
