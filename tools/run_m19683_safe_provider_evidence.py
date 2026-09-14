from __future__ import annotations

import argparse
import json
from pathlib import Path

from dusty.ollama_safe_provider_evidence import collect_safe_provider_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prior-diagnostic", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    prior_path = Path(args.prior_diagnostic)
    output_path = Path(args.output)
    prior = json.loads(prior_path.read_text(encoding="utf-8-sig"))
    payload = collect_safe_provider_evidence(prior_diagnostic=prior).to_payload()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
