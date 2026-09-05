from __future__ import annotations

"""M185 immutable, append-only Frozen Champion Registry.

M185 is a custody/governance boundary, not a promotion algorithm.  A caller
must supply an external deterministic selection fingerprint plus already-passing
M174 robustness evidence and, when forecasts are part of the frozen Champion,
M184 forecast-integration evidence.  The registry never edits a Champion in
place.  A changed strategy/graph/tool identity is a new lineage member, and
lifecycle changes are append-only terminal events.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .forecast_integration_certification import (
    ForecastIntegrationCertification,
    ForecastIntegrationStatus,
)
from .robustness_gate import RobustnessCertification, RobustnessGateStatus
from .strategy_v3 import FrozenStrategyDeployment


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError("source commit requires a 40- or 64-character hexadecimal identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: str, label: str, *, maximum: int = 256) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _validate_robustness(certification: RobustnessCertification) -> None:
    expected = {
        "broker_calibration",
        "walk_forward",
        "parameter_neighborhood",
        "regime_torture",
        "cost_torture",
        "historical_forward_decay",
        "tail_risk",
        "strategy_dependency",
    }
    names = tuple(name for name, _ in certification.checks)
    if len(names) != len(set(names)) or set(names) != expected:
        raise ValueError("M174 robustness check identity drift")
    evidence = tuple(_sha(value, "M174 evidence") for value in certification.evidence_fingerprints)
    if not evidence or len(evidence) != len(set(evidence)):
        raise ValueError("M174 robustness evidence must be unique and nonempty")
    if certification.status is not RobustnessGateStatus.SERIOUS_CHALLENGER:
        raise ValueError("Frozen Champion requires M174 serious-challenger evidence")
    if certification.blockers:
        raise ValueError("passing M174 evidence cannot carry blockers")


def _validate_forecast(
    certification: ForecastIntegrationCertification,
    *,
    strategy_fingerprint: str,
    strategy_family: str,
) -> None:
    if certification.status is not ForecastIntegrationStatus.RESEARCH_INTEGRATION_ELIGIBLE:
        raise ValueError("forecast-enabled Champion requires M184 integration eligibility")
    if certification.blockers:
        raise ValueError("eligible M184 evidence cannot carry blockers")
    if _sha(certification.strategy_fingerprint, "M184 strategy") != strategy_fingerprint:
        raise ValueError("M184 strategy identity does not match frozen deployment")
    if _text(certification.strategy_family, "M184 strategy_family").lower() != strategy_family:
        raise ValueError("M184 strategy family does not match Champion family")
    for value, label in (
        (certification.evaluation_fingerprint, "M184 evaluation"),
        (certification.execution_cost_fingerprint, "M184 execution cost"),
        (certification.variant_fingerprint, "M184 forecast variant"),
        (certification.bucket_fingerprint, "M184 forecast bucket"),
        (certification.policy_fingerprint, "M184 policy"),
    ):
        _sha(value, label)
    evidence = tuple(_sha(value, "M184 evidence") for value in certification.evidence_fingerprints)
    if not evidence or len(evidence) != len(set(evidence)):
        raise ValueError("M184 evidence must be unique and nonempty")


@dataclass(frozen=True, slots=True)
class FrozenChampionRecord:
    lane_id: str
    generation_id: str
    strategy_family: str
    strategy_fingerprint: str
    analysis_graph_fingerprint: str
    tool_fingerprints: tuple[str, ...]
    source_commit: str
    selection_evidence_fingerprint: str
    robustness_fingerprint: str
    forecast_integration_fingerprint: str | None
    parent_champion_fingerprint: str | None
    created_at: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "lane_id", _text(self.lane_id, "Champion lane_id", maximum=128).lower())
        object.__setattr__(self, "generation_id", _text(self.generation_id, "Champion generation_id", maximum=128))
        object.__setattr__(self, "strategy_family", _text(self.strategy_family, "Champion strategy_family", maximum=128).lower())
        object.__setattr__(self, "strategy_fingerprint", _sha(self.strategy_fingerprint, "Champion strategy"))
        object.__setattr__(self, "analysis_graph_fingerprint", _sha(self.analysis_graph_fingerprint, "Champion graph"))
        tools = tuple(_sha(value, "Champion tool") for value in self.tool_fingerprints)
        if not tools or len(tools) != len(set(tools)):
            raise ValueError("Frozen Champion requires unique ordered tool fingerprints")
        object.__setattr__(self, "tool_fingerprints", tools)
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit))
        object.__setattr__(
            self,
            "selection_evidence_fingerprint",
            _sha(self.selection_evidence_fingerprint, "Champion selection evidence"),
        )
        object.__setattr__(self, "robustness_fingerprint", _sha(self.robustness_fingerprint, "Champion robustness"))
        if self.forecast_integration_fingerprint is not None:
            object.__setattr__(
                self,
                "forecast_integration_fingerprint",
                _sha(self.forecast_integration_fingerprint, "Champion forecast integration"),
            )
        if self.parent_champion_fingerprint is not None:
            object.__setattr__(
                self,
                "parent_champion_fingerprint",
                _sha(self.parent_champion_fingerprint, "Champion parent"),
            )
        object.__setattr__(self, "created_at", _aware(self.created_at, "Champion created_at"))
        if isinstance(self.schema_version, bool) or int(self.schema_version) != self.schema_version or self.schema_version != 1:
            raise ValueError("unsupported Frozen Champion schema version")

    @property
    def deployment_fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m185-frozen-deployment-v1",
                self.strategy_fingerprint,
                self.analysis_graph_fingerprint,
                self.tool_fingerprints,
                self.generation_id,
            )
        )

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m185-frozen-champion-v1",
            "schema_version": self.schema_version,
            "lane_id": self.lane_id,
            "generation_id": self.generation_id,
            "strategy_family": self.strategy_family,
            "strategy_fingerprint": self.strategy_fingerprint,
            "analysis_graph_fingerprint": self.analysis_graph_fingerprint,
            "tool_fingerprints": list(self.tool_fingerprints),
            "deployment_fingerprint": self.deployment_fingerprint,
            "source_commit": self.source_commit,
            "selection_evidence_fingerprint": self.selection_evidence_fingerprint,
            "robustness_fingerprint": self.robustness_fingerprint,
            "forecast_integration_fingerprint": self.forecast_integration_fingerprint,
            "parent_champion_fingerprint": self.parent_champion_fingerprint,
            "created_at": self.created_at.isoformat(),
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def strategy_mutation_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def freeze_champion_record(
    *,
    lane_id: str,
    strategy_family: str,
    deployment: FrozenStrategyDeployment,
    source_commit: str,
    selection_evidence_fingerprint: str,
    robustness: RobustnessCertification,
    forecast_integration: ForecastIntegrationCertification | None,
    parent_champion_fingerprint: str | None,
    created_at: datetime,
) -> FrozenChampionRecord:
    """Build an immutable record from externally selected, certified evidence.

    ``selection_evidence_fingerprint`` is intentionally mandatory: M185 does not
    turn M174/M184 research eligibility into a promotion decision by itself.
    """

    _validate_robustness(robustness)
    strategy = _sha(deployment.strategy_hash, "deployment strategy")
    graph = _sha(deployment.graph_hash, "deployment graph")
    family = _text(strategy_family, "strategy_family", maximum=128).lower()
    tools = tuple(_sha(value, "deployment tool") for value in deployment.tool_fingerprints)
    if not tools or len(tools) != len(set(tools)):
        raise ValueError("deployment requires unique ordered analytical tools")
    forecast_fp: str | None = None
    if forecast_integration is not None:
        _validate_forecast(forecast_integration, strategy_fingerprint=strategy, strategy_family=family)
        forecast_fp = forecast_integration.fingerprint
    return FrozenChampionRecord(
        lane_id,
        deployment.generation_id,
        family,
        strategy,
        graph,
        tools,
        source_commit,
        selection_evidence_fingerprint,
        robustness.fingerprint,
        forecast_fp,
        parent_champion_fingerprint,
        created_at,
    )


class ChampionLifecycleEventType(StrEnum):
    REGISTERED = "registered"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    SUPERSEDED = "superseded"


class ChampionLifecycleState(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class ChampionLifecycleEvent:
    champion_fingerprint: str
    event_type: ChampionLifecycleEventType
    actor_fingerprint: str
    evidence_fingerprints: tuple[str, ...]
    reason: str
    created_at: datetime
    successor_fingerprint: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "champion_fingerprint", _sha(self.champion_fingerprint, "lifecycle Champion"))
        object.__setattr__(self, "actor_fingerprint", _sha(self.actor_fingerprint, "lifecycle actor"))
        evidence = tuple(sorted(_sha(value, "lifecycle evidence") for value in self.evidence_fingerprints))
        if not evidence or len(evidence) != len(set(evidence)):
            raise ValueError("lifecycle event requires unique evidence fingerprints")
        object.__setattr__(self, "evidence_fingerprints", evidence)
        object.__setattr__(self, "reason", _text(self.reason, "lifecycle reason", maximum=512))
        object.__setattr__(self, "created_at", _aware(self.created_at, "lifecycle created_at"))
        if self.event_type is ChampionLifecycleEventType.SUPERSEDED:
            if self.successor_fingerprint is None:
                raise ValueError("superseded event requires successor fingerprint")
            object.__setattr__(self, "successor_fingerprint", _sha(self.successor_fingerprint, "successor Champion"))
            if self.successor_fingerprint == self.champion_fingerprint:
                raise ValueError("Champion cannot supersede itself")
        elif self.successor_fingerprint is not None:
            raise ValueError("successor fingerprint is only valid for superseded events")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m185-champion-lifecycle-event-v1",
            "champion_fingerprint": self.champion_fingerprint,
            "event_type": self.event_type.value,
            "actor_fingerprint": self.actor_fingerprint,
            "evidence_fingerprints": list(self.evidence_fingerprints),
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "successor_fingerprint": self.successor_fingerprint,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


class ChampionRegistryIntegrityError(RuntimeError):
    pass


class FrozenChampionRegistry:
    """SQLite-backed append-only Champion custody with fail-closed lineage."""

    def __init__(self, path: str | Path = ":memory:", *, busy_timeout_ms: int = 5000) -> None:
        if not 1 <= busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000")
        self._db = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000.0, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS frozen_champions("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "champion_fingerprint TEXT NOT NULL UNIQUE,"
            "lane_id TEXT NOT NULL,"
            "generation_id TEXT NOT NULL,"
            "parent_champion_fingerprint TEXT,"
            "payload TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "UNIQUE(lane_id,generation_id))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS champion_lifecycle_events("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "event_fingerprint TEXT NOT NULL UNIQUE,"
            "champion_fingerprint TEXT NOT NULL,"
            "event_type TEXT NOT NULL,"
            "payload TEXT NOT NULL,"
            "payload_sha256 TEXT NOT NULL,"
            "created_at TEXT NOT NULL,"
            "FOREIGN KEY(champion_fingerprint) REFERENCES frozen_champions(champion_fingerprint))"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_champion_lane ON frozen_champions(lane_id,seq)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_champion_events ON champion_lifecycle_events(champion_fingerprint,seq)"
        )

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def promotion_authorized(self) -> bool:
        return False

    @property
    def strategy_mutation_authorized(self) -> bool:
        return False

    def _begin(self) -> None:
        self._db.execute("BEGIN IMMEDIATE")

    def _commit(self) -> None:
        self._db.execute("COMMIT")

    def _rollback(self) -> None:
        self._db.execute("ROLLBACK")

    def _insert_event(self, event: ChampionLifecycleEvent) -> None:
        rendered = _canonical(event.payload)
        payload_sha = sha256(rendered.encode("utf-8")).hexdigest()
        existing = self._db.execute(
            "SELECT payload_sha256 FROM champion_lifecycle_events WHERE event_fingerprint=?",
            (event.fingerprint,),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != payload_sha:
                raise ChampionRegistryIntegrityError("lifecycle event fingerprint collision/corruption")
            return
        self._db.execute(
            "INSERT INTO champion_lifecycle_events("
            "event_fingerprint,champion_fingerprint,event_type,payload,payload_sha256,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (
                event.fingerprint,
                event.champion_fingerprint,
                event.event_type.value,
                rendered,
                payload_sha,
                event.created_at.isoformat(),
            ),
        )

    def register(
        self,
        champion: FrozenChampionRecord,
        *,
        actor_fingerprint: str,
        evidence_fingerprints: Iterable[str],
        reason: str,
    ) -> FrozenChampionRecord:
        actor = _sha(actor_fingerprint, "registration actor")
        evidence = tuple(evidence_fingerprints)
        rendered = _canonical(champion.payload)
        payload_sha = sha256(rendered.encode("utf-8")).hexdigest()
        self._begin()
        try:
            existing_fp = self._db.execute(
                "SELECT champion_fingerprint,payload_sha256 FROM frozen_champions WHERE lane_id=? AND generation_id=?",
                (champion.lane_id, champion.generation_id),
            ).fetchone()
            if existing_fp is not None:
                if str(existing_fp[0]) != champion.fingerprint or str(existing_fp[1]) != payload_sha:
                    raise ChampionRegistryIntegrityError("Champion generation is immutable and already occupied")
                self._commit()
                return champion

            lane_rows = self._db.execute(
                "SELECT champion_fingerprint FROM frozen_champions WHERE lane_id=? ORDER BY seq",
                (champion.lane_id,),
            ).fetchall()
            if not lane_rows:
                if champion.parent_champion_fingerprint is not None:
                    raise ValueError("first Champion in a lane cannot claim a parent")
            else:
                if champion.parent_champion_fingerprint is None:
                    raise ValueError("successor Champion requires explicit parent lineage")
                parent = self.get(champion.parent_champion_fingerprint)
                if parent is None or parent.lane_id != champion.lane_id:
                    raise ValueError("successor parent must exist in the same Champion lane")
                if (
                    champion.strategy_fingerprint == parent.strategy_fingerprint
                    and champion.analysis_graph_fingerprint == parent.analysis_graph_fingerprint
                    and champion.tool_fingerprints == parent.tool_fingerprints
                ):
                    raise ValueError("unchanged deployment cannot masquerade as a new Champion generation")
                parent_event = self.latest_event(parent.fingerprint)
                if parent_event is None or parent_event.event_type is not ChampionLifecycleEventType.SUPERSEDED:
                    raise ValueError("parent Champion must be explicitly superseded before successor registration")
                if parent_event.successor_fingerprint != champion.fingerprint:
                    raise ValueError("parent supersession does not name this exact successor fingerprint")

            self._db.execute(
                "INSERT INTO frozen_champions("
                "champion_fingerprint,lane_id,generation_id,parent_champion_fingerprint,payload,payload_sha256,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    champion.fingerprint,
                    champion.lane_id,
                    champion.generation_id,
                    champion.parent_champion_fingerprint,
                    rendered,
                    payload_sha,
                    champion.created_at.isoformat(),
                ),
            )
            self._insert_event(
                ChampionLifecycleEvent(
                    champion.fingerprint,
                    ChampionLifecycleEventType.REGISTERED,
                    actor,
                    tuple(evidence),
                    reason,
                    champion.created_at,
                )
            )
            self._commit()
            return champion
        except Exception:
            self._rollback()
            raise

    def append_terminal_event(self, event: ChampionLifecycleEvent) -> ChampionLifecycleEvent:
        if event.event_type is ChampionLifecycleEventType.REGISTERED:
            raise ValueError("registered lifecycle event is created only by register()")
        self._begin()
        try:
            champion = self.get(event.champion_fingerprint)
            if champion is None:
                raise KeyError(event.champion_fingerprint)
            latest = self.latest_event(event.champion_fingerprint)
            if latest is None or latest.event_type is not ChampionLifecycleEventType.REGISTERED:
                raise ValueError("Champion lifecycle is terminal after suspension/retirement/supersession")
            if event.created_at < latest.created_at:
                raise ValueError("Champion lifecycle time cannot move backwards")
            if event.event_type is ChampionLifecycleEventType.SUPERSEDED:
                assert event.successor_fingerprint is not None
                if self.get(event.successor_fingerprint) is not None:
                    raise ValueError("successor must not already be registered when parent is superseded")
            self._insert_event(event)
            self._commit()
            return event
        except Exception:
            self._rollback()
            raise

    def get(self, champion_fingerprint: str) -> FrozenChampionRecord | None:
        fingerprint = _sha(champion_fingerprint, "Champion lookup")
        row = self._db.execute(
            "SELECT payload,payload_sha256 FROM frozen_champions WHERE champion_fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if row is None:
            return None
        rendered = str(row[0])
        if sha256(rendered.encode("utf-8")).hexdigest() != str(row[1]):
            raise ChampionRegistryIntegrityError("Champion registry payload hash mismatch")
        data = json.loads(rendered)
        record = FrozenChampionRecord(
            data["lane_id"],
            data["generation_id"],
            data["strategy_family"],
            data["strategy_fingerprint"],
            data["analysis_graph_fingerprint"],
            tuple(data["tool_fingerprints"]),
            data["source_commit"],
            data["selection_evidence_fingerprint"],
            data["robustness_fingerprint"],
            data["forecast_integration_fingerprint"],
            data["parent_champion_fingerprint"],
            datetime.fromisoformat(data["created_at"]),
            int(data["schema_version"]),
        )
        if record.fingerprint != fingerprint:
            raise ChampionRegistryIntegrityError("Champion fingerprint does not match stored payload")
        return record

    def latest_event(self, champion_fingerprint: str) -> ChampionLifecycleEvent | None:
        fingerprint = _sha(champion_fingerprint, "Champion event lookup")
        row = self._db.execute(
            "SELECT payload,payload_sha256 FROM champion_lifecycle_events "
            "WHERE champion_fingerprint=? ORDER BY seq DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        if row is None:
            return None
        rendered = str(row[0])
        if sha256(rendered.encode("utf-8")).hexdigest() != str(row[1]):
            raise ChampionRegistryIntegrityError("Champion lifecycle payload hash mismatch")
        data = json.loads(rendered)
        return ChampionLifecycleEvent(
            data["champion_fingerprint"],
            ChampionLifecycleEventType(data["event_type"]),
            data["actor_fingerprint"],
            tuple(data["evidence_fingerprints"]),
            data["reason"],
            datetime.fromisoformat(data["created_at"]),
            data["successor_fingerprint"],
        )

    def state(self, champion_fingerprint: str) -> ChampionLifecycleState:
        event = self.latest_event(champion_fingerprint)
        if event is None:
            raise KeyError(champion_fingerprint)
        return {
            ChampionLifecycleEventType.REGISTERED: ChampionLifecycleState.ACTIVE,
            ChampionLifecycleEventType.SUSPENDED: ChampionLifecycleState.SUSPENDED,
            ChampionLifecycleEventType.RETIRED: ChampionLifecycleState.RETIRED,
            ChampionLifecycleEventType.SUPERSEDED: ChampionLifecycleState.SUPERSEDED,
        }[event.event_type]

    def active_for_lane(self, lane_id: str) -> FrozenChampionRecord | None:
        lane = _text(lane_id, "Champion lane query", maximum=128).lower()
        rows = self._db.execute(
            "SELECT champion_fingerprint FROM frozen_champions WHERE lane_id=? ORDER BY seq",
            (lane,),
        ).fetchall()
        active = tuple(
            record
            for (fingerprint,) in rows
            if (record := self.get(str(fingerprint))) is not None
            and self.state(record.fingerprint) is ChampionLifecycleState.ACTIVE
        )
        if len(active) > 1:
            raise ChampionRegistryIntegrityError("multiple active Champions detected in one lane")
        return active[0] if active else None

    def lineage(self, lane_id: str) -> tuple[FrozenChampionRecord, ...]:
        lane = _text(lane_id, "Champion lane query", maximum=128).lower()
        rows = self._db.execute(
            "SELECT champion_fingerprint FROM frozen_champions WHERE lane_id=? ORDER BY seq",
            (lane,),
        ).fetchall()
        return tuple(self.get(str(row[0])) for row in rows)  # type: ignore[arg-type]

    def integrity_check(self) -> tuple[bool, tuple[str, ...]]:
        errors: list[str] = []
        db_result = str(self._db.execute("PRAGMA integrity_check").fetchone()[0])
        if db_result.lower() != "ok":
            errors.append(f"sqlite:{db_result}")
        champion_rows = self._db.execute(
            "SELECT champion_fingerprint FROM frozen_champions ORDER BY seq"
        ).fetchall()
        for (fingerprint,) in champion_rows:
            try:
                record = self.get(str(fingerprint))
                if record is None or self.latest_event(record.fingerprint) is None:
                    errors.append(f"champion:{fingerprint}:missing_lifecycle")
            except (ValueError, ChampionRegistryIntegrityError, json.JSONDecodeError) as exc:
                errors.append(f"champion:{fingerprint}:{type(exc).__name__}")
        event_rows = self._db.execute(
            "SELECT event_fingerprint,payload,payload_sha256 FROM champion_lifecycle_events ORDER BY seq"
        ).fetchall()
        for event_fp, payload, expected in event_rows:
            rendered = str(payload)
            if sha256(rendered.encode("utf-8")).hexdigest() != str(expected):
                errors.append(f"event:{event_fp}:payload_hash")
        return (not errors, tuple(errors))

    def close(self) -> None:
        self._db.close()
