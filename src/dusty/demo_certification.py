from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Iterable


REQUIRED_MILESTONES = tuple(f"M{number}" for number in range(66, 75))


class ChaosCase(StrEnum):
    NETWORK_DISCONNECT = "network_disconnect"
    TERMINAL_RESTART = "terminal_restart"
    PROCESS_RESTART = "process_restart"
    LOGIN_DRIFT = "login_drift"
    DEMO_TO_LIVE_DRIFT = "demo_to_live_drift"
    PERMISSION_LOSS = "permission_loss"
    SPREAD_SPIKE = "spread_spike"
    SLIPPAGE_SPIKE = "slippage_spike"
    ORDER_REJECTION = "order_rejection"
    PARTIAL_FILL = "partial_fill"
    DUPLICATE_RESPONSE = "duplicate_response"
    MISSING_RESPONSE = "missing_response"
    MANUAL_POSITION = "manual_position"
    STOP_PROTECTION_FAILURE = "stop_protection_failure"
    LOW_DISK = "low_disk"
    HIGH_MEMORY = "high_memory"
    BACKTEST_OVERLOAD = "backtest_overload"
    STALE_MARKET_DATA = "stale_market_data"
    CLOCK_ANOMALY = "clock_anomaly"
    CAPITAL_INFEASIBLE = "capital_infeasible"


REQUIRED_CHAOS_CASES = tuple(ChaosCase)


@dataclass(frozen=True, slots=True)
class DemoMilestoneEvidence:
    milestone: str
    passed: bool
    artifact_hash: str
    data_fingerprint: str
    config_fingerprint: str
    test_fingerprint: str
    commit_sha: str

    def __post_init__(self) -> None:
        if self.milestone not in REQUIRED_MILESTONES:
            raise ValueError(f"unsupported demo milestone: {self.milestone}")
        if any(not value.strip() for value in (self.artifact_hash, self.data_fingerprint, self.config_fingerprint, self.test_fingerprint, self.commit_sha)):
            raise ValueError("demo milestone fingerprints are required")

    @property
    def evidence_hash(self) -> str:
        return sha256(_canonical(self.as_dict()).encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, object]:
        return {
            "milestone": self.milestone,
            "passed": self.passed,
            "artifact_hash": self.artifact_hash,
            "data_fingerprint": self.data_fingerprint,
            "config_fingerprint": self.config_fingerprint,
            "test_fingerprint": self.test_fingerprint,
            "commit_sha": self.commit_sha,
        }


@dataclass(frozen=True, slots=True)
class ChaosResult:
    case: ChaosCase
    passed: bool
    unauthorized_entry_attempts: int = 0
    artifact_hash: str = ""

    def __post_init__(self) -> None:
        if self.unauthorized_entry_attempts < 0:
            raise ValueError("chaos unauthorized-attempt count cannot be negative")
        if not self.artifact_hash.strip():
            raise ValueError("chaos result requires artifact hash")


@dataclass(frozen=True, slots=True)
class DeskRunEvidence:
    desk_run_id: str
    generation_id: str
    passed: bool
    session_fingerprint: str
    ledger_hash: str
    cycle_hash: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.desk_run_id, self.generation_id, self.session_fingerprint, self.ledger_hash, self.cycle_hash)):
            raise ValueError("desk run evidence is incomplete")

    @property
    def evidence_hash(self) -> str:
        return sha256(
            _canonical(
                {
                    "desk_run_id": self.desk_run_id,
                    "generation_id": self.generation_id,
                    "passed": self.passed,
                    "session_fingerprint": self.session_fingerprint,
                    "ledger_hash": self.ledger_hash,
                    "cycle_hash": self.cycle_hash,
                }
            ).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class DemoPhaseCertification:
    demo_desk_certified: bool
    live_write_authorized: bool
    certification_hash: str
    desk_pass_count: int
    reasons: tuple[str, ...]


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def certify_demo_phase(
    evidence: Iterable[DemoMilestoneEvidence],
    chaos_results: Iterable[ChaosResult],
    desk_runs: Iterable[DeskRunEvidence],
    *,
    current_commit_sha: str,
    required_desk_passes: int = 6,
) -> DemoPhaseCertification:
    """Certify the controlled Demo Desk. Even a full pass never grants live-money authority."""
    if not current_commit_sha.strip() or required_desk_passes < 1:
        raise ValueError("demo certification commit and desk requirement are required")
    reasons: list[str] = []
    rows = tuple(evidence)
    by_milestone: dict[str, DemoMilestoneEvidence] = {}
    for row in rows:
        if row.milestone in by_milestone:
            reasons.append(f"duplicate_evidence:{row.milestone}")
        else:
            by_milestone[row.milestone] = row
    for milestone in REQUIRED_MILESTONES:
        row = by_milestone.get(milestone)
        if row is None:
            reasons.append(f"missing_evidence:{milestone}")
            continue
        if not row.passed:
            reasons.append(f"milestone_failed:{milestone}")
        if row.commit_sha != current_commit_sha:
            reasons.append(f"commit_mismatch:{milestone}")

    chaos = tuple(chaos_results)
    chaos_map: dict[ChaosCase, ChaosResult] = {}
    for row in chaos:
        if row.case in chaos_map:
            reasons.append(f"duplicate_chaos:{row.case.value}")
        else:
            chaos_map[row.case] = row
    for case in REQUIRED_CHAOS_CASES:
        row = chaos_map.get(case)
        if row is None:
            reasons.append(f"missing_chaos:{case.value}")
            continue
        if not row.passed:
            reasons.append(f"chaos_failed:{case.value}")
        if row.unauthorized_entry_attempts:
            reasons.append(f"unsafe_entry_attempt:{case.value}")

    desks = tuple(desk_runs)
    if len({row.desk_run_id for row in desks}) != len(desks):
        reasons.append("duplicate_desk_run")
    failed_desks = tuple(row.desk_run_id for row in desks if not row.passed)
    if failed_desks:
        reasons.extend(f"desk_failed:{desk}" for desk in sorted(failed_desks))
    passing = tuple(row for row in desks if row.passed)
    if len(passing) < required_desk_passes:
        reasons.append("insufficient_passing_desks")

    payload = {
        "schema": "dusty-demo-phase-certification-v1",
        "commit": current_commit_sha,
        "milestones": [(name, by_milestone[name].evidence_hash) for name in REQUIRED_MILESTONES if name in by_milestone],
        "chaos": [(case.value, chaos_map[case].artifact_hash) for case in REQUIRED_CHAOS_CASES if case in chaos_map],
        "desks": [(row.desk_run_id, row.evidence_hash) for row in sorted(desks, key=lambda item: item.desk_run_id)],
        "reasons": reasons,
        "live_write_authorized": False,
    }
    digest = sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return DemoPhaseCertification(not reasons, False, digest, len(passing), tuple(reasons))
