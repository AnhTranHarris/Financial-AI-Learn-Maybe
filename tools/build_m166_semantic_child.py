from __future__ import annotations

"""Build an immutable sidecar child after a certified semantic-dead-clause audit.

The tool is broker-free and does not alter the input Strategy Estate or dataset.
It performs no threshold tuning. Only research-hypothesis clauses proven dead by
the supplied bounded PIT audit may be removed.
"""

import argparse
from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile

from dusty.m166_semantic_remediation import build_semantic_child
from dusty.reconstruction_semantics import ClauseActivation, ReconstructionSemanticAssessment
from dusty.strategy_estate import load_strategy_estate
from dusty.strategy_library_snapshot import reconstruction_library_bytes
from dusty.trading_skills import ReconstructionActor, StrategyReconstruction

PROTOCOL = "dusty-m166-semantic-child-builder-v1"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: object, label: str) -> str:
    rendered = str(value or "").strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256")
    return rendered


def _load_json(path: Path, label: str) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _verify_fingerprint(payload: dict[str, object], field: str, label: str) -> str:
    stored = _sha(payload.get(field), label)
    body = dict(payload)
    body.pop(field, None)
    if _digest(body) != stored:
        raise ValueError(f"{label} fingerprint mismatch")
    return stored


def _assessment(raw: object) -> ReconstructionSemanticAssessment:
    if not isinstance(raw, dict):
        raise ValueError("semantic audit assessment missing")
    clauses_raw = raw.get("clauses")
    if not isinstance(clauses_raw, list):
        raise ValueError("semantic audit clauses missing")
    clauses: list[ClauseActivation] = []
    for row in clauses_raw:
        if not isinstance(row, dict):
            raise ValueError("semantic audit clause must be object")
        clauses.append(ClauseActivation(
            int(row["group_index"]), int(row["clause_index"]), str(row["feature"]),
            str(row["operator"]), row["threshold"], int(row["available_count"]),
            int(row["true_count"]),
            None if row.get("observed_min") is None else float(row["observed_min"]),
            None if row.get("observed_max") is None else float(row["observed_max"]),
        ))
    return ReconstructionSemanticAssessment(
        int(raw["total_rows"]), int(raw["session_eligible_rows"]), int(raw["entry_match_count"]),
        tuple(clauses), str(raw["reason"]), str(raw["status"]),
    )


def _atomic_exact(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise RuntimeError(f"existing checkpoint differs from deterministic result: {path}")
        return
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Build immutable M166 semantic-remediation child")
    parser.add_argument("--strategy-estate", type=Path, required=True)
    parser.add_argument("--semantic-audit", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    estate_path = args.strategy_estate.resolve()
    audit_path = args.semantic_audit.resolve()
    if not estate_path.is_file() or not audit_path.is_file():
        raise FileNotFoundError("strategy estate and semantic audit must exist")

    audit = _load_json(audit_path, "semantic audit")
    if audit.get("protocol") != "dusty-m166-reconstruction-semantic-audit-v1":
        raise ValueError("unsupported semantic audit protocol")
    authority = audit.get("authority")
    if not isinstance(authority, dict) or any(authority.get(key) is not False for key in (
        "broker_write", "live_write", "custody_write", "promotion", "retry", "risk_override"
    )):
        raise PermissionError("semantic audit authority must remain false")
    audit_fp = _verify_fingerprint(audit, "audit_fingerprint", "semantic audit")
    parent_recon_fp = _sha(audit.get("reconstruction_fingerprint"), "parent reconstruction")
    parent_strategy_fp = _sha(audit.get("strategy_fingerprint"), "parent strategy")

    rows = load_strategy_estate(estate_path)
    matches = [
        row for row in rows
        if row.fingerprint == parent_recon_fp and row.candidate_spec.strategy_hash == parent_strategy_fp
    ]
    if len(matches) != 1:
        raise RuntimeError("Strategy Estate does not contain exactly one audited parent")
    parent = matches[0]

    child_spec, rules, receipt = build_semantic_child(
        parent, _assessment(audit.get("assessment")), audit_fingerprint=audit_fp,
    )
    actor_fp = _digest({
        "protocol": PROTOCOL,
        "parent_reconstruction_fingerprint": parent.fingerprint,
        "parent_strategy_fingerprint": parent.candidate_spec.strategy_hash,
        "audit_fingerprint": audit_fp,
        "child_strategy_fingerprint": child_spec.strategy_hash,
        "removed_clause_coordinates": receipt.removed_clause_coordinates,
    })
    child = StrategyReconstruction(
        parent.proposal_fingerprint,
        parent.source_id,
        parent.source_url,
        parent.source_content_sha256,
        parent.source_family_fingerprint,
        parent.title,
        parent.symbols,
        parent.timeframe,
        child_spec,
        rules,
        parent.unresolved_source_rules,
        ReconstructionActor.DUSTY_RESEARCH,
        actor_fp,
        parent.created_at,
        parent.schema_version,
    )
    if child.fingerprint == parent.fingerprint:
        raise RuntimeError("semantic child reconstruction identity did not change")

    ids = {row.candidate_spec.strategy_id for row in rows}
    if child.candidate_spec.strategy_id in ids:
        raise RuntimeError("semantic child strategy_id collides with Strategy Estate")
    sidecar = reconstruction_library_bytes((*rows, child))

    receipt_payload = {
        "protocol": PROTOCOL,
        "parent_reconstruction_fingerprint": parent.fingerprint,
        "parent_strategy_fingerprint": parent.candidate_spec.strategy_hash,
        "semantic_audit_fingerprint": audit_fp,
        "removed_clause_coordinates": [list(value) for value in receipt.removed_clause_coordinates],
        "child_reconstruction_fingerprint": child.fingerprint,
        "child_strategy_fingerprint": child.candidate_spec.strategy_hash,
        "child_strategy_id": child.candidate_spec.strategy_id,
        "reason": receipt.reason,
        "parent_preserved": True,
        "threshold_tuning_performed": False,
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "promotion": False,
            "retry": False,
            "risk_override": False,
        },
    }
    receipt_payload["receipt_fingerprint"] = _digest(receipt_payload)

    output_root = args.output_root.resolve()
    estate_out = output_root / "strategy-estate-semantic-child.json"
    receipt_out = output_root / "m166-semantic-child-receipt.json"
    _atomic_exact(estate_out, sidecar)
    _atomic_exact(receipt_out, (json.dumps(receipt_payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))

    print(json.dumps(receipt_payload | {
        "strategy_estate": str(estate_out),
        "receipt": str(receipt_out),
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
