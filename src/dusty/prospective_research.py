"""Local pre-window registrations, not trusted timestamps or trading certificates.

SQLite enforces one evaluation attempt per plan across desktop processes. Receipts
are hash-bound, not signed: the same OS user can rewrite the DB, clock and receipts.
Preserve a receipt independently before the window for stronger external evidence.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any


PROTOCOL = "local-prospective-holdout-v1"
SCREEN = {"minimum_closed_trades": 20, "net_pnl_strictly_above": 0.0,
          "maximum_marked_drawdown": 0.02, "promotion_eligible": False}


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return sha256(canonical(value).encode()).hexdigest()


def utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("registration_clock_requires_UTC")
    return value


def validate_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    if set(receipt) != {"plan_id", "payload"} or digest(receipt["payload"]) != receipt["plan_id"]:
        raise ValueError("registered_plan_hash_mismatch")
    body = receipt["payload"]
    if (set(body) != {"protocol", "created_at", "request", "screen", "timestamp_authority"}
            or body["protocol"] != PROTOCOL or body["screen"] != SCREEN
            or body["timestamp_authority"] != "LOCAL_CLOCK_NOT_INDEPENDENTLY_ATTESTED"):
        raise ValueError("registered_plan_protocol_mismatch")
    request = body["request"]
    plan = request.get("evaluation_plan")
    if not plan or not request["settings"]["holdout_days"]:
        raise ValueError("registration_requires_fixed_holdout")
    created = utc(datetime.fromisoformat(body["created_at"]))
    holdout = utc(datetime.fromisoformat(plan["holdout_start"]))
    if not created < holdout <= created + timedelta(days=30):
        raise ValueError("register_before_holdout_starts_within_30_days")
    if utc(datetime.fromisoformat(request["snapshot_at"])) > created:
        raise ValueError("registration_snapshot_is_in_the_future")
    return request


def validate_for_evaluation(receipt: dict[str, Any], current: dict[str, Any], now: datetime) -> None:
    frozen = validate_receipt(receipt)
    if utc(now) < datetime.fromisoformat(frozen["end"]):
        raise ValueError("registered_window_not_finished_wait_until_fixed_end")
    # Account balance may change while we wait. The simulation must still use the
    # registered capital; runtime, code, identity, symbol economics and rules cannot.
    comparable = dict(current)
    for field in ("snapshot_at", "growth_starting_balance"):
        comparable[field] = frozen[field]
    if comparable != frozen:
        raise ValueError("registered_configuration_changed_do_not_rebind_old_plan")


class ProspectiveRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=1, isolation_level=None)
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE TABLE IF NOT EXISTS plans(plan_id TEXT PRIMARY KEY, "
                       "experiment_key TEXT UNIQUE NOT NULL, receipt TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS attempts(plan_id TEXT PRIMARY KEY REFERENCES plans(plan_id), "
                       "run_id TEXT UNIQUE NOT NULL, attempted_at TEXT NOT NULL)")
            yield db
        finally:
            db.close()

    def register(self, request: dict[str, Any], *, now: datetime) -> dict[str, Any]:
        payload = {"protocol": PROTOCOL, "created_at": utc(now).isoformat(), "request": request,
                   "screen": dict(SCREEN), "timestamp_authority": "LOCAL_CLOCK_NOT_INDEPENDENTLY_ATTESTED"}
        receipt = {"plan_id": digest(payload), "payload": payload}
        validate_receipt(receipt)
        experiment = {k: v for k, v in request.items() if k != "snapshot_at"}
        with self._connection() as db:
            try:
                db.execute("INSERT INTO plans VALUES(?,?,?)", (receipt["plan_id"], digest(experiment), canonical(receipt)))
            except sqlite3.IntegrityError as exc:
                raise ValueError("identical_plan_already_registered_open_saved_plans") from exc
        return receipt

    def get(self, plan_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{64}", plan_id):
            raise ValueError("invalid_registered_plan_id")
        with self._connection() as db:
            row = db.execute("SELECT receipt FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None or len(row[0]) > 100_000:
            raise ValueError("registered_plan_missing_or_oversized")
        receipt = json.loads(row[0])
        validate_receipt(receipt)
        if receipt["plan_id"] != plan_id:
            raise ValueError("registered_plan_identity_mismatch")
        return receipt

    def list_plans(self) -> tuple[dict[str, Any], ...]:
        with self._connection() as db:
            rows = db.execute("SELECT p.plan_id,a.run_id FROM plans p LEFT JOIN attempts a "
                              "ON a.plan_id=p.plan_id ORDER BY p.rowid DESC LIMIT 200").fetchall()
        return tuple({"receipt": self.get(plan_id), "run_id": run_id} for plan_id, run_id in rows)

    def claim(self, plan_id: str, *, current: dict[str, Any], now: datetime, run_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[0-9a-f]{32}", run_id):
            raise ValueError("invalid_research_run_id")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT receipt FROM plans WHERE plan_id=?", (plan_id,)).fetchone()
                if row is None or len(row[0]) > 100_000:
                    raise ValueError("registered_plan_missing_or_oversized")
                receipt = json.loads(row[0])
                if receipt["plan_id"] != plan_id:
                    raise ValueError("registered_plan_identity_mismatch")
                validate_for_evaluation(receipt, current, now)
                db.execute("INSERT INTO attempts VALUES(?,?,?)", (plan_id, run_id, now.isoformat()))
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                raise ValueError("registered_plan_already_attempted_no_automatic_retry") from exc
            except Exception:
                db.rollback()
                raise
        return receipt


def screen_result(receipt: dict[str, Any], laboratory: dict[str, Any]) -> dict[str, Any]:
    validate_receipt(receipt)
    growth = laboratory["growth_backtest"]
    if (type(growth["trade_count"]) is not int or growth["trade_count"] < 0
            or any(isinstance(growth[key], bool) or not isinstance(growth[key], (int, float))
                   or not math.isfinite(growth[key]) for key in ("net_pnl", "max_drawdown_fraction"))
            or not 0 <= growth["max_drawdown_fraction"] <= 1):
        raise ValueError("invalid_prospective_screen_metrics")
    reasons = []
    if growth["trade_count"] < SCREEN["minimum_closed_trades"]:
        reasons.append("insufficient_closed_trades")
    if growth["net_pnl"] <= SCREEN["net_pnl_strictly_above"]:
        reasons.append("nonpositive_net_pnl")
    if growth["max_drawdown_fraction"] > SCREEN["maximum_marked_drawdown"]:
        reasons.append("marked_drawdown_exceeds_screen")
    return {"plan_id": receipt["plan_id"], "screen": dict(SCREEN), "screen_passed": not reasons,
            "reasons": reasons, "promotion_eligible": False, "statistical_confidence_claimed": False,
            "remaining_evidence": ["broker_costs_not_verified", "native_parity_missing",
                                   "local_registration_not_independently_timestamped",
                                   "multiple_testing_not_corrected", "qualified_demo_evidence_missing"]}
