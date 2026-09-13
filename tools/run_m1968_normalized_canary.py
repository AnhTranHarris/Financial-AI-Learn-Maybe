from __future__ import annotations

"""Build and PIT-preflight one normalized EURUSD reconstruction canary.

The canary writes only to a caller-supplied sidecar Strategy Estate. It never
modifies the user's persistent estate and owns no broker, live, promotion,
retry, custody, Guardian or risk authority.
"""

import argparse
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dusty.m166_provisional_quant import build_runtime_bars
from dusty.mt5worker import MT5Bar
from dusty.ollama_strategy_classifier import OllamaStrategyClassifier
from dusty.ollama_strategy_reconstruction_retry import BoundedRetryOllamaStrategyReconstructor
from dusty.reconstruction_runtime_features import MODEL_SAFE_NORMALIZED_FEATURES, augment_runtime_bars
from dusty.reconstruction_semantics import assess_reconstruction_semantics
from dusty.research_sessions import matching_research_session
from dusty.strategy_estate import load_strategy_estate
from dusty.strategy_estate_builder import StrategyEstateBuilder
from dusty.strategy_estate_cli import installed_model_digest
from dusty.strategy_seed_proposals import starter_strategy_proposals

UTC = timezone.utc
CANARY_PROTOCOL = "dusty-m1968-normalized-reconstruction-canary-v1"
CANARY_PROPOSAL_ID = "dusty:eurusd-momentum-pullback"
CANARY_FEATURES = ("return_1", "rsi") + MODEL_SAFE_NORMALIZED_FEATURES
CANARY_TIMEFRAMES = ("M15",)
CANARY_SYMBOLS = ("EURUSD",)
CANARY_SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "LONDON_NY_OVERLAP")


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _fingerprint(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _dt(value: object) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("bar timestamp must be timezone-aware")
    return result.astimezone(UTC)


def _load_bars(path: Path) -> tuple[MT5Bar, ...]:
    rows: list[MT5Bar] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError(f"dataset row {line_number} must be an object")
            at = raw.get("at", raw.get("time", raw.get("time_utc")))
            rows.append(MT5Bar(
                _dt(at),
                float(raw["open"]),
                float(raw["high"]),
                float(raw["low"]),
                float(raw["close"]),
                int(raw.get("tick_volume", 0)),
                int(raw.get("spread", 0)),
                int(raw.get("real_volume", 0)),
            ))
    if not rows:
        raise ValueError("canary dataset contains no bars")
    return tuple(rows)


def _proposal():
    matches = tuple(row for row in starter_strategy_proposals() if row.proposal_id == CANARY_PROPOSAL_ID)
    if len(matches) != 1:
        raise RuntimeError("normalized canary proposal identity drift")
    return matches[0]


def _bind_sessions(rows, sessions: tuple[str, ...]):
    if not sessions:
        return rows
    return tuple(replace(row, session=matching_research_session(row.at, sessions)) for row in rows)


def _existing_canary(sidecar: Path, proposal_fingerprint: str):
    rows = tuple(row for row in load_strategy_estate(sidecar) if row.proposal_fingerprint == proposal_fingerprint)
    if len(rows) > 1:
        raise ValueError("sidecar contains multiple reconstructions for canary proposal")
    return rows[0] if rows else None


def _existing_receipt(path: Path) -> tuple[dict[str, object], int] | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("protocol") != CANARY_PROTOCOL:
        raise ValueError("existing canary receipt protocol mismatch")
    stored = str(raw.get("receipt_fingerprint", ""))
    check = dict(raw)
    check.pop("receipt_fingerprint", None)
    if stored != _fingerprint(check):
        raise ValueError("existing canary receipt fingerprint mismatch")
    status = str(raw.get("status", ""))
    exit_code = 0 if status == "activatable" else (3 if status == "reconstruction_unavailable" else 4)
    return raw, exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Build one normalized EURUSD reconstruction canary")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--estate", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434")
    parser.add_argument("--training-days", type=int, default=365)
    args = parser.parse_args()

    dataset = args.dataset.resolve()
    estate = args.estate.resolve()
    receipt = args.receipt.resolve()
    if estate == receipt:
        raise ValueError("canary estate and receipt paths must differ")
    existing = _existing_receipt(receipt)
    if existing is not None:
        raw, exit_code = existing
        print(json.dumps(raw, indent=2, sort_keys=True, allow_nan=False, default=str))
        return exit_code
    if not dataset.is_file():
        raise ValueError("frozen canary dataset missing")
    if args.training_days < 30 or args.training_days > 3650:
        raise ValueError("training-days out of bounded range")

    proposal = _proposal()
    model_digest = installed_model_digest(args.model, base_url=args.ollama)
    reconstruction = _existing_canary(estate, proposal.fingerprint)
    population_rows: list[dict[str, object]] = []

    if reconstruction is None:
        builder = StrategyEstateBuilder(
            reconstructor=BoundedRetryOllamaStrategyReconstructor(base_url=args.ollama),
            classifier=OllamaStrategyClassifier(base_url=args.ollama),
        )
        result = builder.populate(
            (proposal,),
            model_tag=args.model,
            model_digest=model_digest,
            allowed_symbols=CANARY_SYMBOLS,
            allowed_timeframes=CANARY_TIMEFRAMES,
            allowed_features=CANARY_FEATURES,
            allowed_sessions=CANARY_SESSIONS,
            estate_path=estate,
        )
        population_rows = [
            {
                "proposal_id": row.proposal_id,
                "status": row.status.value,
                "reconstruction_fingerprint": row.reconstruction_fingerprint,
                "reason": row.reason,
            }
            for row in result.rows
        ]
        reconstruction = _existing_canary(estate, proposal.fingerprint)

    if reconstruction is None:
        payload = {
            "protocol": CANARY_PROTOCOL,
            "status": "reconstruction_unavailable",
            "proposal_id": proposal.proposal_id,
            "proposal_fingerprint": proposal.fingerprint,
            "model_tag": args.model,
            "model_digest": model_digest,
            "dataset_sha256": sha256(dataset.read_bytes()).hexdigest(),
            "allowed_features": CANARY_FEATURES,
            "population": population_rows,
            "authority": _authority(),
        }
        return _write_receipt(receipt, payload, exit_code=3)

    candidate = reconstruction.candidate_spec
    if "EURUSD" not in {value.upper() for value in reconstruction.symbols}:
        raise ValueError("canary reconstruction symbol identity drift")

    runtime = augment_runtime_bars(build_runtime_bars(_load_bars(dataset)))
    runtime = _bind_sessions(runtime, candidate.session_filters)
    if not runtime:
        raise ValueError("canary produced no runtime bars")
    start = runtime[0].at
    cutoff = start + timedelta(days=args.training_days)
    training = tuple(row for row in runtime if row.at < cutoff)
    if not training:
        raise ValueError("canary produced no bounded PIT training rows")

    if candidate.event_exclusion_minutes:
        semantic_payload = {
            "status": "unsupported_event_hypothesis",
            "reason": "concept-only seed declares no event filter evidence",
            "total_rows": len(training),
            "session_eligible_rows": 0,
            "entry_match_count": 0,
            "clauses": [],
        }
        canary_status = "rejected"
    else:
        assessment = assess_reconstruction_semantics(candidate, training)
        semantic_payload = {
            "status": assessment.status,
            "reason": assessment.reason,
            "total_rows": assessment.total_rows,
            "session_eligible_rows": assessment.session_eligible_rows,
            "entry_match_count": assessment.entry_match_count,
            "clauses": [asdict(row) | {"dead": row.dead} for row in assessment.clauses],
        }
        canary_status = "activatable" if assessment.valid and assessment.entry_match_count > 0 else "rejected"

    payload = {
        "protocol": CANARY_PROTOCOL,
        "status": canary_status,
        "proposal_id": proposal.proposal_id,
        "proposal_fingerprint": proposal.fingerprint,
        "model_tag": args.model,
        "model_digest": model_digest,
        "dataset_sha256": sha256(dataset.read_bytes()).hexdigest(),
        "estate_sha256": sha256(estate.read_bytes()).hexdigest(),
        "reconstruction_fingerprint": reconstruction.fingerprint,
        "strategy_fingerprint": candidate.strategy_hash,
        "strategy_id": candidate.strategy_id,
        "allowed_features": CANARY_FEATURES,
        "training_start": training[0].at.isoformat(),
        "training_end": training[-1].at.isoformat(),
        "training_rows": len(training),
        "semantic_assessment": semantic_payload,
        "population": population_rows,
        "threshold_tuning_performed": False,
        "main_strategy_estate_modified": False,
        "authority": _authority(),
    }
    return _write_receipt(receipt, payload, exit_code=0 if canary_status == "activatable" else 4)


def _authority() -> dict[str, bool]:
    return {
        "broker_write": False,
        "live_write": False,
        "custody_write": False,
        "promotion": False,
        "retry": False,
        "risk_override": False,
        "guardian_override": False,
    }


def _write_receipt(path: Path, payload: dict[str, object], *, exit_code: int) -> int:
    payload["receipt_fingerprint"] = _fingerprint(payload)
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_text(encoding="utf-8") != rendered:
        raise ValueError("canary receipt already exists with different bytes")
    path.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
