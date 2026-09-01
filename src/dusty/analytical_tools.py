from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping, Sequence


Scalar = bool | int | float | str


class ToolKind(StrEnum):
    NATIVE_INDICATOR = "native_indicator"
    CUSTOM_INDICATOR = "custom_indicator"
    PRICE_STRUCTURE = "price_structure"
    DRAWING_TOOL = "drawing_tool"


class ToolRole(StrEnum):
    REGIME = "regime"
    DIRECTION = "direction"
    ENTRY = "entry"
    CONFIRMATION = "confirmation"
    HOLD = "hold"
    EXIT = "exit"
    PROTECTION = "protection"
    RISK_SUPPRESSION = "risk_suppression"
    VISUAL_ONLY = "visual_only"


class ToolOrigin(StrEnum):
    MT5_NATIVE = "mt5_native"
    USER_INSTALLED = "user_installed"
    USER_DRAWN = "user_drawn"
    DUSTY_GENERATED = "dusty_generated"
    IMPORTED_TEMPLATE = "imported_template"
    ONLINE_STRATEGY = "online_strategy"
    UNKNOWN = "unknown"


class ToolLifecycle(StrEnum):
    DISCOVERED = "discovered"
    QUARANTINED = "quarantined"
    CHARACTERIZED = "characterized"
    RESEARCH_APPROVED = "research_approved"
    CHALLENGER = "challenger"
    CERTIFIED_DEPENDENCY = "certified_dependency"
    REGIME_RESTRICTED = "regime_restricted"
    DEGRADED = "degraded"
    SUSPENDED = "suspended"
    RETIRED = "retired"
    INVALID = "invalid"


class TemporalBehavior(StrEnum):
    CAUSAL_COMPLETED_BAR = "causal_completed_bar"
    CURRENT_BAR_UNSTABLE = "current_bar_unstable"
    REPAINTING = "repainting"
    FUTURE_DEPENDENT = "future_dependent"
    UNDETERMINED = "undetermined"


@dataclass(frozen=True, slots=True)
class ToolParameter:
    name: str
    value: Scalar
    value_type: str

    def __post_init__(self) -> None:
        if not self.name.strip() or self.value_type not in {"bool", "int", "float", "str", "enum"}:
            raise ValueError("tool parameter requires a name and supported type")
        if self.value_type == "bool" and not isinstance(self.value, bool):
            raise ValueError("bool tool parameter has wrong value type")
        if self.value_type in {"int", "enum"} and (isinstance(self.value, bool) or not isinstance(self.value, int)):
            raise ValueError("integer tool parameter has wrong value type")
        if self.value_type == "float" and (isinstance(self.value, bool) or not isinstance(self.value, (int, float))):
            raise ValueError("float tool parameter has wrong value type")
        if self.value_type == "str" and not isinstance(self.value, str):
            raise ValueError("string tool parameter has wrong value type")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("tool parameter must be finite")


@dataclass(frozen=True, slots=True)
class ToolBuffer:
    index: int
    name: str
    role: ToolRole

    def __post_init__(self) -> None:
        if self.index < 0 or not self.name.strip():
            raise ValueError("tool buffer requires nonnegative index and name")


@dataclass(frozen=True, slots=True)
class AnalyticalToolSpec:
    tool_id: str
    version: str
    kind: ToolKind
    roles: tuple[ToolRole, ...]
    origin: ToolOrigin
    known_at: datetime
    parameters: tuple[ToolParameter, ...] = ()
    buffers: tuple[ToolBuffer, ...] = ()
    warmup_bars: int = 0
    artifact_path: str = ""
    artifact_hash: str = ""
    source_available: bool = False
    license_allows_use: bool = True
    license_allows_modification: bool = False
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.tool_id.strip() or not self.version.strip() or not self.roles:
            raise ValueError("analytical tool requires identity, version and role")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("tool known_at must be timezone-aware")
        if self.warmup_bars < 0:
            raise ValueError("tool warmup cannot be negative")
        if len({row.name for row in self.parameters}) != len(self.parameters):
            raise ValueError("tool parameter names must be unique")
        if len({row.index for row in self.buffers}) != len(self.buffers):
            raise ValueError("tool buffer indices must be unique")
        if self.kind is ToolKind.CUSTOM_INDICATOR:
            if not self.artifact_path.strip() or len(self.artifact_hash) != 64:
                raise ValueError("custom indicator requires path and SHA-256 hash")
            if not self.buffers:
                raise ValueError("custom indicator requires declared buffer semantics")
        if self.license_allows_modification and not self.source_available:
            raise ValueError("opaque analytical tools cannot be marked modifiable")

    @property
    def fingerprint(self) -> str:
        return sha256(_canonical(self.as_dict()).encode("utf-8")).hexdigest()

    @property
    def execution_eligible(self) -> bool:
        return self.license_allows_use and ToolRole.VISUAL_ONLY not in self.roles

    def as_dict(self) -> dict[str, object]:
        return {
            "tool_id": self.tool_id,
            "version": self.version,
            "kind": self.kind.value,
            "roles": [role.value for role in self.roles],
            "origin": self.origin.value,
            "known_at": self.known_at.isoformat(),
            "parameters": [
                {"name": row.name, "value": row.value, "value_type": row.value_type}
                for row in self.parameters
            ],
            "buffers": [
                {"index": row.index, "name": row.name, "role": row.role.value}
                for row in self.buffers
            ],
            "warmup_bars": self.warmup_bars,
            "artifact_path": self.artifact_path,
            "artifact_hash": self.artifact_hash,
            "source_available": self.source_available,
            "license_allows_use": self.license_allows_use,
            "license_allows_modification": self.license_allows_modification,
            "notes": self.notes,
        }


