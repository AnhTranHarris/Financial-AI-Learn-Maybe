from __future__ import annotations

"""M198 deterministic Multi-Desk Certification Framework.

M198 does not create Demo evidence and does not impose the six-desk graduation
count reserved for M199.  It combines independently certified M194 desks into
one generation-level contract.  A generation passes only when every supplied
desk passes and the evidence is independent rather than duplicated.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json

from .single_desk_demo_certification import SingleDeskDemoStatus


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _text(value: str, label: str) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > 128:
        raise ValueError(f"{label} must be non-empty, one line, and <= 128 characters")
    return rendered


class MultiDeskGenerationStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    CERTIFIED = "certified"


@dataclass(frozen=True, slots=True)
class CertifiedDeskEvidence:
    desk_id: str
    generation_id: str
    single_desk_status: SingleDeskDemoStatus
    single_desk_fingerprint: str
    champion_fingerprint: str
    account_fingerprint: str
    terminal_fingerprint: str
    broker_profile_fingerprint: str
    runtime_attestation_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "desk_id", _text(self.desk_id, "desk_id"))
        object.__setattr__(self, "generation_id", _text(self.generation_id, "generation_id"))
        if not isinstance(self.single_desk_status, SingleDeskDemoStatus):
            raise ValueError("single_desk_status must use SingleDeskDemoStatus")
        for field, label in (
            ("single_desk_fingerprint", "single-desk certification"),
            ("champion_fingerprint", "Champion"),
            ("account_fingerprint", "account"),
            ("terminal_fingerprint", "terminal"),
            ("broker_profile_fingerprint", "broker profile"),
            ("runtime_attestation_fingerprint", "runtime attestation"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m198-certified-desk-evidence-v1",
            self.desk_id,
            self.generation_id,
            self.single_desk_status.value,
            self.single_desk_fingerprint,
            self.champion_fingerprint,
            self.account_fingerprint,
            self.terminal_fingerprint,
            self.broker_profile_fingerprint,
            self.runtime_attestation_fingerprint,
        ))


@dataclass(frozen=True, slots=True)
class MultiDeskGenerationCertification:
    generation_id: str
    status: MultiDeskGenerationStatus
    desk_count: int
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
            "dusty-m198-multi-desk-generation-v1",
            self.generation_id,
            self.status.value,
            self.desk_count,
            self.champion_fingerprint,
            self.desk_fingerprints,
            self.blockers,
        ))


def certify_multi_desk_generation(
    generation_id: str,
    desks: tuple[CertifiedDeskEvidence, ...],
) -> MultiDeskGenerationCertification:
    """Certify one independent Demo generation.

    M198 intentionally accepts any non-empty cohort size. M199 owns the rule that
    six independently passing desks/generations are required for graduation.
    Sequential and concurrent execution are treated identically because identity
    and evidence independence, not wall-clock overlap, define the cohort.
    """
    generation = _text(generation_id, "generation_id")
    rows = tuple(desks)
    if not rows:
        return MultiDeskGenerationCertification(
            generation, MultiDeskGenerationStatus.PENDING, 0, None, (), ("no_desk_evidence",)
        )

    blockers: list[str] = []
    if any(row.generation_id != generation for row in rows):
        blockers.append("generation_identity_mismatch")

    desk_ids = tuple(row.desk_id for row in rows)
    if len(set(desk_ids)) != len(desk_ids):
        blockers.append("duplicate_desk_identity")

    certification_ids = tuple(row.single_desk_fingerprint for row in rows)
    if len(set(certification_ids)) != len(certification_ids):
        blockers.append("reused_single_desk_certification")

    account_ids = tuple(row.account_fingerprint for row in rows)
    if len(set(account_ids)) != len(account_ids):
        blockers.append("duplicate_account_identity")

    runtime_ids = tuple(row.runtime_attestation_fingerprint for row in rows)
    if len(set(runtime_ids)) != len(runtime_ids):
        blockers.append("reused_runtime_attestation")

    champions = {row.champion_fingerprint for row in rows}
    champion = next(iter(champions)) if len(champions) == 1 else None
    if len(champions) != 1:
        blockers.append("mixed_champion_generation")

    if any(row.single_desk_status is SingleDeskDemoStatus.REJECTED for row in rows):
        blockers.append("desk_rejected")
    elif any(row.single_desk_status is not SingleDeskDemoStatus.CERTIFIED for row in rows):
        blockers.append("desk_not_certified")

    blockers = list(dict.fromkeys(blockers))
    if blockers:
        status = (
            MultiDeskGenerationStatus.REJECTED
            if any(name in blockers for name in (
                "duplicate_desk_identity",
                "reused_single_desk_certification",
                "duplicate_account_identity",
                "reused_runtime_attestation",
                "mixed_champion_generation",
                "desk_rejected",
            ))
            else MultiDeskGenerationStatus.PENDING
        )
    else:
        status = MultiDeskGenerationStatus.CERTIFIED

    return MultiDeskGenerationCertification(
        generation,
        status,
        len(rows),
        champion,
        tuple(sorted(row.fingerprint for row in rows)),
        tuple(blockers),
    )
