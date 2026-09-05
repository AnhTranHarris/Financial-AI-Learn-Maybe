from __future__ import annotations

"""M160 deterministic research-value scheduler and durable loop governor."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from typing import Iterable, Iterator

from .controlled_evolution import EvolutionAction, EvolutionDecision, ExperimentOutcome, ExperimentOutcomeType
from .experiment_manifest import EvaluationStage, ExperimentManifest
from .experiment_queue import ExperimentJobSpec, ExperimentResource
from .strategy_family import ExhaustionAssessment, ExhaustionSignal


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _text(value: str, label: str) -> str:
    rendered = str(value).strip()
    if not rendered:
        raise ValueError(f"{label} required")
    return rendered


def _unit(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered) or not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{label} must be finite in [0, 1]")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


class LoopState(StrEnum):
    PROPOSED = "proposed"
    TESTING = "testing"
    FAILED_RESEARCHABLE = "failed_researchable"
    CHALLENGER_CREATED = "challenger_created"
    RETESTING = "retesting"
    PASSED_STAGE = "passed_stage"
    EXHAUSTION_WARNING = "exhaustion_warning"
    EXHAUSTED = "exhausted"
    GRAVEYARD = "graveyard"
    REOPEN_ELIGIBLE = "reopen_eligible"


class GovernorAction(StrEnum):
    ADMIT = "admit"
    ADVANCE_STAGE = "advance_stage"
    RETRY_EXACT = "retry_exact"
    CREATE_CHALLENGER = "create_challenger"
    REGISTER_CHALLENGER = "register_challenger"
    WARN_EXHAUSTION = "warn_exhaustion"
    EXHAUST = "exhaust"
    ARCHIVE_GRAVEYARD = "archive_graveyard"
    REOPEN = "reopen"
    HOLD = "hold"


class ReopenChangeKind(StrEnum):
    DATASET = "dataset"
    EVALUATION_POLICY = "evaluation_policy"
    MARKET_REGIME = "market_regime"
    CONTEXT = "context"
    EXTERNAL_EVIDENCE = "external_evidence"
    SOFTWARE = "software"


_ADMISSION_STATES = frozenset({LoopState.PROPOSED, LoopState.CHALLENGER_CREATED})


@dataclass(frozen=True, slots=True)
class SchedulerPolicy:
    information_weight: float = 0.30
    resolution_weight: float = 0.25
    novelty_weight: float = 0.20
    strategic_weight: float = 0.25
    cost_penalty: float = 0.45
    aging_rate: float = 0.02
    maximum_age_bonus: float = 0.20
    version: str = "m160-research-value-v1"

    def __post_init__(self) -> None:
        weights = (self.information_weight, self.resolution_weight, self.novelty_weight, self.strategic_weight)
        if any(not math.isfinite(value) or value < 0.0 for value in weights):
            raise ValueError("scheduler weights must be finite and nonnegative")
        if not math.isclose(sum(weights), 1.0, rel_tol=0.0, abs_tol=1e-12):
            raise ValueError("scheduler weights must sum to 1")
        for name in ("cost_penalty", "aging_rate", "maximum_age_bonus"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        object.__setattr__(self, "version", _text(self.version, "scheduler policy version"))


@dataclass(frozen=True, slots=True)
class ResearchCandidate:
    manifest_fingerprint: str
    execution_fingerprint: str
    family_fingerprint: str
    resource: ExperimentResource
    state: LoopState
    expected_information_gain: float
    failure_resolution_probability: float
    novelty: float
    strategic_value: float
    normalized_compute_cost: float
    age_steps: int
    admission_sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_fingerprint", _sha(self.manifest_fingerprint, "candidate manifest"))
        object.__setattr__(self, "execution_fingerprint", _sha(self.execution_fingerprint, "candidate execution"))
        object.__setattr__(self, "family_fingerprint", _sha(self.family_fingerprint, "candidate family"))
        for name in ("expected_information_gain", "failure_resolution_probability", "novelty", "strategic_value", "normalized_compute_cost"):
            object.__setattr__(self, name, _unit(getattr(self, name), name))
        for name in ("age_steps", "admission_sequence"):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) != value or int(value) < 0:
                raise ValueError(f"candidate {name} must be a nonnegative integer")
            object.__setattr__(self, name, int(value))
        if self.state not in _ADMISSION_STATES:
            raise ValueError("candidate state is not eligible for research admission")

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class RankedCandidate:
    candidate: ResearchCandidate
    score: float
    base_value: float
    cost_adjusted_value: float
    age_bonus: float
    policy_version: str


def score_candidate(candidate: ResearchCandidate, policy: SchedulerPolicy = SchedulerPolicy()) -> RankedCandidate:
    base = (
        candidate.expected_information_gain * policy.information_weight
        + candidate.failure_resolution_probability * policy.resolution_weight
        + candidate.novelty * policy.novelty_weight
        + candidate.strategic_value * policy.strategic_weight
    )
    cost_adjusted = base * (1.0 - policy.cost_penalty * candidate.normalized_compute_cost)
    age_bonus = min(candidate.age_steps * policy.aging_rate, policy.maximum_age_bonus)
    score = min(1.0, max(0.0, cost_adjusted + age_bonus))
    return RankedCandidate(candidate, score, base, cost_adjusted, age_bonus, policy.version)


def rank_candidates(candidates: Iterable[ResearchCandidate], *, policy: SchedulerPolicy = SchedulerPolicy()) -> tuple[RankedCandidate, ...]:
    rows = tuple(candidates)
    manifests = tuple(row.manifest_fingerprint for row in rows)
    executions = tuple(row.execution_fingerprint for row in rows)
    if len(manifests) != len(set(manifests)):
        raise ValueError("research admission batch cannot contain duplicate manifest fingerprints")
    if len(executions) != len(set(executions)):
        raise ValueError("research admission batch cannot contain duplicate execution fingerprints")
    ranked = tuple(score_candidate(row, policy) for row in rows)
    return tuple(sorted(ranked, key=lambda row: (-row.score, row.candidate.admission_sequence, row.candidate.manifest_fingerprint)))


def admission_queue_spec(manifest: ExperimentManifest, *, symbol: str, timeframe: str, max_attempts: int = 3) -> ExperimentJobSpec:
    """Rank before admission; do not mutate M155's content-addressed priority later."""
    return manifest.to_queue_spec(symbol=symbol, timeframe=timeframe, priority=0, max_attempts=max_attempts)


