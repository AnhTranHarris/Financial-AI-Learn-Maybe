from __future__ import annotations

"""M199 deterministic Six-Desk Graduation Protocol.

M198 certifies independence inside one Demo generation. M199 adds the
constitutional rule that six independently certified desk evidence units must
pass across one or more generations without recycling account, certification,
runtime, desk, or Champion identity.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json

from .multi_desk_certification import (
    CertifiedDeskEvidence,
    MultiDeskGenerationStatus,
    certify_multi_desk_generation,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


class SixDeskGraduationStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    GRADUATED = "graduated"


@dataclass(frozen=True, slots=True)
class SixDeskGraduation:
    status: SixDeskGraduationStatus
    required_desk_count: int
    certified_desk_count: int
    generation_fingerprints: tuple[str, ...]
    champion_fingerprint: str | None
    desk_fingerprints: tuple[str, ...]
    blockers: tuple[str, ...]

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m199-six-desk-graduation-v1",
            self.status.value,
            self.required_desk_count,
            self.certified_desk_count,
            self.generation_fingerprints,
            self.champion_fingerprint,
            self.desk_fingerprints,
            self.blockers,
        ))


def certify_six_desk_graduation(
    desks: tuple[CertifiedDeskEvidence, ...],
    *,
    required_desk_count: int = 6,
) -> SixDeskGraduation:
    """Apply the six-desk graduation rule to raw M198-compatible evidence.

    Sequential and concurrent execution are equivalent. Independence is defined
    by evidence identity, not wall-clock overlap. Any failed generation rejects
    graduation; incomplete evidence remains pending. This function grants no
    operational authority.
    """
    if required_desk_count != 6:
        raise ValueError("M199 constitutional required_desk_count must equal 6")

    rows = tuple(desks)
    if not rows:
        return SixDeskGraduation(
            SixDeskGraduationStatus.PENDING,
            required_desk_count,
            0,
            (),
            None,
            (),
            ("no_desk_evidence", "insufficient_independent_desks"),
        )

    blockers: list[str] = []

    # Global independence across sequential or concurrent generations.
    for values, blocker in (
        ((row.desk_id for row in rows), "duplicate_desk_identity"),
        ((row.single_desk_fingerprint for row in rows), "reused_single_desk_certification"),
        ((row.account_fingerprint for row in rows), "reused_account_identity"),
        ((row.runtime_attestation_fingerprint for row in rows), "reused_runtime_attestation"),
        ((row.fingerprint for row in rows), "reused_desk_evidence"),
    ):
        material = tuple(values)
        if len(set(material)) != len(material):
            blockers.append(blocker)

    champions = {row.champion_fingerprint for row in rows}
    champion = next(iter(champions)) if len(champions) == 1 else None
    if len(champions) != 1:
        blockers.append("champion_drift_across_graduation")

    generations: list[object] = []
    for generation_id in sorted({row.generation_id for row in rows}):
        cohort = tuple(row for row in rows if row.generation_id == generation_id)
        generations.append(certify_multi_desk_generation(generation_id, cohort))

    if any(row.status is MultiDeskGenerationStatus.REJECTED for row in generations):
        blockers.append("rejected_generation_present")
    elif any(row.status is not MultiDeskGenerationStatus.CERTIFIED for row in generations):
        blockers.append("pending_generation_present")

    certified_desk_count = sum(
        row.desk_count for row in generations if row.status is MultiDeskGenerationStatus.CERTIFIED
    )
    if certified_desk_count < required_desk_count:
        blockers.append("insufficient_independent_desks")

    blockers = list(dict.fromkeys(blockers))
    if blockers:
        status = (
            SixDeskGraduationStatus.REJECTED
            if any(name in blockers for name in (
                "duplicate_desk_identity",
                "reused_single_desk_certification",
                "reused_account_identity",
                "reused_runtime_attestation",
                "reused_desk_evidence",
                "champion_drift_across_graduation",
                "rejected_generation_present",
            ))
            else SixDeskGraduationStatus.PENDING
        )
    else:
        status = SixDeskGraduationStatus.GRADUATED

    return SixDeskGraduation(
        status,
        required_desk_count,
        certified_desk_count,
        tuple(sorted(row.fingerprint for row in generations)),
        champion,
        tuple(sorted(row.fingerprint for row in rows)),
        tuple(blockers),
    )
