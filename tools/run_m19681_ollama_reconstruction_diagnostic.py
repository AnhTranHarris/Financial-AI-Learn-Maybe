from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dusty.ollama_reconstruction_diagnostic import diagnose_ollama_reconstruction


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _fingerprint(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_prior_receipt(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("protocol") != "dusty-m1968-normalized-reconstruction-canary-v1":
        raise ValueError("prior M196.8 canary receipt protocol mismatch")
    stored = str(raw.get("receipt_fingerprint", ""))
    check = dict(raw)
    check.pop("receipt_fingerprint", None)
    if stored != _fingerprint(check):
        raise ValueError("prior M196.8 canary receipt fingerprint mismatch")
    if raw.get("status") != "reconstruction_unavailable":
        raise ValueError("M196.8.1 diagnostic only accepts an unavailable prior canary")
    population = raw.get("population")
    if not isinstance(population, list) or len(population) != 1 or not isinstance(population[0], dict):
        raise ValueError("prior M196.8 canary population evidence malformed")
    reason = str(population[0].get("reason", ""))
    if "TimeoutError" not in reason:
        raise ValueError("M196.8.1 diagnostic requires preserved timeout evidence")
    if raw.get("main_strategy_estate_modified") is not False or raw.get("threshold_tuning_performed") is not False:
        raise ValueError("prior canary mutation/tuning evidence is unsafe")
    authority = raw.get("authority")
    if not isinstance(authority, dict) or any(value is not False for value in authority.values()):
        raise ValueError("prior canary unexpectedly carries authority")
    return raw


def main() -> int:
    parser = argparse.ArgumentParser(description="Diagnose local Ollama after M196.8 timeout without retrying the canary")
    parser.add_argument("--prior-receipt", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    args = parser.parse_args()

    prior = _load_prior_receipt(args.prior_receipt.resolve())
    result = diagnose_ollama_reconstruction(
        model_tag=str(prior["model_tag"]),
        model_digest=str(prior["model_digest"]),
        base_url=args.ollama,
    )
    payload = result.to_payload()
    payload["prior_canary_receipt_fingerprint"] = prior["receipt_fingerprint"]
    payload["prior_canary_status"] = prior["status"]
    payload["prior_canary_reason"] = prior["population"][0]["reason"]
    payload["recovery_action"] = (
        "eligible_for_engineering_review" if result.status == "pass" else "provider_health_blocked"
    )
    payload["canary_retry_authorized"] = False

    # Re-bind fingerprint after attaching prior-canary provenance.
    payload.pop("diagnostic_fingerprint", None)
    payload["diagnostic_fingerprint"] = _fingerprint(payload)
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() and output.read_text(encoding="utf-8") != rendered:
        raise ValueError("diagnostic output already exists with different bytes")
    output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if result.status == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