@dataclass(frozen=True, slots=True)
class ResearchLoopRecord:
    loop_fingerprint: str
    root_manifest_fingerprint: str
    active_manifest_fingerprint: str
    root_execution_fingerprint: str
    active_execution_fingerprint: str
    active_subject_fingerprint: str
    family_fingerprint: str
    stage: EvaluationStage
    state: LoopState
    iteration: int
    evidence_fingerprints: tuple[str, ...]
    last_outcome_fingerprint: str | None
    exhaustion_signal: ExhaustionSignal
    created_at: datetime
    updated_at: datetime
    broker_write_authority: bool = False
    risk_override_authority: bool = False
    entry_veto_authority: bool = False
    promotion_authority: bool = False

    def __post_init__(self) -> None:
        for name in ("loop_fingerprint", "root_manifest_fingerprint", "active_manifest_fingerprint", "root_execution_fingerprint", "active_execution_fingerprint", "active_subject_fingerprint", "family_fingerprint"):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        if isinstance(self.iteration, bool) or int(self.iteration) != self.iteration or int(self.iteration) < 0:
            raise ValueError("loop iteration must be a nonnegative integer")
        object.__setattr__(self, "iteration", int(self.iteration))
        object.__setattr__(self, "evidence_fingerprints", tuple(sorted({_sha(value, "loop evidence") for value in self.evidence_fingerprints})))
        if self.last_outcome_fingerprint is not None:
            object.__setattr__(self, "last_outcome_fingerprint", _sha(self.last_outcome_fingerprint, "loop outcome"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "loop created_at"))
        object.__setattr__(self, "updated_at", _aware(self.updated_at, "loop updated_at"))
        if self.updated_at < self.created_at:
            raise ValueError("loop updated_at cannot precede created_at")
        if any((self.broker_write_authority, self.risk_override_authority, self.entry_veto_authority, self.promotion_authority)):
            raise ValueError("research loop cannot receive operational trading authority")