@dataclass(frozen=True, slots=True)
class ToolRevision:
    parent_fingerprint: str
    child: AnalyticalToolSpec
    rationale: str
    source_diff_hash: str = ""

    def __post_init__(self) -> None:
        if len(self.parent_fingerprint) != 64 or not self.rationale.strip():
            raise ValueError("tool revision requires parent fingerprint and rationale")
        if self.child.fingerprint == self.parent_fingerprint:
            raise ValueError("tool revision must create a distinct immutable child")
        if self.source_diff_hash and len(self.source_diff_hash) != 64:
            raise ValueError("tool revision source diff must be SHA-256")


@dataclass(frozen=True, slots=True)
class DiscoveredIndicatorArtifact:
    relative_path: str
    artifact_hash: str
    source_available: bool
    size_bytes: int

    def __post_init__(self) -> None:
        if not self.relative_path.strip() or len(self.artifact_hash) != 64 or self.size_bytes < 1:
            raise ValueError("discovered indicator artifact is invalid")


_TRANSITIONS: dict[ToolLifecycle, frozenset[ToolLifecycle]] = {
    ToolLifecycle.DISCOVERED: frozenset({ToolLifecycle.QUARANTINED, ToolLifecycle.INVALID}),
    ToolLifecycle.QUARANTINED: frozenset({ToolLifecycle.CHARACTERIZED, ToolLifecycle.INVALID, ToolLifecycle.RETIRED}),
    ToolLifecycle.CHARACTERIZED: frozenset({ToolLifecycle.RESEARCH_APPROVED, ToolLifecycle.INVALID, ToolLifecycle.RETIRED}),
    ToolLifecycle.RESEARCH_APPROVED: frozenset({ToolLifecycle.CHALLENGER, ToolLifecycle.DEGRADED, ToolLifecycle.RETIRED, ToolLifecycle.INVALID}),
    ToolLifecycle.CHALLENGER: frozenset({ToolLifecycle.CERTIFIED_DEPENDENCY, ToolLifecycle.REGIME_RESTRICTED, ToolLifecycle.DEGRADED, ToolLifecycle.RETIRED, ToolLifecycle.INVALID}),
    ToolLifecycle.CERTIFIED_DEPENDENCY: frozenset({ToolLifecycle.REGIME_RESTRICTED, ToolLifecycle.DEGRADED, ToolLifecycle.SUSPENDED, ToolLifecycle.RETIRED, ToolLifecycle.INVALID}),
    ToolLifecycle.REGIME_RESTRICTED: frozenset({ToolLifecycle.CHALLENGER, ToolLifecycle.DEGRADED, ToolLifecycle.SUSPENDED, ToolLifecycle.RETIRED, ToolLifecycle.INVALID}),
    ToolLifecycle.DEGRADED: frozenset({ToolLifecycle.CHALLENGER, ToolLifecycle.SUSPENDED, ToolLifecycle.RETIRED, ToolLifecycle.INVALID}),
    ToolLifecycle.SUSPENDED: frozenset({ToolLifecycle.CHALLENGER, ToolLifecycle.RETIRED, ToolLifecycle.INVALID}),
    ToolLifecycle.RETIRED: frozenset(),
    ToolLifecycle.INVALID: frozenset(),
}


