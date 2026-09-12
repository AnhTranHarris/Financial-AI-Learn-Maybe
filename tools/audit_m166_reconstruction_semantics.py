from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dusty.experience import TradeSide
from dusty.m166_provisional_quant import build_runtime_bars
from dusty.mt5worker import MT5Bar
from dusty.reconstruction_semantics import assess_reconstruction_semantics
from dusty.research import Clause, RuleOp
from dusty.research_sessions import matching_research_session
from dusty.runtime import RuntimeBar
from dusty.strategy_ir import ExecutionSensitivity, ExitPlan, GroupMode, RuleGroup, StrategySpecV2


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _sha(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _dt(value: object) -> datetime:
    text = str(value).replace("Z", "+00:00")
    result = datetime.fromisoformat(text)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("bar timestamp must be timezone-aware")
    return result


def _load_bars(path: Path) -> tuple[MT5Bar, ...]:
    rows: list[MT5Bar] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"dataset row {line_number} must be object")
            at = row.get("at", row.get("time", row.get("time_utc")))
            rows.append(MT5Bar(
                _dt(at),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                int(row.get("tick_volume", 0)),
                int(row.get("spread", 0)),
                int(row.get("real_volume", 0)),
            ))
    if not rows:
        raise ValueError("dataset contains no bars")
    return tuple(rows)


def _spec(row: dict[str, object]) -> StrategySpecV2:
    groups: list[RuleGroup] = []
    for raw_group in row["entry_groups"]:  # type: ignore[index]
        clauses = tuple(
            Clause(str(raw["feature"]), RuleOp(str(raw["op"])), raw["value"])
            for raw in raw_group["clauses"]
        )
        groups.append(RuleGroup(clauses, GroupMode(str(raw_group["mode"]))))
    exit_row = row["exit_plan"]
    return StrategySpecV2(
        strategy_id=str(row["strategy_id"]),
        direction=TradeSide(str(row["direction"])),
        entry_groups=tuple(groups),
        exit_plan=ExitPlan(
            str(exit_row["stop_rule"]),
            str(exit_row.get("target_rule", "")),
            str(exit_row.get("trailing_rule", "off")),
            str(exit_row.get("breakeven_rule", "off")),
            int(exit_row.get("max_hold_steps", 1)),
        ),
        decision_timeframe_minutes=int(row["decision_timeframe_minutes"]),
        intended_horizon_minutes=int(row["intended_horizon_minutes"]),
        session_filters=tuple(str(value) for value in row.get("session_filters", [])),
        event_exclusion_minutes=int(row.get("event_exclusion_minutes", 0)),
        cooldown_steps=int(row.get("cooldown_steps", 0)),
        scale_in_limit=int(row.get("scale_in_limit", 0)),
        scale_out_fractions=tuple(float(value) for value in row.get("scale_out_fractions", [])),
        cost_bps=float(row.get("cost_bps", 0.0)),
        execution_sensitivity=ExecutionSensitivity(str(row.get("execution_sensitivity", "normal"))),
        is_scalping=bool(row.get("is_scalping", False)),
        is_hft=bool(row.get("is_hft", False)),
        martingale=bool(row.get("martingale", False)),
        loss_recovery_sizing=bool(row.get("loss_recovery_sizing", False)),
        unbounded_averaging=bool(row.get("unbounded_averaging", False)),
        schema_version=int(row.get("schema_version", 2)),
    )


def _bind_sessions(rows: tuple[RuntimeBar, ...], sessions: tuple[str, ...]) -> tuple[RuntimeBar, ...]:
    if not sessions:
        return rows
    result: list[RuntimeBar] = []
    for row in rows:
        result.append(RuntimeBar(
            row.at,
            row.open,
            row.high,
            row.low,
            row.close,
            row.features,
            matching_research_session(row.at, sessions),
            row.event_blocked,
            row.execution_price,
        ))
    return tuple(result)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only semantic preflight for one M166 strategy identity")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--strategy-estate", type=Path, required=True)
    parser.add_argument("--strategy-fingerprint", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--training-days", type=int, default=365)
    args = parser.parse_args()

    estate = _load_json(args.strategy_estate)
    if not isinstance(estate, dict) or not isinstance(estate.get("reconstructions"), list):
        raise ValueError("strategy estate schema invalid")

    matches: list[tuple[dict[str, object], StrategySpecV2]] = []
    for reconstruction in estate["reconstructions"]:
        if not isinstance(reconstruction, dict) or not isinstance(reconstruction.get("candidate_spec"), dict):
            continue
        candidate = _spec(reconstruction["candidate_spec"])
        if candidate.strategy_hash == args.strategy_fingerprint:
            matches.append((reconstruction, candidate))
    if len(matches) != 1:
        raise ValueError("strategy fingerprint must resolve to exactly one reconstruction")

    reconstruction, candidate = matches[0]
    bars = _load_bars(args.dataset)
    runtime = _bind_sessions(build_runtime_bars(bars), candidate.session_filters)
    if not runtime:
        raise ValueError("dataset produced no completed runtime bars")

    start = runtime[0].at
    cutoff = start + __import__("datetime").timedelta(days=int(args.training_days))
    training = tuple(row for row in runtime if row.at < cutoff)
    assessment = assess_reconstruction_semantics(candidate, training)

    payload = {
        "protocol": "dusty-m166-reconstruction-semantic-audit-v1",
        "strategy_fingerprint": candidate.strategy_hash,
        "reconstruction_fingerprint": reconstruction.get("fingerprint"),
        "actor": reconstruction.get("actor"),
        "dataset_file_sha256": sha256(args.dataset.read_bytes()).hexdigest(),
        "training_start": training[0].at.isoformat() if training else None,
        "training_end": training[-1].at.isoformat() if training else None,
        "training_rows": len(training),
        "assessment": {
            "status": assessment.status,
            "reason": assessment.reason,
            "total_rows": assessment.total_rows,
            "session_eligible_rows": assessment.session_eligible_rows,
            "entry_match_count": assessment.entry_match_count,
            "clauses": [asdict(row) | {"dead": row.dead} for row in assessment.clauses],
        },
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "promotion": False,
            "retry": False,
            "risk_override": False,
        },
    }
    payload["audit_fingerprint"] = _sha(payload)
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False, default=str)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        if args.output.exists() and args.output.read_text(encoding="utf-8") != rendered + "\n":
            raise ValueError("semantic audit output already exists with different bytes")
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
