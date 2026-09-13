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

from dusty.reconstruction_feature_contract import validate_reconstruction_spec
from dusty.reconstruction_retirement import retire_dead_reconstruction
from dusty.strategy_estate import load_strategy_estate


def _read(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def _write_exact(path: Path, payload: dict[str, object]) -> None:
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") != rendered:
        raise RuntimeError(f"existing immutable evidence differs: {path}")
    path.write_text(rendered, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Retire one dead M166 reconstruction and audit legacy Estate semantics")
    parser.add_argument("--child-audit", type=Path, required=True)
    parser.add_argument("--child-receipt", type=Path, required=True)
    parser.add_argument("--strategy-estate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    child_audit = _read(args.child_audit)
    child_receipt = _read(args.child_receipt)
    retirement = retire_dead_reconstruction(child_audit, child_receipt)

    estate = load_strategy_estate(args.strategy_estate)
    admissible: list[str] = []
    legacy_invalid: list[dict[str, str]] = []
    for row in estate:
        try:
            validate_reconstruction_spec(row.candidate_spec)
        except ValueError as exc:
            legacy_invalid.append({
                "reconstruction_fingerprint": row.fingerprint,
                "strategy_fingerprint": row.candidate_spec.strategy_hash,
                "proposal_fingerprint": row.proposal_fingerprint,
                "reason": str(exc),
            })
        else:
            admissible.append(row.fingerprint)

    payload = retirement.payload | {
        "retirement_fingerprint": retirement.fingerprint,
        "estate_file_sha256": sha256(args.strategy_estate.read_bytes()).hexdigest(),
        "estate_total_reconstructions": len(estate),
        "estate_semantically_admissible_count": len(admissible),
        "estate_legacy_invalid_count": len(legacy_invalid),
        "estate_semantically_admissible_reconstruction_fingerprints": sorted(admissible),
        "estate_legacy_invalid": sorted(legacy_invalid, key=lambda row: row["reconstruction_fingerprint"]),
    }
    _write_exact(args.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
