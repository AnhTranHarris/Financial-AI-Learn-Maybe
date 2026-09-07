from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Iterable

from dusty.multi_desk_certification import (
    MultiDeskCertification,
    MultiDeskCertificationStatus,
)


class SixDeskGraduationStatus(str, Enum):
    PENDING = "pending"
    REJECTED = "rejected"
    GRADUATED = "graduated"


@dataclass(frozen=True)
class SixDeskGraduation:
    status: SixDeskGraduationStatus
    generation_fingerprint: str
    required_passes: int
    certified_desks: int
    blockers: tuple[str, ...]
    graduation_fingerprint: str
    broker_write_authority: bool = False
    live_write_authority: bool = False
    promotion_authority: bool = False
    risk_override_authority: bool = False
    guardian_override_authority: bool = False


def certify_six_desk_graduation(
    generations: Iterable[MultiDeskCertification],
    *,
    required_passes: int = 6,
) -> SixDeskGraduation:
    """Certify six independent M198-passing desk evidence units.

    M199 owns only the six-desk graduation rule. M198 remains responsible for
    independence inside each generation. This function never grants trading,
    live-write, promotion, risk-override, or Guardian-override authority.
    """
    if required_passes <= 0:
        raise ValueError("required_passes must be positive")

    rows = tuple(generations)
    blockers: list[str] = []

    if not rows:
        blockers.append("no_generation_evidence")

    passed = tuple(
        row for row in rows if row.status is MultiDeskCertificationStatus.CERTIFIED
    )

    if any(row.status is MultiDeskCertificationStatus.REJECTED for row in rows):
        blockers.append("rejected_generation_present")

    if any(row.status is MultiDeskCertificationStatus.PENDING for row in rows):
        blockers.append("pending_generation_present")

    fingerprints = tuple(row.certification_fingerprint for row in passed)
    if len(set(fingerprints)) != len(fingerprints):
        blockers.append("reused_generation_evidence")

    champion_ids = {
        row.champion_fingerprint
        for row in passed
    }
    if len(champion_ids) > 1:
        blockers.append("champion_drift_across_graduation")

    if len(passed) < required_passes:
        blockers.append("insufficient_independent_passes")

    status = (
        SixDeskGraduationStatus.GRADUATED
        if not blockers and len(passed) >= required_passes
        else (
            SixDeskGraduationStatus.REJECTED
            if "rejected_generation_present" in blockers
            else SixDeskGraduationStatus.PENDING
        )
    )

    material = "|".join(
        [
            status.value,
            str(required_passes),
            str(len(passed)),
            *sorted(fingerprints),
            *sorted(blockers),
        ]
    )
    graduation_fingerprint = sha256(material.encode("utf-8")).hexdigest()
    generation_fingerprint = (
        next(iter(champion_ids)) if len(champion_ids) == 1 else ""
    )

    return SixDeskGraduation(
        status=status,
        generation_fingerprint=generation_fingerprint,
        required_passes=required_passes,
        certified_desks=len(passed),
        blockers=tuple(blockers),
        graduation_fingerprint=graduation_fingerprint,
    )
