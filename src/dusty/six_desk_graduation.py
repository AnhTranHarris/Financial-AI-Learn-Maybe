from __future__ import annotations

"""M199 deterministic Six-Desk Graduation Protocol.

M199 sits above M198. A certified M198 generation contributes all of its desk
evidence or none of it. Dusty graduates only after at least six independent
M194-certified desk evidence units have accumulated for one frozen Champion.
Sequential generations are equivalent to concurrent cohorts, so a constrained
PC may accumulate six one-desk generations instead of hosting six desks at once.

A failed/pending generation is evidence, but contributes zero toward the six.
Evidence reuse, identity drift, or mixed Champions fail closed.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json

from .multi_desk_certification import (
    CertifiedDeskEvidence,
    MultiDeskGenerationCertification,
    MultiDeskGenerationStatus,
    certify_multi_desk_generation,
)


_REQUIRED_DESKS = 6


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


class SixDeskGraduationStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    GRADUATED = "graduated"


@dataclass(frozen=True, slots=True)
class GraduationGenerationEvidence:
    certification: MultiDeskGenerationCertification
    desks: tuple[CertifiedDeskEvidence, ...]

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m199-generation-evidence-v1",
            self.certification.fingerprint,
            tuple(sorted(row.fingerprint for row in self.desks)),
        ))


@dataclass(frozen=True, slots=True)
class SixDeskGraduationCertification:
    champion_fingerprint: str
    status: SixDeskGraduationStatus
    required_desk_count: int
    qualifying_desk_count: int
    qualifying_generation_count: int
    evaluated_generation_count: int
    qualifying_generation_fingerprints: tuple[str, ...]
    qualifying_desk_fingerprints: tuple[str, ...]
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
            self.champion_fingerprint,
            self.status.value,
            self.required_desk_count,
            self.qualifying_desk_count,
            self.qualifying_generation_count,
            self.evaluated_generation_count,
            self.qualifying_generation_fingerprints,
            self.qualifying_desk_fingerprints,
            self.blockers,
        ))


def certify_six_desk_graduation(
    champion_fingerprint: str,
    generations: tuple[GraduationGenerationEvidence, ...],
) -> SixDeskGraduationCertification:
    """Evaluate the constitutional six-independent-desk graduation rule.

    Each supplied generation is re-certified from its underlying M198 desk
    evidence. Only an exact, independently reproducible M198 CERTIFIED result
    contributes desks. A pending/rejected generation contributes zero and asks
    Dusty for another round; it does not erase previously earned independent
    passes. Evidence reuse or Champion/identity inconsistency is a hard reject.
    """
    champion = _sha(champion_fingerprint, "Champion")
    rows = tuple(generations)

    hard_blockers: list[str] = []
    soft_blockers: list[str] = []
    qualifying: list[GraduationGenerationEvidence] = []

    seen_generation_ids: set[str] = set()
    seen_generation_certifications: set[str] = set()
    seen_desk_fingerprints: set[str] = set()
    seen_single_desk_certifications: set[str] = set()
    seen_accounts: set[str] = set()
    seen_runtimes: set[str] = set()

    for item in rows:
        cert = item.certification
        desks = tuple(item.desks)

        if cert.generation_id in seen_generation_ids:
            hard_blockers.append("duplicate_generation_identity")
        seen_generation_ids.add(cert.generation_id)

        if cert.fingerprint in seen_generation_certifications:
            hard_blockers.append("reused_generation_certification")
        seen_generation_certifications.add(cert.fingerprint)

        # Recompute M198 from the raw desk evidence so a caller cannot pair a
        # certified summary with different underlying desks.
        reproduced = certify_multi_desk_generation(cert.generation_id, desks)
        if reproduced.fingerprint != cert.fingerprint:
            hard_blockers.append("generation_certification_mismatch")
            continue

        if cert.status is not MultiDeskGenerationStatus.CERTIFIED:
            soft_blockers.append("generation_not_certified")
            continue

        if cert.champion_fingerprint != champion:
            hard_blockers.append("mixed_champion_graduation")
            continue

        generation_hard_failure = False
        for desk in desks:
            if desk.fingerprint in seen_desk_fingerprints:
                hard_blockers.append("reused_desk_evidence")
                generation_hard_failure = True
            if desk.single_desk_fingerprint in seen_single_desk_certifications:
                hard_blockers.append("reused_single_desk_certification")
                generation_hard_failure = True
            if desk.account_fingerprint in seen_accounts:
                hard_blockers.append("reused_account_identity")
                generation_hard_failure = True
            if desk.runtime_attestation_fingerprint in seen_runtimes:
                hard_blockers.append("reused_runtime_attestation")
                generation_hard_failure = True

        if generation_hard_failure:
            continue

        for desk in desks:
            seen_desk_fingerprints.add(desk.fingerprint)
            seen_single_desk_certifications.add(desk.single_desk_fingerprint)
            seen_accounts.add(desk.account_fingerprint)
            seen_runtimes.add(desk.runtime_attestation_fingerprint)
        qualifying.append(item)

    hard_blockers = list(dict.fromkeys(hard_blockers))
    soft_blockers = list(dict.fromkeys(soft_blockers))

    qualifying_desk_fingerprints = tuple(sorted(
        desk.fingerprint
        for item in qualifying
        for desk in item.desks
    ))
    qualifying_generation_fingerprints = tuple(sorted(
        item.certification.fingerprint for item in qualifying
    ))
    qualifying_desk_count = len(qualifying_desk_fingerprints)

    if hard_blockers:
        status = SixDeskGraduationStatus.REJECTED
        blockers = tuple(hard_blockers + soft_blockers)
    elif qualifying_desk_count >= _REQUIRED_DESKS:
        status = SixDeskGraduationStatus.GRADUATED
        blockers = ()
    else:
        status = SixDeskGraduationStatus.PENDING
        blockers_list = list(soft_blockers)
        blockers_list.append("six_independent_desks_not_yet_proven")
        blockers = tuple(dict.fromkeys(blockers_list))

    return SixDeskGraduationCertification(
        champion_fingerprint=champion,
        status=status,
        required_desk_count=_REQUIRED_DESKS,
        qualifying_desk_count=qualifying_desk_count,
        qualifying_generation_count=len(qualifying),
        evaluated_generation_count=len(rows),
        qualifying_generation_fingerprints=qualifying_generation_fingerprints,
        qualifying_desk_fingerprints=qualifying_desk_fingerprints,
        blockers=blockers,
    )
