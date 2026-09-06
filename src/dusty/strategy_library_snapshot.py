from __future__ import annotations

"""Hash-pinned disk transport for M196.5 reconstructed research packages.

The Windows research worker is spawned in a fresh Python interpreter, so an
in-memory registry would silently diverge between the UI and worker.  This
module transports only research reconstructions through an immutable JSON
snapshot whose exact bytes are SHA-256 pinned in the process environment.

Trading Skills are intentionally *not* deserialized here: their ACTIVE / Demo
status must be rebuilt from M185/M194 authoritative evidence rather than trusted
from a self-described JSON file.
"""

from dataclasses import asdict
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable, Mapping

from .experience import TradeSide
from .research import Clause, RuleOp
from .strategy_ir import (
    ExecutionSensitivity,
    ExitPlan,
    GroupMode,
    RuleGroup,
    StrategySpecV2,
)
from .trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)


LIBRARY_PATH_ENV = "DUSTY_RECONSTRUCTION_LIBRARY"
LIBRARY_SHA256_ENV = "DUSTY_RECONSTRUCTION_LIBRARY_SHA256"
_PROTOCOL = "dusty-m1965-reconstruction-library-v1"


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _required_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _exact_keys(row: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(row) != expected:
        missing = sorted(expected - set(row))
        extra = sorted(set(row) - expected)
        raise ValueError(f"{label} schema mismatch missing={missing} extra={extra}")


def _scalar(value: object, label: str) -> bool | int | float | str:
    if not isinstance(value, (bool, int, float, str)):
        raise ValueError(f"{label} must be a scalar")
    return value


def _spec_payload(spec: StrategySpecV2) -> dict[str, object]:
    return {
        "strategy_id": spec.strategy_id,
        "direction": spec.direction.value,
        "entry_groups": [
            {
                "mode": group.mode.value,
                "clauses": [
                    {"feature": clause.feature, "op": clause.op.value, "value": clause.value}
                    for clause in group.clauses
                ],
            }
            for group in spec.entry_groups
        ],
        "exit_plan": asdict(spec.exit_plan),
        "decision_timeframe_minutes": spec.decision_timeframe_minutes,
        "intended_horizon_minutes": spec.intended_horizon_minutes,
        "session_filters": list(spec.session_filters),
        "event_exclusion_minutes": spec.event_exclusion_minutes,
        "cooldown_steps": spec.cooldown_steps,
        "scale_in_limit": spec.scale_in_limit,
        "scale_out_fractions": list(spec.scale_out_fractions),
        "cost_bps": spec.cost_bps,
        "execution_sensitivity": spec.execution_sensitivity.value,
        "is_scalping": spec.is_scalping,
        "is_hft": spec.is_hft,
        "martingale": spec.martingale,
        "loss_recovery_sizing": spec.loss_recovery_sizing,
        "unbounded_averaging": spec.unbounded_averaging,
        "schema_version": spec.schema_version,
    }


def _spec_from_payload(value: object) -> StrategySpecV2:
    row = _required_mapping(value, "candidate_spec")
    expected = {
        "strategy_id", "direction", "entry_groups", "exit_plan",
        "decision_timeframe_minutes", "intended_horizon_minutes", "session_filters",
        "event_exclusion_minutes", "cooldown_steps", "scale_in_limit",
        "scale_out_fractions", "cost_bps", "execution_sensitivity", "is_scalping",
        "is_hft", "martingale", "loss_recovery_sizing", "unbounded_averaging",
        "schema_version",
    }
    _exact_keys(row, expected, "candidate_spec")
    groups_raw = row["entry_groups"]
    if not isinstance(groups_raw, list) or not groups_raw:
        raise ValueError("candidate_spec entry_groups must be a nonempty list")
    groups: list[RuleGroup] = []
    for group_raw in groups_raw:
        group = _required_mapping(group_raw, "entry_group")
        _exact_keys(group, {"mode", "clauses"}, "entry_group")
        clauses_raw = group["clauses"]
        if not isinstance(clauses_raw, list) or not clauses_raw:
            raise ValueError("entry_group clauses must be a nonempty list")
        clauses: list[Clause] = []
        for clause_raw in clauses_raw:
            clause = _required_mapping(clause_raw, "clause")
            _exact_keys(clause, {"feature", "op", "value"}, "clause")
            clauses.append(Clause(str(clause["feature"]), RuleOp(str(clause["op"])), _scalar(clause["value"], "clause value")))
        groups.append(RuleGroup(tuple(clauses), GroupMode(str(group["mode"]))))
    exit_raw = _required_mapping(row["exit_plan"], "exit_plan")
    _exact_keys(exit_raw, {"stop_rule", "target_rule", "trailing_rule", "breakeven_rule", "max_hold_steps"}, "exit_plan")
    filters = row["session_filters"]
    fractions = row["scale_out_fractions"]
    if not isinstance(filters, list) or any(not isinstance(v, str) for v in filters):
        raise ValueError("session_filters must be strings")
    if not isinstance(fractions, list):
        raise ValueError("scale_out_fractions must be a list")
    for flag in ("is_scalping", "is_hft", "martingale", "loss_recovery_sizing", "unbounded_averaging"):
        if type(row[flag]) is not bool:
            raise ValueError(f"{flag} must be boolean")
    return StrategySpecV2(
        strategy_id=str(row["strategy_id"]),
        direction=TradeSide(str(row["direction"])),
        entry_groups=tuple(groups),
        exit_plan=ExitPlan(
            str(exit_raw["stop_rule"]),
            str(exit_raw["target_rule"]),
            str(exit_raw["trailing_rule"]),
            str(exit_raw["breakeven_rule"]),
            int(exit_raw["max_hold_steps"]),
        ),
        decision_timeframe_minutes=int(row["decision_timeframe_minutes"]),
        intended_horizon_minutes=int(row["intended_horizon_minutes"]),
        session_filters=tuple(filters),
        event_exclusion_minutes=int(row["event_exclusion_minutes"]),
        cooldown_steps=int(row["cooldown_steps"]),
        scale_in_limit=int(row["scale_in_limit"]),
        scale_out_fractions=tuple(float(v) for v in fractions),
        cost_bps=float(row["cost_bps"]),
        execution_sensitivity=ExecutionSensitivity(str(row["execution_sensitivity"])),
        is_scalping=row["is_scalping"],
        is_hft=row["is_hft"],
        martingale=row["martingale"],
        loss_recovery_sizing=row["loss_recovery_sizing"],
        unbounded_averaging=row["unbounded_averaging"],
        schema_version=int(row["schema_version"]),
    )


def _reconstruction_payload(row: StrategyReconstruction) -> dict[str, object]:
    return {
        "fingerprint": row.fingerprint,
        "proposal_fingerprint": row.proposal_fingerprint,
        "source_id": row.source_id,
        "source_url": row.source_url,
        "source_content_sha256": row.source_content_sha256,
        "source_family_fingerprint": row.source_family_fingerprint,
        "title": row.title,
        "symbols": list(row.symbols),
        "timeframe": row.timeframe,
        "candidate_spec": _spec_payload(row.candidate_spec),
        "rules": [{"name": rule.name, "value": rule.value, "basis": rule.basis.value} for rule in row.rules],
        "unresolved_source_rules": list(row.unresolved_source_rules),
        "actor": row.actor.value,
        "actor_fingerprint": row.actor_fingerprint,
        "created_at": row.created_at.isoformat(),
        "schema_version": row.schema_version,
    }


def reconstruction_library_payload(rows: Iterable[StrategyReconstruction]) -> dict[str, object]:
    items = tuple(rows)
    if len({row.fingerprint for row in items}) != len(items):
        raise ValueError("reconstruction library cannot contain duplicate identities")
    return {
        "protocol": _PROTOCOL,
        "reconstructions": [_reconstruction_payload(row) for row in sorted(items, key=lambda row: row.fingerprint)],
    }


def reconstruction_library_bytes(rows: Iterable[StrategyReconstruction]) -> bytes:
    return _canonical(reconstruction_library_payload(rows))


def write_reconstruction_library(path: str | Path, rows: Iterable[StrategyReconstruction]) -> str:
    """Atomically write immutable worker transport and return exact byte digest."""
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = reconstruction_library_bytes(rows)
    with NamedTemporaryFile("wb", prefix=destination.name + ".", suffix=".tmp", dir=destination.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return sha256(content).hexdigest()


def _reconstruction_from_payload(value: object) -> StrategyReconstruction:
    row = _required_mapping(value, "reconstruction")
    expected = {
        "fingerprint", "proposal_fingerprint", "source_id", "source_url",
        "source_content_sha256", "source_family_fingerprint", "title", "symbols",
        "timeframe", "candidate_spec", "rules", "unresolved_source_rules", "actor",
        "actor_fingerprint", "created_at", "schema_version",
    }
    _exact_keys(row, expected, "reconstruction")
    symbols, unresolved, rules_raw = row["symbols"], row["unresolved_source_rules"], row["rules"]
    if not isinstance(symbols, list) or any(not isinstance(v, str) for v in symbols):
        raise ValueError("reconstruction symbols must be strings")
    if not isinstance(unresolved, list) or any(not isinstance(v, str) for v in unresolved):
        raise ValueError("unresolved_source_rules must be strings")
    if not isinstance(rules_raw, list) or not rules_raw:
        raise ValueError("reconstruction rules must be a nonempty list")
    rules = []
    for raw in rules_raw:
        rule = _required_mapping(raw, "reconstruction rule")
        _exact_keys(rule, {"name", "value", "basis"}, "reconstruction rule")
        rules.append(ReconstructionRule(str(rule["name"]), str(rule["value"]), ReconstructionRuleBasis(str(rule["basis"]))))
    result = StrategyReconstruction(
        str(row["proposal_fingerprint"]),
        str(row["source_id"]),
        str(row["source_url"]),
        str(row["source_content_sha256"]),
        str(row["source_family_fingerprint"]),
        str(row["title"]),
        tuple(symbols),
        str(row["timeframe"]),
        _spec_from_payload(row["candidate_spec"]),
        tuple(rules),
        tuple(unresolved),
        ReconstructionActor(str(row["actor"])),
        str(row["actor_fingerprint"]),
        datetime_from_iso(str(row["created_at"])),
        int(row["schema_version"]),
    )
    if result.fingerprint != str(row["fingerprint"]).lower():
        raise ValueError("reconstruction fingerprint does not match payload")
    return result


def datetime_from_iso(value: str):
    from datetime import datetime
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("invalid reconstruction created_at") from exc


def load_reconstruction_library(path: str | Path, expected_sha256: str) -> tuple[StrategyReconstruction, ...]:
    expected = str(expected_sha256).strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("reconstruction library expected digest must be SHA-256")
    content = Path(path).resolve().read_bytes()
    if sha256(content).hexdigest() != expected:
        raise ValueError("reconstruction library byte digest mismatch")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("reconstruction library is not canonical UTF-8 JSON") from exc
    root = _required_mapping(payload, "reconstruction library")
    _exact_keys(root, {"protocol", "reconstructions"}, "reconstruction library")
    if root["protocol"] != _PROTOCOL:
        raise ValueError("unsupported reconstruction library protocol")
    raw_rows = root["reconstructions"]
    if not isinstance(raw_rows, list):
        raise ValueError("reconstruction library rows must be a list")
    rows = tuple(_reconstruction_from_payload(row) for row in raw_rows)
    if len({row.fingerprint for row in rows}) != len(rows):
        raise ValueError("reconstruction library identities must be unique")
    return rows


def configured_reconstructions(environ: Mapping[str, str] | None = None) -> tuple[StrategyReconstruction, ...]:
    """Load the exact snapshot inherited by parent and spawned worker."""
    env = os.environ if environ is None else environ
    path = str(env.get(LIBRARY_PATH_ENV, "")).strip()
    digest = str(env.get(LIBRARY_SHA256_ENV, "")).strip()
    if not path and not digest:
        return ()
    if not path or not digest:
        raise ValueError("reconstruction library path and digest must be configured together")
    return load_reconstruction_library(path, digest)
