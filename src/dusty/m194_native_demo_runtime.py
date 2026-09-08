from __future__ import annotations

"""M194.1 native Demo runtime identity and heartbeat binding.

This module is deliberately admission/evidence-only. It never imports
MetaTrader5, never calls order_send, never changes terminal settings, and never
grants broker-write authority. A run may begin only when the exact native
preflight is READY and the M185 registry reports one ACTIVE Champion for the
requested lane.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json

from .champion_registry import ChampionLifecycleState, FrozenChampionRecord, FrozenChampionRegistry
from .m194_native_demo_journal import (
    M194NativeEventKind,
    M194NativeEvidenceEvent,
    SQLiteM194NativeEvidenceJournal,
)
from .m194_native_demo_preflight import (
    NativeDemoPreflightAssessment,
    NativeDemoPreflightStatus,
    NativeDemoTerminalSnapshot,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git_sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires a full Git SHA")
    return rendered


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _text(value: object, label: str, *, maximum: int = 256) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def terminal_identity_fingerprint(snapshot: NativeDemoTerminalSnapshot) -> str:
    return _digest((
        "dusty-m1941-terminal-identity-v1",
        snapshot.terminal_path,
        snapshot.terminal_build,
        snapshot.server,
    ))


def account_identity_fingerprint(snapshot: NativeDemoTerminalSnapshot) -> str:
    return _digest((
        "dusty-m1941-account-identity-v1",
        snapshot.login,
        snapshot.server,
        snapshot.account_mode.value,
        snapshot.account_currency,
        snapshot.leverage,
    ))


@dataclass(frozen=True, slots=True)
class M194NativeRunIdentity:
    run_id: str
    lane_id: str
    champion_fingerprint: str
    source_commit: str
    preflight_fingerprint: str
    initial_snapshot_fingerprint: str
    terminal_fingerprint: str
    account_fingerprint: str
    symbol_spec_fingerprint: str
    started_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", _text(self.run_id, "run_id", maximum=128))
        object.__setattr__(self, "lane_id", _text(self.lane_id, "lane_id", maximum=128).lower())
        object.__setattr__(self, "champion_fingerprint", _sha(self.champion_fingerprint, "Champion"))
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "source commit"))
        for name, label in (
            ("preflight_fingerprint", "preflight"),
            ("initial_snapshot_fingerprint", "initial snapshot"),
            ("terminal_fingerprint", "terminal"),
            ("account_fingerprint", "account"),
            ("symbol_spec_fingerprint", "symbol spec"),
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), label))
        object.__setattr__(self, "started_at", _aware(self.started_at, "started_at"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m1941-native-run-identity-v1",
            self.run_id,
            self.lane_id,
            self.champion_fingerprint,
            self.source_commit,
            self.preflight_fingerprint,
            self.initial_snapshot_fingerprint,
            self.terminal_fingerprint,
            self.account_fingerprint,
            self.symbol_spec_fingerprint,
            self.started_at.isoformat(),
        ))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False


def _verify_ready_preflight(
    snapshot: NativeDemoTerminalSnapshot,
    assessment: NativeDemoPreflightAssessment,
    *,
    source_commit: str,
) -> None:
    commit = _git_sha(source_commit, "source commit")
    if assessment.source_commit != commit:
        raise ValueError("M194.1 preflight source commit drift")
    if assessment.snapshot_fingerprint != snapshot.fingerprint:
        raise ValueError("M194.1 preflight/snapshot identity mismatch")
    if assessment.status is not NativeDemoPreflightStatus.READY or assessment.blockers:
        raise PermissionError("M194.1 native Demo run requires READY preflight")


def start_native_demo_run(
    *,
    registry: FrozenChampionRegistry,
    journal: SQLiteM194NativeEvidenceJournal,
    lane_id: str,
    run_id: str,
    source_commit: str,
    snapshot: NativeDemoTerminalSnapshot,
    assessment: NativeDemoPreflightAssessment,
    started_at: datetime,
) -> M194NativeRunIdentity:
    """Bind one exact ACTIVE Champion and READY native preflight to a run."""

    _verify_ready_preflight(snapshot, assessment, source_commit=source_commit)
    lane = _text(lane_id, "lane_id", maximum=128).lower()
    integrity_ok, integrity_errors = registry.integrity_check()
    if not integrity_ok:
        raise RuntimeError(f"M185 registry integrity failure: {integrity_errors}")
    champion = registry.active_for_lane(lane)
    if champion is None:
        raise PermissionError("M194.1 requires one ACTIVE M185 Champion for the lane")
    if registry.state(champion.fingerprint) is not ChampionLifecycleState.ACTIVE:
        raise PermissionError("M194.1 Champion is not ACTIVE")

    identity = M194NativeRunIdentity(
        run_id=run_id,
        lane_id=lane,
        champion_fingerprint=champion.fingerprint,
        source_commit=source_commit,
        preflight_fingerprint=assessment.fingerprint,
        initial_snapshot_fingerprint=snapshot.fingerprint,
        terminal_fingerprint=terminal_identity_fingerprint(snapshot),
        account_fingerprint=account_identity_fingerprint(snapshot),
        symbol_spec_fingerprint=snapshot.symbol_spec_fingerprint,
        started_at=started_at,
    )
    registry_attestation = _digest((
        "dusty-m1941-registry-attestation-v1",
        lane,
        champion.fingerprint,
        champion.generation_id,
        champion.deployment_fingerprint,
        True,
    ))
    journal.append(M194NativeEvidenceEvent(
        run_id=identity.run_id,
        kind=M194NativeEventKind.RUN_STARTED,
        occurred_at=identity.started_at,
        source_commit=identity.source_commit,
        champion_fingerprint=identity.champion_fingerprint,
        evidence_fingerprints=(
            identity.fingerprint,
            assessment.fingerprint,
            snapshot.fingerprint,
            registry_attestation,
        ),
        payload={
            "protocol": "dusty-m1941-native-run-start-v1",
            "lane_id": identity.lane_id,
            "run_identity_fingerprint": identity.fingerprint,
            "preflight_fingerprint": assessment.fingerprint,
            "snapshot_fingerprint": snapshot.fingerprint,
            "terminal_fingerprint": identity.terminal_fingerprint,
            "account_fingerprint": identity.account_fingerprint,
            "symbol_spec_fingerprint": identity.symbol_spec_fingerprint,
            "registry_attestation_fingerprint": registry_attestation,
            "broker_write_authority": False,
            "live_write_authority": False,
        },
    ))
    return identity


def record_native_demo_heartbeat(
    *,
    journal: SQLiteM194NativeEvidenceJournal,
    identity: M194NativeRunIdentity,
    snapshot: NativeDemoTerminalSnapshot,
    assessment: NativeDemoPreflightAssessment,
    observed_at: datetime,
) -> str:
    """Append a read-only heartbeat only while exact terminal/account identity holds."""

    _verify_ready_preflight(snapshot, assessment, source_commit=identity.source_commit)
    if terminal_identity_fingerprint(snapshot) != identity.terminal_fingerprint:
        raise PermissionError("M194.1 terminal identity drift")
    if account_identity_fingerprint(snapshot) != identity.account_fingerprint:
        raise PermissionError("M194.1 account identity drift")
    if snapshot.symbol_spec_fingerprint != identity.symbol_spec_fingerprint:
        raise PermissionError("M194.1 symbol specification drift")

    event = M194NativeEvidenceEvent(
        run_id=identity.run_id,
        kind=M194NativeEventKind.HEARTBEAT,
        occurred_at=_aware(observed_at, "observed_at"),
        source_commit=identity.source_commit,
        champion_fingerprint=identity.champion_fingerprint,
        evidence_fingerprints=(identity.fingerprint, snapshot.fingerprint, assessment.fingerprint),
        payload={
            "protocol": "dusty-m1941-native-heartbeat-v1",
            "run_identity_fingerprint": identity.fingerprint,
            "snapshot_fingerprint": snapshot.fingerprint,
            "preflight_fingerprint": assessment.fingerprint,
            "terminal_fingerprint": identity.terminal_fingerprint,
            "account_fingerprint": identity.account_fingerprint,
            "symbol_spec_fingerprint": identity.symbol_spec_fingerprint,
            "broker_write_authority": False,
            "live_write_authority": False,
        },
    )
    return journal.append(event)
