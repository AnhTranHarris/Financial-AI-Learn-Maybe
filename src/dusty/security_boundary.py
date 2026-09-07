from __future__ import annotations

"""M201 Production Security / Credential Boundary certification.

M201 certifies the deployment boundary around the already-existing Dusty runtime.
It intentionally does not add a credential vault, broker-write path, process
manager, model router, or MT5 execution implementation.

The preferred MT5 deployment contract is an exact, pre-authenticated terminal.
Dusty therefore has no reason to persist broker passwords. The certification
also keeps model providers, filesystem state, subprocess control, and MQL5
research surfaces outside broker authority.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import re
from urllib.parse import urlsplit


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
    if not rendered or "\n" in rendered or "\r" in rendered:
        raise ValueError(f"{label} must be non-empty and one line")
    return rendered


class SecurityBoundaryStatus(StrEnum):
    PENDING = "pending"
    REJECTED = "rejected"
    CERTIFIED = "certified"


class CredentialMode(StrEnum):
    PREAUTHENTICATED_TERMINAL_ONLY = "preauthenticated_terminal_only"


@dataclass(frozen=True, slots=True)
class SecurityBoundaryPolicy:
    credential_mode: CredentialMode = CredentialMode.PREAUTHENTICATED_TERMINAL_ONLY
    require_exact_terminal_path: bool = True
    require_local_model_endpoint: bool = True
    allow_mql5_dll_imports: bool = False
    allow_mql5_webrequest: bool = False
    allowed_state_roots: tuple[str, ...] = ("DustyDragon",)
    approved_broker_write_surfaces: tuple[str, ...] = (
        "src/dusty/demo_execution.py",
        "src/dusty/demo_execution_bridge.py",
    )

    def __post_init__(self) -> None:
        roots = tuple(_text(row, "allowed state root") for row in self.allowed_state_roots)
        if len(roots) != len(set(roots)):
            raise ValueError("allowed state roots must be unique")
        writes = tuple(_text(row, "approved broker write surface") for row in self.approved_broker_write_surfaces)
        if len(writes) != len(set(writes)):
            raise ValueError("approved broker write surfaces must be unique")
        object.__setattr__(self, "allowed_state_roots", roots)
        object.__setattr__(self, "approved_broker_write_surfaces", writes)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m201-security-policy-v1",
            self.credential_mode.value,
            self.require_exact_terminal_path,
            self.require_local_model_endpoint,
            self.allow_mql5_dll_imports,
            self.allow_mql5_webrequest,
            self.allowed_state_roots,
            self.approved_broker_write_surfaces,
        ))


@dataclass(frozen=True, slots=True)
class SecurityBoundaryEvidence:
    source_commit: str
    terminal_executable_path: str
    terminal_identity_fingerprint: str
    account_identity_fingerprint: str
    credential_fields_persisted: tuple[str, ...]
    raw_account_login_persisted: bool
    model_endpoint: str
    broker_write_surfaces: tuple[str, ...]
    broad_process_termination_used: bool
    shell_execution_used: bool
    state_write_roots: tuple[str, ...]
    mql5_dll_imports_present: bool
    mql5_webrequest_present: bool
    generated_mt5_config_contains_credentials: bool
    live_write_surface_present: bool
    source_tree_integrity_ok: bool

    def __post_init__(self) -> None:
        commit = str(self.source_commit).strip().lower()
        if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit):
            raise ValueError("source_commit requires full SHA-1 Git identity")
        object.__setattr__(self, "source_commit", commit)
        object.__setattr__(self, "terminal_executable_path", _text(self.terminal_executable_path, "terminal path"))
        object.__setattr__(self, "terminal_identity_fingerprint", _sha(self.terminal_identity_fingerprint, "terminal identity"))
        object.__setattr__(self, "account_identity_fingerprint", _sha(self.account_identity_fingerprint, "account identity"))
        fields = tuple(sorted({_text(row, "credential field").lower() for row in self.credential_fields_persisted}))
        object.__setattr__(self, "credential_fields_persisted", fields)
        object.__setattr__(self, "model_endpoint", _text(self.model_endpoint, "model endpoint"))
        writes = tuple(sorted({_text(row, "broker write surface") for row in self.broker_write_surfaces}))
        object.__setattr__(self, "broker_write_surfaces", writes)
        roots = tuple(sorted({_text(row, "state write root") for row in self.state_write_roots}))
        object.__setattr__(self, "state_write_roots", roots)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m201-security-evidence-v1",
            self.source_commit,
            self.terminal_executable_path,
            self.terminal_identity_fingerprint,
            self.account_identity_fingerprint,
            self.credential_fields_persisted,
            self.raw_account_login_persisted,
            self.model_endpoint,
            self.broker_write_surfaces,
            self.broad_process_termination_used,
            self.shell_execution_used,
            self.state_write_roots,
            self.mql5_dll_imports_present,
            self.mql5_webrequest_present,
            self.generated_mt5_config_contains_credentials,
            self.live_write_surface_present,
            self.source_tree_integrity_ok,
        ))


@dataclass(frozen=True, slots=True)
class SecurityBoundaryCertification:
    status: SecurityBoundaryStatus
    policy_fingerprint: str
    evidence_fingerprint: str
    blockers: tuple[str, ...]

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    credential_access_authority = False
    process_kill_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m201-security-certification-v1",
            self.status.value,
            self.policy_fingerprint,
            self.evidence_fingerprint,
            self.blockers,
        ))


def _local_endpoint(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and host in {"127.0.0.1", "localhost", "::1"}


def _root_allowed(raw: str, allowed: tuple[str, ...]) -> bool:
    # Evidence may describe Windows paths while certification runs on Linux CI.
    # Split both separator styles explicitly instead of letting the host OS
    # reinterpret the evidence path.
    parts = tuple(part.lower() for part in re.split(r"[\\/]+", raw) if part)
    allowed_lower = {item.lower() for item in allowed}
    return any(part in allowed_lower for part in parts)


def certify_security_boundary(
    policy: SecurityBoundaryPolicy,
    evidence: SecurityBoundaryEvidence,
) -> SecurityBoundaryCertification:
    blockers: list[str] = []
    hard_reject = False

    if policy.credential_mode is CredentialMode.PREAUTHENTICATED_TERMINAL_ONLY:
        if evidence.credential_fields_persisted:
            blockers.append("broker_credentials_persisted")
            hard_reject = True
        if evidence.raw_account_login_persisted:
            blockers.append("raw_account_login_persisted")
            hard_reject = True
        if evidence.generated_mt5_config_contains_credentials:
            blockers.append("generated_mt5_config_contains_credentials")
            hard_reject = True

    path = evidence.terminal_executable_path.lower().replace("/", "\\")
    if policy.require_exact_terminal_path and not path.endswith("\\terminal64.exe"):
        blockers.append("exact_terminal_executable_not_proven")

    if policy.require_local_model_endpoint and not _local_endpoint(evidence.model_endpoint):
        blockers.append("model_endpoint_not_localhost")
        hard_reject = True

    unapproved_writes = sorted(set(evidence.broker_write_surfaces) - set(policy.approved_broker_write_surfaces))
    if unapproved_writes:
        blockers.append("unapproved_broker_write_surface:" + ",".join(unapproved_writes))
        hard_reject = True

    if evidence.live_write_surface_present:
        blockers.append("live_write_surface_present_before_m204_authorization")
        hard_reject = True
    if evidence.broad_process_termination_used:
        blockers.append("broad_process_termination_used")
        hard_reject = True
    if evidence.shell_execution_used:
        blockers.append("shell_execution_used")
        hard_reject = True

    invalid_roots = tuple(row for row in evidence.state_write_roots if not _root_allowed(row, policy.allowed_state_roots))
    if invalid_roots:
        blockers.append("state_write_outside_allowed_root:" + ",".join(invalid_roots))
        hard_reject = True

    if evidence.mql5_dll_imports_present and not policy.allow_mql5_dll_imports:
        blockers.append("mql5_dll_import_prohibited")
        hard_reject = True
    if evidence.mql5_webrequest_present and not policy.allow_mql5_webrequest:
        blockers.append("mql5_webrequest_prohibited")
        hard_reject = True
    if not evidence.source_tree_integrity_ok:
        blockers.append("source_tree_integrity_not_proven")
        hard_reject = True

    blockers = list(dict.fromkeys(blockers))
    if hard_reject:
        status = SecurityBoundaryStatus.REJECTED
    elif blockers:
        status = SecurityBoundaryStatus.PENDING
    else:
        status = SecurityBoundaryStatus.CERTIFIED

    return SecurityBoundaryCertification(
        status=status,
        policy_fingerprint=policy.fingerprint,
        evidence_fingerprint=evidence.fingerprint,
        blockers=tuple(blockers),
    )
