from __future__ import annotations

"""M189 durable autonomous research-campaign orchestration.

M155 owns experiment leasing, M160 owns research-value/governor transitions,
and M164 owns durable artifacts. M189 only binds those controls into an
ordered A1 -> A2 -> A3 campaign with immutable budgets, exact resume identity,
and bounded stagnation. It has no broker, risk, Guardian, or promotion surface.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable

from .research_brain import ResearchSchool
from .research_loop_governor import LoopState, ResearchLoopRecord


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _commit(value: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError("source commit requires a 40- or 64-character hexadecimal identity")
    return rendered


def _text(value: str, label: str, *, maximum: int = 256) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _nonnegative_int(value: int, label: str) -> int:
    if isinstance(value, bool) or int(value) != value or int(value) < 0:
        raise ValueError(f"{label} must be a nonnegative integer")
    return int(value)


def _positive_int(value: int, label: str) -> int:
    rendered = _nonnegative_int(value, label)
    if rendered < 1:
        raise ValueError(f"{label} must be positive")
    return rendered


def _nonnegative_float(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or rendered < 0:
        raise ValueError(f"{label} must be finite and nonnegative")
    return rendered


class CampaignStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    EXHAUSTED = "exhausted"
    COMPLETE = "complete"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class AutonomousCampaignManifest:
    campaign_id: str
    constitution_fingerprint: str
    context_fingerprint: str
    source_commit: str
    maximum_steps: int = 10_000
    maximum_experiments: int = 2_000
    maximum_resource_seconds: float = 7 * 24 * 60 * 60
    maximum_stagnant_steps: int = 3
    schools: tuple[ResearchSchool, ...] = (
        ResearchSchool.A1_EDGE,
        ResearchSchool.A2_PROFITABILITY,
        ResearchSchool.A3_VELOCITY,
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "campaign_id", _text(self.campaign_id, "campaign_id", maximum=128))
        object.__setattr__(self, "constitution_fingerprint", _sha(self.constitution_fingerprint, "campaign constitution"))
        object.__setattr__(self, "context_fingerprint", _sha(self.context_fingerprint, "campaign context"))
        object.__setattr__(self, "source_commit", _commit(self.source_commit))
        object.__setattr__(self, "maximum_steps", _positive_int(self.maximum_steps, "maximum_steps"))
        object.__setattr__(self, "maximum_experiments", _positive_int(self.maximum_experiments, "maximum_experiments"))
        object.__setattr__(
            self,
            "maximum_resource_seconds",
            _nonnegative_float(self.maximum_resource_seconds, "maximum_resource_seconds"),
        )
        if self.maximum_resource_seconds <= 0:
            raise ValueError("maximum_resource_seconds must be positive")
        object.__setattr__(
            self,
            "maximum_stagnant_steps",
            _positive_int(self.maximum_stagnant_steps, "maximum_stagnant_steps"),
        )
        schools = tuple(ResearchSchool(value) for value in self.schools)
        expected = (
            ResearchSchool.A1_EDGE,
            ResearchSchool.A2_PROFITABILITY,
            ResearchSchool.A3_VELOCITY,
        )
        if schools != expected:
            raise ValueError("autonomous campaign must preserve A1 -> A2 -> A3 school order")
        object.__setattr__(self, "schools", schools)

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m189-autonomous-campaign-manifest-v1",
                self.campaign_id,
                self.constitution_fingerprint,
                self.context_fingerprint,
                self.source_commit,
                self.maximum_steps,
                self.maximum_experiments,
                self.maximum_resource_seconds,
                self.maximum_stagnant_steps,
                tuple(value.value for value in self.schools),
            )
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class CampaignCheckpoint:
    campaign_id: str
    manifest_fingerprint: str
    loop_fingerprint: str
    school_index: int
    loop_state: LoopState
    loop_iteration: int
    step_index: int
    completed_experiment_fingerprints: tuple[str, ...]
    result_fingerprints: tuple[str, ...]
    resource_seconds_used: float
    last_action_fingerprint: str | None
    stagnant_steps: int
    status: CampaignStatus
    reason: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "campaign_id", _text(self.campaign_id, "checkpoint campaign_id", maximum=128))
        object.__setattr__(self, "manifest_fingerprint", _sha(self.manifest_fingerprint, "checkpoint manifest"))
        object.__setattr__(self, "loop_fingerprint", _sha(self.loop_fingerprint, "checkpoint loop"))
        object.__setattr__(self, "school_index", _nonnegative_int(self.school_index, "checkpoint school_index"))
        if self.school_index > 2:
            raise ValueError("checkpoint school_index exceeds A1/A2/A3")
        object.__setattr__(self, "loop_iteration", _nonnegative_int(self.loop_iteration, "checkpoint loop_iteration"))
        object.__setattr__(self, "step_index", _nonnegative_int(self.step_index, "checkpoint step_index"))
        experiments = tuple(sorted(_sha(value, "checkpoint experiment") for value in self.completed_experiment_fingerprints))
        results = tuple(sorted(_sha(value, "checkpoint result") for value in self.result_fingerprints))
        if len(experiments) != len(set(experiments)) or len(results) != len(set(results)):
            raise ValueError("checkpoint experiment/result identities must be unique")
        object.__setattr__(self, "completed_experiment_fingerprints", experiments)
        object.__setattr__(self, "result_fingerprints", results)
        object.__setattr__(
            self,
            "resource_seconds_used",
            _nonnegative_float(self.resource_seconds_used, "checkpoint resource_seconds_used"),
        )
        if self.last_action_fingerprint is not None:
            object.__setattr__(self, "last_action_fingerprint", _sha(self.last_action_fingerprint, "checkpoint action"))
        object.__setattr__(self, "stagnant_steps", _nonnegative_int(self.stagnant_steps, "checkpoint stagnant_steps"))
        object.__setattr__(self, "reason", _text(self.reason, "checkpoint reason", maximum=512))
        object.__setattr__(self, "created_at", _aware(self.created_at, "checkpoint created_at"))

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m189-campaign-checkpoint-v1",
                self.campaign_id,
                self.manifest_fingerprint,
                self.loop_fingerprint,
                self.school_index,
                self.loop_state.value,
                self.loop_iteration,
                self.step_index,
                self.completed_experiment_fingerprints,
                self.result_fingerprints,
                self.resource_seconds_used,
                self.last_action_fingerprint,
                self.stagnant_steps,
                self.status.value,
                self.reason,
                self.created_at.isoformat(),
            )
        )

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


_TERMINAL_LOOP_STATES = {LoopState.EXHAUSTED, LoopState.GRAVEYARD}


def start_campaign(
    manifest: AutonomousCampaignManifest,
    loop: ResearchLoopRecord,
    *,
    now: datetime,
) -> CampaignCheckpoint:
    return CampaignCheckpoint(
        manifest.campaign_id,
        manifest.fingerprint,
        loop.loop_fingerprint,
        0,
        loop.state,
        loop.iteration,
        0,
        (),
        (),
        0.0,
        None,
        0,
        CampaignStatus.EXHAUSTED if loop.state in _TERMINAL_LOOP_STATES else CampaignStatus.ACTIVE,
        "research_loop_already_exhausted" if loop.state in _TERMINAL_LOOP_STATES else "campaign_started",
        now,
    )


def advance_campaign(
    manifest: AutonomousCampaignManifest,
    previous: CampaignCheckpoint,
    loop: ResearchLoopRecord,
    *,
    action_fingerprint: str,
    completed_experiment_fingerprints: Iterable[str] = (),
    result_fingerprints: Iterable[str] = (),
    resource_seconds_delta: float = 0.0,
    school_passed: bool = False,
    now: datetime,
) -> CampaignCheckpoint:
    if previous.manifest_fingerprint != manifest.fingerprint or previous.campaign_id != manifest.campaign_id:
        raise ValueError("campaign resume manifest identity drift")
    if previous.status is not CampaignStatus.ACTIVE:
        raise ValueError("only active campaigns may advance")
    if loop.loop_fingerprint != previous.loop_fingerprint:
        raise ValueError("campaign resume research-loop identity drift")
    if loop.iteration < previous.loop_iteration:
        raise ValueError("research-loop iteration regressed across campaign checkpoint")
    if loop.updated_at < previous.created_at:
        raise ValueError("research-loop state predates durable campaign checkpoint")
    action = _sha(action_fingerprint, "campaign action")
    new_experiments = tuple(sorted({_sha(value, "campaign completed experiment") for value in completed_experiment_fingerprints}))
    new_results = tuple(sorted({_sha(value, "campaign result") for value in result_fingerprints}))
    if set(previous.completed_experiment_fingerprints) & set(new_experiments):
        raise ValueError("campaign step cannot recount completed experiment evidence")
    if set(previous.result_fingerprints) & set(new_results):
        raise ValueError("campaign step cannot recount result evidence")
    total_experiments = tuple(sorted((*previous.completed_experiment_fingerprints, *new_experiments)))
    total_results = tuple(sorted((*previous.result_fingerprints, *new_results)))
    resource = previous.resource_seconds_used + _nonnegative_float(resource_seconds_delta, "campaign resource_seconds_delta")
    step = previous.step_index + 1

    made_progress = bool(new_experiments or new_results or loop.iteration > previous.loop_iteration or loop.state != previous.loop_state)
    stagnant = 0 if made_progress or action != previous.last_action_fingerprint else previous.stagnant_steps + 1

    school_index = previous.school_index
    status = CampaignStatus.ACTIVE
    reason = "campaign_step_recorded"
    if loop.state in _TERMINAL_LOOP_STATES:
        status = CampaignStatus.EXHAUSTED
        reason = f"m160_research_loop_{loop.state.value}"
    elif school_passed:
        if loop.state is not LoopState.PASSED_STAGE:
            raise ValueError("school pass requires M160 PASSED_STAGE evidence")
        if school_index == len(manifest.schools) - 1:
            status = CampaignStatus.COMPLETE
            reason = "a1_a2_a3_campaign_complete"
        else:
            school_index += 1
            reason = f"advance_to_{manifest.schools[school_index].value}"
    elif step >= manifest.maximum_steps:
        status = CampaignStatus.PAUSED
        reason = "campaign_step_budget_exhausted"
    elif len(total_experiments) >= manifest.maximum_experiments:
        status = CampaignStatus.PAUSED
        reason = "campaign_experiment_budget_exhausted"
    elif resource >= manifest.maximum_resource_seconds:
        status = CampaignStatus.PAUSED
        reason = "campaign_resource_budget_exhausted"
    elif stagnant >= manifest.maximum_stagnant_steps:
        status = CampaignStatus.PAUSED
        reason = "campaign_stagnation_detected"

    return CampaignCheckpoint(
        manifest.campaign_id,
        manifest.fingerprint,
        loop.loop_fingerprint,
        school_index,
        loop.state,
        loop.iteration,
        step,
        total_experiments,
        total_results,
        resource,
        action,
        stagnant,
        status,
        reason,
        now,
    )


def cancel_campaign(
    manifest: AutonomousCampaignManifest,
    previous: CampaignCheckpoint,
    *,
    reason: str,
    now: datetime,
) -> CampaignCheckpoint:
    if previous.manifest_fingerprint != manifest.fingerprint or previous.campaign_id != manifest.campaign_id:
        raise ValueError("campaign cancellation manifest identity drift")
    if previous.status in {CampaignStatus.COMPLETE, CampaignStatus.EXHAUSTED, CampaignStatus.CANCELLED}:
        raise ValueError("terminal campaign cannot be cancelled again")
    return CampaignCheckpoint(
        previous.campaign_id,
        previous.manifest_fingerprint,
        previous.loop_fingerprint,
        previous.school_index,
        previous.loop_state,
        previous.loop_iteration,
        previous.step_index,
        previous.completed_experiment_fingerprints,
        previous.result_fingerprints,
        previous.resource_seconds_used,
        previous.last_action_fingerprint,
        previous.stagnant_steps,
        CampaignStatus.CANCELLED,
        _text(reason, "campaign cancellation reason", maximum=512),
        now,
    )


class SQLiteAutonomousCampaignStore:
    """Append-only campaign checkpoints with exact manifest-bound resume."""

    def __init__(self, path: str | Path = ":memory:", *, busy_timeout_ms: int = 5_000) -> None:
        if not 1 <= busy_timeout_ms <= 60_000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000")
        self._db = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000.0)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS autonomous_campaign_checkpoints("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL, manifest_fingerprint TEXT NOT NULL,"
            "checkpoint_fingerprint TEXT NOT NULL UNIQUE, payload TEXT NOT NULL, payload_sha256 TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_autonomous_campaign_latest "
            "ON autonomous_campaign_checkpoints(campaign_id,seq)"
        )
        self._db.commit()

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def promotion_authorized(self) -> bool:
        return False

    @staticmethod
    def _payload(checkpoint: CampaignCheckpoint) -> dict[str, object]:
        return {
            "campaign_id": checkpoint.campaign_id,
            "manifest_fingerprint": checkpoint.manifest_fingerprint,
            "loop_fingerprint": checkpoint.loop_fingerprint,
            "school_index": checkpoint.school_index,
            "loop_state": checkpoint.loop_state.value,
            "loop_iteration": checkpoint.loop_iteration,
            "step_index": checkpoint.step_index,
            "completed_experiment_fingerprints": list(checkpoint.completed_experiment_fingerprints),
            "result_fingerprints": list(checkpoint.result_fingerprints),
            "resource_seconds_used": checkpoint.resource_seconds_used,
            "last_action_fingerprint": checkpoint.last_action_fingerprint,
            "stagnant_steps": checkpoint.stagnant_steps,
            "status": checkpoint.status.value,
            "reason": checkpoint.reason,
            "created_at": checkpoint.created_at.isoformat(),
        }

    def append(self, checkpoint: CampaignCheckpoint) -> None:
        payload = self._payload(checkpoint)
        rendered = _canonical(payload)
        payload_sha = sha256(rendered.encode("utf-8")).hexdigest()
        with self._db:
            prior = self._db.execute(
                "SELECT manifest_fingerprint,payload_sha256 FROM autonomous_campaign_checkpoints "
                "WHERE campaign_id=? ORDER BY seq DESC LIMIT 1",
                (checkpoint.campaign_id,),
            ).fetchone()
            if prior is not None and str(prior[0]) != checkpoint.manifest_fingerprint:
                raise ValueError("campaign store refuses manifest drift for existing campaign_id")
            existing = self._db.execute(
                "SELECT payload_sha256 FROM autonomous_campaign_checkpoints WHERE checkpoint_fingerprint=?",
                (checkpoint.fingerprint,),
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != payload_sha:
                    raise RuntimeError("campaign checkpoint fingerprint collision/corruption")
                return
            self._db.execute(
                "INSERT INTO autonomous_campaign_checkpoints("
                "campaign_id,manifest_fingerprint,checkpoint_fingerprint,payload,payload_sha256) VALUES(?,?,?,?,?)",
                (checkpoint.campaign_id, checkpoint.manifest_fingerprint, checkpoint.fingerprint, rendered, payload_sha),
            )

    def latest(self, manifest: AutonomousCampaignManifest) -> CampaignCheckpoint | None:
        row = self._db.execute(
            "SELECT manifest_fingerprint,payload,payload_sha256 FROM autonomous_campaign_checkpoints "
            "WHERE campaign_id=? ORDER BY seq DESC LIMIT 1",
            (manifest.campaign_id,),
        ).fetchone()
        if row is None:
            return None
        if str(row[0]) != manifest.fingerprint:
            raise ValueError("campaign resume manifest identity drift")
        rendered = str(row[1])
        if sha256(rendered.encode("utf-8")).hexdigest() != str(row[2]):
            raise RuntimeError("campaign checkpoint payload integrity failure")
        data = json.loads(rendered)
        return CampaignCheckpoint(
            data["campaign_id"],
            data["manifest_fingerprint"],
            data["loop_fingerprint"],
            int(data["school_index"]),
            LoopState(data["loop_state"]),
            int(data["loop_iteration"]),
            int(data["step_index"]),
            tuple(data["completed_experiment_fingerprints"]),
            tuple(data["result_fingerprints"]),
            float(data["resource_seconds_used"]),
            data["last_action_fingerprint"],
            int(data["stagnant_steps"]),
            CampaignStatus(data["status"]),
            data["reason"],
            datetime.fromisoformat(data["created_at"]),
        )

    def integrity_ok(self) -> bool:
        if str(self._db.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
            return False
        rows = self._db.execute("SELECT payload,payload_sha256 FROM autonomous_campaign_checkpoints").fetchall()
        return all(sha256(str(payload).encode("utf-8")).hexdigest() == str(expected) for payload, expected in rows)

    def close(self) -> None:
        self._db.close()
