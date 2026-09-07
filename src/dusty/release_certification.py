from __future__ import annotations

"""M203 deterministic release / rollback certification.

This module proves that a candidate release is content-addressed, bound to one
source commit and one predecessor, and has an explicit reversible state-schema
contract. It does not install software, mutate state, change MT5, or grant live
execution authority.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha256(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256")
    return rendered


def _commit(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires full Git SHA-1")
    return rendered


def _name(value: str, label: str) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered:
        raise ValueError(f"{label} must be non-empty and one line")
    return rendered


class ReleaseCertificationStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    CERTIFIED = "certified"


@dataclass(frozen=True, slots=True)
class ReleaseArtifact:
    relative_path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        path = _name(self.relative_path, "release artifact path").replace("\\", "/")
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError("release artifact path must be repository-relative")
        if self.size_bytes < 0:
            raise ValueError("release artifact size cannot be negative")
        object.__setattr__(self, "relative_path", path)
        object.__setattr__(self, "sha256", _sha256(self.sha256, "release artifact"))

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m203-release-artifact-v1", self.relative_path, self.sha256, self.size_bytes))


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    release_id: str
    source_commit: str
    source_tree_sha256: str
    python_abi: str
    state_schema_version: int
    artifacts: tuple[ReleaseArtifact, ...]
    predecessor_release_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "release_id", _name(self.release_id, "release id"))
        object.__setattr__(self, "source_commit", _commit(self.source_commit, "release source commit"))
        object.__setattr__(self, "source_tree_sha256", _sha256(self.source_tree_sha256, "source tree"))
        object.__setattr__(self, "python_abi", _name(self.python_abi, "python ABI"))
        if self.state_schema_version < 1:
            raise ValueError("state schema version must be positive")
        artifacts = tuple(sorted(self.artifacts, key=lambda row: row.relative_path))
        if not artifacts:
            raise ValueError("release manifest requires artifacts")
        if len({row.relative_path for row in artifacts}) != len(artifacts):
            raise ValueError("release artifact paths must be unique")
        object.__setattr__(self, "artifacts", artifacts)
        if self.predecessor_release_fingerprint is not None:
            object.__setattr__(
                self,
                "predecessor_release_fingerprint",
                _sha256(self.predecessor_release_fingerprint, "predecessor release"),
            )

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m203-release-manifest-v1",
            self.release_id,
            self.source_commit,
            self.source_tree_sha256,
            self.python_abi,
            self.state_schema_version,
            tuple(row.fingerprint for row in self.artifacts),
            self.predecessor_release_fingerprint,
        ))


@dataclass(frozen=True, slots=True)
class RollbackEvidence:
    candidate_release_fingerprint: str
    predecessor_release_fingerprint: str
    predecessor_artifacts_available: bool
    predecessor_artifacts_integrity_ok: bool
    state_backup_available: bool
    rollback_schema_supported: bool
    clean_process_stop_proven: bool
    exact_predecessor_restart_proven: bool
    post_rollback_state_integrity_ok: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_release_fingerprint", _sha256(self.candidate_release_fingerprint, "candidate release"))
        object.__setattr__(self, "predecessor_release_fingerprint", _sha256(self.predecessor_release_fingerprint, "predecessor release"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m203-rollback-evidence-v1",
            self.candidate_release_fingerprint,
            self.predecessor_release_fingerprint,
            self.predecessor_artifacts_available,
            self.predecessor_artifacts_integrity_ok,
            self.state_backup_available,
            self.rollback_schema_supported,
            self.clean_process_stop_proven,
            self.exact_predecessor_restart_proven,
            self.post_rollback_state_integrity_ok,
        ))


@dataclass(frozen=True, slots=True)
class ReleaseRollbackCertification:
    status: ReleaseCertificationStatus
    release_fingerprint: str
    rollback_evidence_fingerprint: str
    blockers: tuple[str, ...]

    install_authority = False
    rollback_authority = False
    broker_write_authority = False
    live_write_authority = False
    credential_access_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m203-release-rollback-certification-v1",
            self.status.value,
            self.release_fingerprint,
            self.rollback_evidence_fingerprint,
            self.blockers,
        ))


def certify_release_rollback(
    release: ReleaseManifest,
    evidence: RollbackEvidence,
) -> ReleaseRollbackCertification:
    blockers: list[str] = []
    hard_reject = False

    if evidence.candidate_release_fingerprint != release.fingerprint:
        blockers.append("rollback_evidence_candidate_release_mismatch")
        hard_reject = True

    if release.predecessor_release_fingerprint is None:
        blockers.append("predecessor_release_not_bound")
    elif evidence.predecessor_release_fingerprint != release.predecessor_release_fingerprint:
        blockers.append("rollback_evidence_predecessor_mismatch")
        hard_reject = True

    checks = (
        (evidence.predecessor_artifacts_available, "predecessor_artifacts_unavailable"),
        (evidence.predecessor_artifacts_integrity_ok, "predecessor_artifact_integrity_failed"),
        (evidence.state_backup_available, "state_backup_unavailable"),
        (evidence.rollback_schema_supported, "rollback_schema_not_supported"),
        (evidence.clean_process_stop_proven, "clean_process_stop_not_proven"),
        (evidence.exact_predecessor_restart_proven, "exact_predecessor_restart_not_proven"),
        (evidence.post_rollback_state_integrity_ok, "post_rollback_state_integrity_failed"),
    )
    for ok, reason in checks:
        if not ok:
            blockers.append(reason)

    if not evidence.predecessor_artifacts_integrity_ok or not evidence.post_rollback_state_integrity_ok:
        hard_reject = True

    blockers = list(dict.fromkeys(blockers))
    if hard_reject:
        status = ReleaseCertificationStatus.REJECTED
    elif blockers:
        status = ReleaseCertificationStatus.PENDING
    else:
        status = ReleaseCertificationStatus.CERTIFIED

    return ReleaseRollbackCertification(
        status=status,
        release_fingerprint=release.fingerprint,
        rollback_evidence_fingerprint=evidence.fingerprint,
        blockers=tuple(blockers),
    )
