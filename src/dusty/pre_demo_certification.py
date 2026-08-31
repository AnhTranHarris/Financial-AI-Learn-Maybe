from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable


REQUIRED_MILESTONES = tuple(f"M{number}" for number in range(56, 65))


@dataclass(frozen=True, slots=True)
class MilestoneEvidence:
    milestone: str
    passed: bool
    artifact_hash: str
    data_fingerprint: str
    config_fingerprint: str
    test_fingerprint: str
    commit_sha: str

    def __post_init__(self) -> None:
        if self.milestone not in REQUIRED_MILESTONES:
            raise ValueError(f"unsupported milestone evidence: {self.milestone}")
        values = (
            self.artifact_hash,
            self.data_fingerprint,
            self.config_fingerprint,
            self.test_fingerprint,
            self.commit_sha,
        )
        if any(not value.strip() for value in values):
            raise ValueError("certification evidence fingerprints are required")

    @property
    def evidence_hash(self) -> str:
        return sha256(_canonical_json(self.as_dict()).encode("utf-8")).hexdigest()

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
class PreDemoCertification:
    ready_for_demo_execution_engineering: bool
    broker_write_authorized: bool
    certification_hash: str
    evidence_hashes: tuple[tuple[str, str], ...]
    reasons: tuple[str, ...]

    def checkpoint_payload(self) -> str:
        """Stable restart/audit payload; contains no authority to place broker orders."""
        return _canonical_json(
            {
                "ready_for_demo_execution_engineering": self.ready_for_demo_execution_engineering,
                "broker_write_authorized": self.broker_write_authorized,
                "certification_hash": self.certification_hash,
                "evidence_hashes": list(self.evidence_hashes),
                "reasons": list(self.reasons),
            }
        )


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def certify_pre_demo(
    evidence: Iterable[MilestoneEvidence],
    *,
    current_commit_sha: str,
) -> PreDemoCertification:
    """Certify M56-M64 evidence; M65 can only authorize engineering, never trading."""
    if not current_commit_sha.strip():
        raise ValueError("current commit sha is required")
    rows = tuple(evidence)
    by_milestone: dict[str, MilestoneEvidence] = {}
    reasons: list[str] = []
    for item in rows:
        if item.milestone in by_milestone:
            reasons.append(f"duplicate_evidence:{item.milestone}")
        else:
            by_milestone[item.milestone] = item

    for milestone in REQUIRED_MILESTONES:
        item = by_milestone.get(milestone)
        if item is None:
            reasons.append(f"missing_evidence:{milestone}")
            continue
        if not item.passed:
            reasons.append(f"milestone_failed:{milestone}")
        if item.commit_sha != current_commit_sha:
            reasons.append(f"commit_mismatch:{milestone}")

    ordered_hashes = tuple(
        (milestone, by_milestone[milestone].evidence_hash)
        for milestone in REQUIRED_MILESTONES
        if milestone in by_milestone
    )
    bundle_payload = {
        "schema": "dusty-pre-demo-certification-v1",
        "current_commit_sha": current_commit_sha,
        "required_milestones": list(REQUIRED_MILESTONES),
        "evidence_hashes": list(ordered_hashes),
        "reasons": reasons,
        "broker_write_authorized": False,
    }
    certification_hash = sha256(_canonical_json(bundle_payload).encode("utf-8")).hexdigest()
    return PreDemoCertification(
        ready_for_demo_execution_engineering=not reasons,
        broker_write_authorized=False,
        certification_hash=certification_hash,
        evidence_hashes=ordered_hashes,
        reasons=tuple(reasons),
    )
