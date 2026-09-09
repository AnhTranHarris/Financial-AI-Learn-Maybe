from __future__ import annotations

"""Production admission for M194.1 native Demo runtime start."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json

from .champion_registry import ChampionLifecycleState, FrozenChampionRegistry
from .m185_production_custody import ProductionChampionCustodyEnvelope
from .m194_native_demo_journal import SQLiteM194NativeEvidenceJournal
from .m194_native_demo_preflight import NativeDemoPreflightAssessment, NativeDemoTerminalSnapshot
from .m194_native_demo_runtime import M194NativeRunIdentity, start_native_demo_run


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class M194ProductionRuntimeAdmission:
    production_custody_fingerprint: str
    champion_fingerprint: str
    lane_id: str
    native_run_identity_fingerprint: str
    source_commit: str

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    position_mutation_authority = False
    promotion_authority = False
    risk_override_authority = False

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m194-production-runtime-admission-v1",
            "production_custody_fingerprint": self.production_custody_fingerprint,
            "champion_fingerprint": self.champion_fingerprint,
            "lane_id": self.lane_id,
            "native_run_identity_fingerprint": self.native_run_identity_fingerprint,
            "source_commit": self.source_commit,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "retry": False,
                "position_mutation": False,
                "promotion": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def start_production_native_demo_run(
    *,
    custody: ProductionChampionCustodyEnvelope,
    registry: FrozenChampionRegistry,
    journal: SQLiteM194NativeEvidenceJournal,
    run_id: str,
    source_commit: str,
    snapshot: NativeDemoTerminalSnapshot,
    assessment: NativeDemoPreflightAssessment,
    started_at: datetime,
) -> tuple[M194ProductionRuntimeAdmission, M194NativeRunIdentity]:
    champion = custody.champion_record
    integrity_ok, errors = registry.integrity_check()
    if not integrity_ok:
        raise RuntimeError(f"M194 production registry integrity failure: {errors}")
    if registry.state(champion.fingerprint) is not ChampionLifecycleState.ACTIVE:
        raise PermissionError("M194 production custody Champion is not ACTIVE")
    active = registry.active_for_lane(champion.lane_id)
    if active is None or active.fingerprint != champion.fingerprint:
        raise PermissionError("M194 production custody Champion is not unique active Champion for lane")
    if active.deployment_fingerprint != champion.deployment_fingerprint:
        raise ValueError("M194 production Champion deployment identity drift")
    if active.strategy_fingerprint != champion.strategy_fingerprint:
        raise ValueError("M194 production Champion strategy identity drift")

    identity = start_native_demo_run(
        registry=registry,
        journal=journal,
        lane_id=champion.lane_id,
        run_id=run_id,
        source_commit=source_commit,
        snapshot=snapshot,
        assessment=assessment,
        started_at=started_at,
    )
    if identity.champion_fingerprint != champion.fingerprint or identity.lane_id != champion.lane_id:
        raise RuntimeError("M194 native run identity drifted from production custody")
    admission = M194ProductionRuntimeAdmission(
        production_custody_fingerprint=custody.fingerprint,
        champion_fingerprint=champion.fingerprint,
        lane_id=champion.lane_id,
        native_run_identity_fingerprint=identity.fingerprint,
        source_commit=identity.source_commit,
    )
    return admission, identity
