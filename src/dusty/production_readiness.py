from __future__ import annotations

"""M204 Restricted Production Readiness certification.

M204 is the final software/governance integration gate before any explicit live
account authorization. Passing M204 means the frozen release is eligible for a
separate human/live-account authorization step. It never grants broker-write or
live-write authority itself.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json

from .long_running_soak import LongRunningSoakCertification, LongRunningSoakStatus
from .production_observability import FirmHealth, ProductionHealthSnapshot
from .release_certification import ReleaseRollbackCertification, ReleaseCertificationStatus
from .security_boundary import SecurityBoundaryCertification, SecurityBoundaryStatus


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _commit(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires full Git SHA-1")
    return rendered


class ProductionReadinessStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    READY_FOR_EXPLICIT_LIVE_AUTHORIZATION = "ready_for_explicit_live_authorization"


@dataclass(frozen=True, slots=True)
class RestrictedProductionEvidence:
    source_commit: str
    runtime_source_commit: str
    release_source_commit: str
    soak: LongRunningSoakCertification
    security: SecurityBoundaryCertification
    health: ProductionHealthSnapshot
    release: ReleaseRollbackCertification
    exact_terminal_identity_bound: bool
    exact_account_identity_bound: bool
    exact_broker_profile_bound: bool
    strategy_champion_frozen: bool
    portfolio_risk_constitution_bound: bool
    guardian_bound: bool
    demo_and_live_state_separation_proven: bool
    explicit_live_authorization_absent: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commit", _commit(self.source_commit, "M204 source commit"))
        object.__setattr__(self, "runtime_source_commit", _commit(self.runtime_source_commit, "runtime source commit"))
        object.__setattr__(self, "release_source_commit", _commit(self.release_source_commit, "release source commit"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m204-restricted-production-evidence-v1",
            self.source_commit,
            self.runtime_source_commit,
            self.release_source_commit,
            self.soak.fingerprint,
            self.security.fingerprint,
            self.health.fingerprint,
            self.release.fingerprint,
            self.exact_terminal_identity_bound,
            self.exact_account_identity_bound,
            self.exact_broker_profile_bound,
            self.strategy_champion_frozen,
            self.portfolio_risk_constitution_bound,
            self.guardian_bound,
            self.demo_and_live_state_separation_proven,
            self.explicit_live_authorization_absent,
        ))


@dataclass(frozen=True, slots=True)
class RestrictedProductionCertification:
    status: ProductionReadinessStatus
    evidence_fingerprint: str
    blockers: tuple[str, ...]

    broker_write_authority = False
    live_write_authority = False
    live_account_authorization = False
    install_authority = False
    credential_access_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    @property
    def ready_for_human_live_authorization(self) -> bool:
        return self.status is ProductionReadinessStatus.READY_FOR_EXPLICIT_LIVE_AUTHORIZATION

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m204-restricted-production-certification-v1",
            self.status.value,
            self.evidence_fingerprint,
            self.blockers,
        ))


def certify_restricted_production(
    evidence: RestrictedProductionEvidence,
) -> RestrictedProductionCertification:
    blockers: list[str] = []
    hard_reject = False

    if len({evidence.source_commit, evidence.runtime_source_commit, evidence.release_source_commit}) != 1:
        blockers.append("software_identity_drift")
        hard_reject = True

    if evidence.soak.status is LongRunningSoakStatus.REJECTED:
        blockers.append("m200_soak_rejected")
        hard_reject = True
    elif evidence.soak.status is not LongRunningSoakStatus.CERTIFIED:
        blockers.append("m200_soak_not_certified")

    if evidence.security.status is SecurityBoundaryStatus.REJECTED:
        blockers.append("m201_security_rejected")
        hard_reject = True
    elif evidence.security.status is not SecurityBoundaryStatus.CERTIFIED:
        blockers.append("m201_security_not_certified")

    if evidence.health.health is FirmHealth.HALTED:
        blockers.append("m202_firm_health_halted")
        hard_reject = True
    elif evidence.health.health is not FirmHealth.HEALTHY:
        blockers.append("m202_firm_health_not_healthy")

    if evidence.release.status is ReleaseCertificationStatus.REJECTED:
        blockers.append("m203_release_rejected")
        hard_reject = True
    elif evidence.release.status is not ReleaseCertificationStatus.CERTIFIED:
        blockers.append("m203_release_not_certified")

    required = (
        (evidence.exact_terminal_identity_bound, "exact_terminal_identity_not_bound"),
        (evidence.exact_account_identity_bound, "exact_account_identity_not_bound"),
        (evidence.exact_broker_profile_bound, "exact_broker_profile_not_bound"),
        (evidence.strategy_champion_frozen, "champion_not_frozen"),
        (evidence.portfolio_risk_constitution_bound, "portfolio_risk_not_bound"),
        (evidence.guardian_bound, "guardian_not_bound"),
        (evidence.demo_and_live_state_separation_proven, "demo_live_separation_not_proven"),
        (evidence.explicit_live_authorization_absent, "live_authorization_already_present"),
    )
    for ok, reason in required:
        if not ok:
            blockers.append(reason)

    if not evidence.explicit_live_authorization_absent:
        hard_reject = True

    blockers = list(dict.fromkeys(blockers))
    if hard_reject:
        status = ProductionReadinessStatus.REJECTED
    elif blockers:
        status = ProductionReadinessStatus.PENDING
    else:
        status = ProductionReadinessStatus.READY_FOR_EXPLICIT_LIVE_AUTHORIZATION

    return RestrictedProductionCertification(
        status=status,
        evidence_fingerprint=evidence.fingerprint,
        blockers=tuple(blockers),
    )