@dataclass(frozen=True, slots=True)
class GovernorDecision:
    action: GovernorAction
    from_state: LoopState
    to_state: LoopState
    reason: str
    active_execution_fingerprint: str
    outcome_fingerprint: str | None = None
    challenger_execution_fingerprints: tuple[str, ...] = ()
    exhaustion_signal: ExhaustionSignal = ExhaustionSignal.NONE

    def __post_init__(self) -> None:
        object.__setattr__(self, "reason", _text(self.reason, "governor decision reason"))
        object.__setattr__(self, "active_execution_fingerprint", _sha(self.active_execution_fingerprint, "governor execution"))
        if self.outcome_fingerprint is not None:
            object.__setattr__(self, "outcome_fingerprint", _sha(self.outcome_fingerprint, "governor outcome"))
        challengers = tuple(sorted({_sha(value, "governor challenger") for value in self.challenger_execution_fingerprints}))
        object.__setattr__(self, "challenger_execution_fingerprints", challengers)
        if self.action is GovernorAction.RETRY_EXACT and self.from_state is not self.to_state:
            raise ValueError("exact retry must preserve testing state")
        if self.action is GovernorAction.CREATE_CHALLENGER and not challengers:
            raise ValueError("challenger action requires Challenger execution fingerprints")
        if self.action is not GovernorAction.CREATE_CHALLENGER and challengers:
            raise ValueError("only Challenger action may carry Challenger executions")


@dataclass(frozen=True, slots=True)
class ReopenEvidence:
    evidence_fingerprint: str
    previous_context_fingerprint: str
    new_context_fingerprint: str
    change_kind: ReopenChangeKind
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_fingerprint", _sha(self.evidence_fingerprint, "reopen evidence"))
        object.__setattr__(self, "previous_context_fingerprint", _sha(self.previous_context_fingerprint, "previous reopen context"))
        object.__setattr__(self, "new_context_fingerprint", _sha(self.new_context_fingerprint, "new reopen context"))
        object.__setattr__(self, "reason", _text(self.reason, "reopen reason"))
        if self.previous_context_fingerprint == self.new_context_fingerprint:
            raise ValueError("reopen requires materially changed context identity")


def govern_outcome(record: ResearchLoopRecord, outcome: ExperimentOutcome, evolution: EvolutionDecision, exhaustion: ExhaustionAssessment) -> GovernorDecision:
    if record.state not in {LoopState.TESTING, LoopState.RETESTING}:
        raise ValueError("experiment outcome can only govern an active testing state")
    if outcome.subject_fingerprint != record.active_subject_fingerprint or evolution.subject_fingerprint != record.active_subject_fingerprint:
        raise ValueError("outcome/evolution does not bind active M158 strategy subject")
    if evolution.outcome_fingerprint != outcome.fingerprint:
        raise ValueError("evolution decision does not bind exact experiment outcome")
    if outcome.outcome is ExperimentOutcomeType.INFRASTRUCTURE_FAILED:
        if evolution.action is not EvolutionAction.RETRY_EXACT:
            raise ValueError("infrastructure failure must remain an exact retry")
        if evolution.exact_retry_execution_fingerprint != record.active_execution_fingerprint:
            raise ValueError("infrastructure retry must preserve exact active execution")
        return GovernorDecision(GovernorAction.RETRY_EXACT, record.state, record.state, "infrastructure failure preserves immutable execution", record.active_execution_fingerprint, outcome.fingerprint)
    if outcome.outcome is ExperimentOutcomeType.PASSED:
        if evolution.action is not EvolutionAction.ADVANCE:
            raise ValueError("passed experiment requires M158 advance decision")
        return GovernorDecision(GovernorAction.ADVANCE_STAGE, record.state, LoopState.PASSED_STAGE, "stage evidence passed", record.active_execution_fingerprint, outcome.fingerprint)
    if evolution.action not in {EvolutionAction.CREATE_CHALLENGER, EvolutionAction.STOP_RESEARCH}:
        raise ValueError("research failure requires Challenger or stop-research decision")
    if exhaustion.signal in {ExhaustionSignal.WARNING, ExhaustionSignal.STRONG}:
        return GovernorDecision(GovernorAction.WARN_EXHAUSTION, record.state, LoopState.EXHAUSTION_WARNING, f"family exhaustion evidence={exhaustion.signal.value}", record.active_execution_fingerprint, outcome.fingerprint, exhaustion_signal=exhaustion.signal)
    if evolution.action is EvolutionAction.STOP_RESEARCH:
        return GovernorDecision(GovernorAction.HOLD, record.state, LoopState.FAILED_RESEARCHABLE, "research failed without an evidence-supported Challenger", record.active_execution_fingerprint, outcome.fingerprint)
    challengers = tuple(row.compiled_genome.execution_fingerprint for row in evolution.challengers)
    return GovernorDecision(GovernorAction.CREATE_CHALLENGER, record.state, LoopState.FAILED_RESEARCHABLE, "research failure produced bounded Challenger descendants", record.active_execution_fingerprint, outcome.fingerprint, challengers)