class SQLiteAnalyticalToolRegistry:
    """Append-only analytical-tool identity and lifecycle registry."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tool_spec (fingerprint TEXT PRIMARY KEY, tool_id TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS tool_event (event_id INTEGER PRIMARY KEY AUTOINCREMENT, fingerprint TEXT NOT NULL REFERENCES tool_spec(fingerprint), state TEXT NOT NULL, at_epoch REAL NOT NULL, reason TEXT NOT NULL)"
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def register(self, spec: AnalyticalToolSpec, *, at: datetime, reason: str = "discovered") -> str:
        _aware(at)
        if not reason.strip():
            raise ValueError("tool registration reason is required")
        payload = _canonical(spec.as_dict())
        existing = self._db.execute(
            "SELECT payload FROM tool_spec WHERE fingerprint=?", (spec.fingerprint,)
        ).fetchone()
        if existing is not None:
            if existing[0] != payload:
                raise ValueError("tool fingerprint collision")
            return spec.fingerprint
        with self._db:
            self._db.execute(
                "INSERT INTO tool_spec(fingerprint,tool_id,payload) VALUES (?,?,?)",
                (spec.fingerprint, spec.tool_id, payload),
            )
            self._db.execute(
                "INSERT INTO tool_event(fingerprint,state,at_epoch,reason) VALUES (?,?,?,?)",
                (spec.fingerprint, ToolLifecycle.DISCOVERED.value, at.timestamp(), reason),
            )
        return spec.fingerprint

    def state(self, fingerprint: str) -> ToolLifecycle:
        row = self._db.execute(
            "SELECT state FROM tool_event WHERE fingerprint=? ORDER BY event_id DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        if row is None:
            raise KeyError(fingerprint)
        return ToolLifecycle(row[0])

    def transition(self, fingerprint: str, target: ToolLifecycle, *, at: datetime, reason: str) -> None:
        _aware(at)
        if not reason.strip():
            raise ValueError("tool lifecycle transition requires reason")
        current = self.state(fingerprint)
        if target not in _TRANSITIONS[current]:
            raise ValueError(f"illegal tool transition: {current.value}->{target.value}")
        last = self._db.execute(
            "SELECT at_epoch FROM tool_event WHERE fingerprint=? ORDER BY event_id DESC LIMIT 1",
            (fingerprint,),
        ).fetchone()
        if last is None or at.timestamp() < float(last[0]):
            raise ValueError("tool lifecycle events must be chronological")
        with self._db:
            self._db.execute(
                "INSERT INTO tool_event(fingerprint,state,at_epoch,reason) VALUES (?,?,?,?)",
                (fingerprint, target.value, at.timestamp(), reason),
            )

    def history(self, fingerprint: str) -> tuple[tuple[ToolLifecycle, float, str], ...]:
        rows = self._db.execute(
            "SELECT state,at_epoch,reason FROM tool_event WHERE fingerprint=? ORDER BY event_id",
            (fingerprint,),
        ).fetchall()
        return tuple((ToolLifecycle(state), float(at), reason) for state, at, reason in rows)

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone() == ("ok",)


@dataclass(frozen=True, slots=True)
class SeriesRevision:
    as_of: datetime
    values: tuple[tuple[datetime, float], ...]

    def __post_init__(self) -> None:
        _aware(self.as_of)
        if tuple(sorted(self.values, key=lambda row: row[0])) != self.values:
            raise ValueError("series revision values must be chronological")
        for at, value in self.values:
            _aware(at)
            if not math.isfinite(value):
                raise ValueError("series revision values must be finite")


def classify_temporal_behavior(revisions: Sequence[SeriesRevision]) -> TemporalBehavior:
    if len(revisions) < 2:
        return TemporalBehavior.UNDETERMINED
    if tuple(sorted(revisions, key=lambda row: row.as_of)) != tuple(revisions):
        raise ValueError("series revisions must be chronological")
    current_only_changed = False
    previous: SeriesRevision | None = None
    for revision in revisions:
        if any(source_at > revision.as_of for source_at, _ in revision.values):
            return TemporalBehavior.FUTURE_DEPENDENT
        if previous is not None:
            before = dict(previous.values)
            after = dict(revision.values)
            for source_at, old_value in before.items():
                if source_at not in after:
                    return TemporalBehavior.REPAINTING
                if not math.isclose(old_value, after[source_at], rel_tol=1e-12, abs_tol=1e-12):
                    if source_at < previous.as_of:
                        return TemporalBehavior.REPAINTING
                    current_only_changed = True
        previous = revision
    return TemporalBehavior.CURRENT_BAR_UNSTABLE if current_only_changed else TemporalBehavior.CAUSAL_COMPLETED_BAR


@dataclass(frozen=True, slots=True)
class ToolDiagnostic:
    temporal_behavior: TemporalBehavior
    buffer_semantics_known: bool
    native_reproducible: bool
    file_hash_matches: bool
    operational: bool
    sample_count: int
    incremental_expectancy: float
    redundant: bool = False
    regime_expectancy: tuple[tuple[str, float], ...] = ()
    repair_hypothesis: str = ""

    def __post_init__(self) -> None:
        if self.sample_count < 0 or not math.isfinite(self.incremental_expectancy):
            raise ValueError("tool diagnostic sample/expectancy is invalid")
        if any(not name.strip() or not math.isfinite(value) for name, value in self.regime_expectancy):
            raise ValueError("tool regime diagnostic is invalid")


@dataclass(frozen=True, slots=True)
class LifecycleRecommendation:
    target: ToolLifecycle
    reasons: tuple[str, ...]
    modification_warranted: bool


def recommend_lifecycle(diagnostic: ToolDiagnostic, *, minimum_samples: int = 30) -> LifecycleRecommendation:
    if minimum_samples < 1:
        raise ValueError("minimum samples must be positive")
    invalid_reasons: list[str] = []
    if diagnostic.temporal_behavior in {TemporalBehavior.REPAINTING, TemporalBehavior.FUTURE_DEPENDENT}:
        invalid_reasons.append(f"temporal_invalid:{diagnostic.temporal_behavior.value}")
    if not diagnostic.buffer_semantics_known:
        invalid_reasons.append("unknown_buffer_semantics")
    if not diagnostic.file_hash_matches:
        invalid_reasons.append("artifact_hash_drift")
    if invalid_reasons:
        return LifecycleRecommendation(ToolLifecycle.INVALID, tuple(invalid_reasons), False)
    if not diagnostic.operational or not diagnostic.native_reproducible:
        reasons = ("operational_failure",) if not diagnostic.operational else ("native_reproduction_failed",)
        return LifecycleRecommendation(ToolLifecycle.SUSPENDED, reasons, bool(diagnostic.repair_hypothesis.strip()))
    if diagnostic.sample_count < minimum_samples:
        return LifecycleRecommendation(ToolLifecycle.RESEARCH_APPROVED, ("insufficient_evidence",), False)
    positive_regimes = tuple(name for name, value in diagnostic.regime_expectancy if value > 0)
    if diagnostic.incremental_expectancy <= 0 and positive_regimes:
        return LifecycleRecommendation(
            ToolLifecycle.REGIME_RESTRICTED,
            tuple(f"positive_only_in_regime:{name}" for name in positive_regimes),
            bool(diagnostic.repair_hypothesis.strip()),
        )
    if diagnostic.redundant:
        return LifecycleRecommendation(ToolLifecycle.RETIRED, ("no_incremental_information",), False)
    if diagnostic.incremental_expectancy <= 0:
        target = ToolLifecycle.DEGRADED if diagnostic.repair_hypothesis.strip() else ToolLifecycle.RETIRED
        return LifecycleRecommendation(target, ("nonpositive_incremental_expectancy",), bool(diagnostic.repair_hypothesis.strip()))
    return LifecycleRecommendation(ToolLifecycle.CHALLENGER, ("positive_incremental_expectancy",), False)


def hash_artifact(path: Path, *, allowed_root: Path) -> str:
    root = allowed_root.resolve(strict=True)
    candidate = path.resolve(strict=True)
    if root != candidate and root not in candidate.parents:
        raise ValueError("analytical artifact must remain inside approved root")
    if not candidate.is_file() or candidate.suffix.lower() not in {".ex5", ".mq5"}:
        raise ValueError("analytical artifact must be an MQ5/EX5 file")
    return sha256(candidate.read_bytes()).hexdigest()


def discover_indicator_files(
    root: Path,
    *,
    maximum_files: int = 1_000,
    maximum_file_bytes: int = 50_000_000,
) -> tuple[DiscoveredIndicatorArtifact, ...]:
    """Inventory bounded MQ5/EX5 artifacts without loading or executing them."""
    if maximum_files < 1 or maximum_file_bytes < 1:
        raise ValueError("indicator discovery budgets must be positive")
    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise ValueError("indicator discovery root must be a directory")
    candidates = tuple(
        path
        for path in sorted(resolved_root.rglob("*"))
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in {".mq5", ".ex5"}
    )
    if len(candidates) > maximum_files:
        raise ValueError("indicator discovery file budget exceeded")
    rows: list[DiscoveredIndicatorArtifact] = []
    source_stems = {path.relative_to(resolved_root).with_suffix("").as_posix().lower() for path in candidates if path.suffix.lower() == ".mq5"}
    for path in candidates:
        size = path.stat().st_size
        if size < 1 or size > maximum_file_bytes:
            raise ValueError("indicator artifact size budget exceeded")
        relative = path.relative_to(resolved_root).as_posix()
        stem = path.relative_to(resolved_root).with_suffix("").as_posix().lower()
        rows.append(
            DiscoveredIndicatorArtifact(
                relative,
                hash_artifact(path, allowed_root=resolved_root),
                path.suffix.lower() == ".mq5" or stem in source_stems,
                size,
            )
        )
    return tuple(rows)


def _aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