def review_exhaustion_warning(record: ResearchLoopRecord, exhaustion: ExhaustionAssessment) -> GovernorDecision:
    if record.state is not LoopState.EXHAUSTION_WARNING:
        raise ValueError("exhaustion review requires EXHAUSTION_WARNING state")
    if exhaustion.signal is ExhaustionSignal.STRONG:
        return GovernorDecision(GovernorAction.EXHAUST, record.state, LoopState.EXHAUSTED, "subsequent evidence confirms strong family exhaustion", record.active_execution_fingerprint, exhaustion_signal=exhaustion.signal)
    if exhaustion.signal is ExhaustionSignal.WARNING:
        return GovernorDecision(GovernorAction.HOLD, record.state, record.state, "family remains under exhaustion warning", record.active_execution_fingerprint, exhaustion_signal=exhaustion.signal)
    return GovernorDecision(GovernorAction.HOLD, record.state, LoopState.FAILED_RESEARCHABLE, "new evidence no longer supports exhaustion warning", record.active_execution_fingerprint)


def archive_graveyard(record: ResearchLoopRecord) -> GovernorDecision:
    if record.state is not LoopState.EXHAUSTED:
        raise ValueError("only exhausted research can enter Graveyard")
    return GovernorDecision(GovernorAction.ARCHIVE_GRAVEYARD, record.state, LoopState.GRAVEYARD, "exhausted family archived without deleting evidence", record.active_execution_fingerprint, exhaustion_signal=record.exhaustion_signal)


def assess_reopen(record: ResearchLoopRecord, evidence: ReopenEvidence) -> GovernorDecision:
    if record.state is not LoopState.GRAVEYARD:
        raise ValueError("reopen evidence applies only to Graveyard research")
    return GovernorDecision(GovernorAction.REOPEN, record.state, LoopState.REOPEN_ELIGIBLE, f"material reopen evidence: {evidence.change_kind.value}", record.active_execution_fingerprint, exhaustion_signal=record.exhaustion_signal)


@dataclass(frozen=True, slots=True)
class LoopEvent:
    sequence: int
    loop_fingerprint: str
    from_state: LoopState
    to_state: LoopState
    action: GovernorAction
    event_at: datetime
    event_fingerprint: str
    details: dict[str, object]


class SQLiteResearchLoopStore:
    """Narrow M160 control ledger; jobs stay in M155 and artifacts stay in M164."""

    def __init__(self, path: str | Path = ":memory:", *, busy_timeout_ms: int = 5000) -> None:
        if not 1 <= busy_timeout_ms <= 60000:
            raise ValueError("busy_timeout_ms must be between 1 and 60000")
        self._db = sqlite3.connect(str(path), timeout=busy_timeout_ms / 1000.0, isolation_level=None)
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        self._db.execute("CREATE TABLE IF NOT EXISTS research_loops(loop_fingerprint TEXT PRIMARY KEY,root_manifest_fingerprint TEXT NOT NULL,active_manifest_fingerprint TEXT NOT NULL,root_execution_fingerprint TEXT NOT NULL,active_execution_fingerprint TEXT NOT NULL,active_subject_fingerprint TEXT NOT NULL,family_fingerprint TEXT NOT NULL,stage TEXT NOT NULL,state TEXT NOT NULL,iteration INTEGER NOT NULL,evidence_json TEXT NOT NULL,last_outcome_fingerprint TEXT,exhaustion_signal TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)")
        self._db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_research_loop_root_manifest ON research_loops(root_manifest_fingerprint)")
        self._db.execute("CREATE TABLE IF NOT EXISTS research_loop_events(seq INTEGER PRIMARY KEY AUTOINCREMENT,loop_fingerprint TEXT NOT NULL,from_state TEXT NOT NULL,to_state TEXT NOT NULL,action TEXT NOT NULL,event_at TEXT NOT NULL,event_fingerprint TEXT NOT NULL,details TEXT NOT NULL,FOREIGN KEY(loop_fingerprint) REFERENCES research_loops(loop_fingerprint))")

    @property
    def broker_write_authorized(self) -> bool:
        return False

    @property
    def promotion_authorized(self) -> bool:
        return False

    @contextmanager
    def _write(self) -> Iterator[None]:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
        except Exception:
            self._db.execute("ROLLBACK")
            raise
        else:
            self._db.execute("COMMIT")

    @staticmethod
    def _loop_fingerprint(manifest: str, execution: str, subject: str, family: str, stage: EvaluationStage) -> str:
        return _digest(("dusty-m160-loop-v2", manifest, execution, subject, family, stage.value))

    def _event(self, loop: str, from_state: LoopState, to_state: LoopState, action: GovernorAction, at: datetime, details: dict[str, object]) -> None:
        rendered = _canonical(details)
        event_fp = _digest({"loop": loop, "from": from_state.value, "to": to_state.value, "action": action.value, "event_at": at.isoformat(), "details": json.loads(rendered)})
        self._db.execute("INSERT INTO research_loop_events(loop_fingerprint,from_state,to_state,action,event_at,event_fingerprint,details) VALUES(?,?,?,?,?,?,?)", (loop, from_state.value, to_state.value, action.value, at.isoformat(), event_fp, rendered))

    def register(self, *, manifest_fingerprint: str, execution_fingerprint: str, subject_fingerprint: str, family_fingerprint: str, stage: EvaluationStage, now: datetime) -> ResearchLoopRecord:
        manifest = _sha(manifest_fingerprint, "loop manifest")
        execution = _sha(execution_fingerprint, "loop execution")
        subject = _sha(subject_fingerprint, "loop subject")
        family = _sha(family_fingerprint, "loop family")
        now_utc = _aware(now, "loop registration time")
        loop_fp = self._loop_fingerprint(manifest, execution, subject, family, stage)
        with self._write():
            existing = self._db.execute("SELECT loop_fingerprint FROM research_loops WHERE root_manifest_fingerprint=?", (manifest,)).fetchone()
            if existing is not None:
                if existing[0] != loop_fp:
                    raise RuntimeError("root manifest is already bound to a different research loop")
                row = self.snapshot(loop_fp)
                assert row is not None
                return row
            self._db.execute("INSERT INTO research_loops(loop_fingerprint,root_manifest_fingerprint,active_manifest_fingerprint,root_execution_fingerprint,active_execution_fingerprint,active_subject_fingerprint,family_fingerprint,stage,state,iteration,evidence_json,last_outcome_fingerprint,exhaustion_signal,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (loop_fp, manifest, manifest, execution, execution, subject, family, stage.value, LoopState.PROPOSED.value, 0, "[]", None, ExhaustionSignal.NONE.value, now_utc.isoformat(), now_utc.isoformat()))
            self._event(loop_fp, LoopState.PROPOSED, LoopState.PROPOSED, GovernorAction.HOLD, now_utc, {"event": "REGISTERED"})
        row = self.snapshot(loop_fp)
        assert row is not None
        return row

    def snapshot(self, loop_fingerprint: str) -> ResearchLoopRecord | None:
        loop_fp = _sha(loop_fingerprint, "loop lookup")
        row = self._db.execute("SELECT root_manifest_fingerprint,active_manifest_fingerprint,root_execution_fingerprint,active_execution_fingerprint,active_subject_fingerprint,family_fingerprint,stage,state,iteration,evidence_json,last_outcome_fingerprint,exhaustion_signal,created_at,updated_at FROM research_loops WHERE loop_fingerprint=?", (loop_fp,)).fetchone()
        if row is None:
            return None
        root_manifest, active_manifest, root_execution, active_execution, active_subject, family, stage, state, iteration, evidence, outcome, exhaustion, created, updated = row
        return ResearchLoopRecord(loop_fp, root_manifest, active_manifest, root_execution, active_execution, active_subject, family, EvaluationStage(stage), LoopState(state), int(iteration), tuple(json.loads(evidence)), outcome, ExhaustionSignal(exhaustion), datetime.fromisoformat(created), datetime.fromisoformat(updated))

    def apply(self, loop_fingerprint: str, decision: GovernorDecision, *, now: datetime, evidence_fingerprints: Iterable[str] = ()) -> ResearchLoopRecord:
        loop_fp = _sha(loop_fingerprint, "loop transition")
        now_utc = _aware(now, "loop transition time")
        extra = tuple(sorted({_sha(value, "transition evidence") for value in evidence_fingerprints}))
        with self._write():
            current = self.snapshot(loop_fp)
            if current is None:
                raise KeyError("research loop not found")
            if current.state is not decision.from_state:
                raise RuntimeError("stale research-loop transition")
            if current.active_execution_fingerprint != decision.active_execution_fingerprint:
                raise RuntimeError("governor decision execution drift")
            evidence = tuple(sorted(set(current.evidence_fingerprints) | set(extra)))
            outcome = decision.outcome_fingerprint or current.last_outcome_fingerprint
            next_iteration = current.iteration + (1 if decision.action is GovernorAction.ADMIT else 0)
            self._db.execute("UPDATE research_loops SET state=?,iteration=?,evidence_json=?,last_outcome_fingerprint=?,exhaustion_signal=?,updated_at=? WHERE loop_fingerprint=? AND state=? AND active_execution_fingerprint=?", (decision.to_state.value, next_iteration, _canonical(evidence), outcome, decision.exhaustion_signal.value, now_utc.isoformat(), loop_fp, current.state.value, current.active_execution_fingerprint))
            if self._db.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("research-loop compare-and-swap transition failed")
            self._event(loop_fp, decision.from_state, decision.to_state, decision.action, now_utc, {"reason": decision.reason, "outcome": decision.outcome_fingerprint, "challengers": decision.challenger_execution_fingerprints, "exhaustion": decision.exhaustion_signal.value, "evidence": extra})
        row = self.snapshot(loop_fp)
        assert row is not None
        return row

    def admit(self, loop_fingerprint: str, *, now: datetime) -> ResearchLoopRecord:
        current = self.snapshot(loop_fingerprint)
        if current is None:
            raise KeyError("research loop not found")
        if current.state not in _ADMISSION_STATES:
            raise ValueError("research loop is not admission eligible")
        target = LoopState.TESTING if current.state is LoopState.PROPOSED else LoopState.RETESTING
        decision = GovernorDecision(GovernorAction.ADMIT, current.state, target, "governor admitted ranked research candidate", current.active_execution_fingerprint)
        return self.apply(loop_fingerprint, decision, now=now)

    def register_challenger(self, loop_fingerprint: str, *, manifest_fingerprint: str, execution_fingerprint: str, subject_fingerprint: str, now: datetime) -> ResearchLoopRecord:
        loop_fp = _sha(loop_fingerprint, "Challenger loop")
        manifest = _sha(manifest_fingerprint, "Challenger manifest")
        execution = _sha(execution_fingerprint, "Challenger execution")
        subject = _sha(subject_fingerprint, "Challenger subject")
        now_utc = _aware(now, "Challenger registration time")
        with self._write():
            current = self.snapshot(loop_fp)
            if current is None:
                raise KeyError("research loop not found")
            if current.state is not LoopState.FAILED_RESEARCHABLE:
                raise ValueError("Challenger registration requires failed-researchable state")
            if execution == current.active_execution_fingerprint or manifest == current.active_manifest_fingerprint or subject == current.active_subject_fingerprint:
                raise ValueError("Challenger must carry new manifest, execution, and subject identities")
            self._db.execute("UPDATE research_loops SET active_manifest_fingerprint=?,active_execution_fingerprint=?,active_subject_fingerprint=?,state=?,updated_at=? WHERE loop_fingerprint=? AND state=? AND active_execution_fingerprint=?", (manifest, execution, subject, LoopState.CHALLENGER_CREATED.value, now_utc.isoformat(), loop_fp, current.state.value, current.active_execution_fingerprint))
            if self._db.execute("SELECT changes()").fetchone()[0] != 1:
                raise RuntimeError("stale Challenger registration")
            self._event(loop_fp, current.state, LoopState.CHALLENGER_CREATED, GovernorAction.REGISTER_CHALLENGER, now_utc, {"prior_manifest": current.active_manifest_fingerprint, "prior_execution": current.active_execution_fingerprint, "challenger_manifest": manifest, "challenger_execution": execution, "challenger_subject": subject})
        row = self.snapshot(loop_fp)
        assert row is not None
        return row

    def history(self, loop_fingerprint: str) -> tuple[LoopEvent, ...]:
        loop_fp = _sha(loop_fingerprint, "loop history")
        rows = self._db.execute("SELECT seq,from_state,to_state,action,event_at,event_fingerprint,details FROM research_loop_events WHERE loop_fingerprint=? ORDER BY seq", (loop_fp,)).fetchall()
        return tuple(LoopEvent(int(seq), loop_fp, LoopState(a), LoopState(b), GovernorAction(action), datetime.fromisoformat(at), event_fp, json.loads(details)) for seq, a, b, action, at, event_fp, details in rows)

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def checkpoint_wal(self) -> tuple[int, int, int]:
        row = self._db.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return int(row[0]), int(row[1]), int(row[2])

    def close(self) -> None:
        self._db.close()
