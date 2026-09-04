from __future__ import annotations

"""M136-M154 integration primitives for Dusty's autonomous research organism.

This module deliberately composes existing certified contracts. It owns no
broker-write surface and no LLM can grant trade, risk, or promotion authority.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from statistics import fmean
from typing import Callable, Iterable, Mapping, Sequence

from .features import FeatureBar, completed_feature_bars_from_mt5
from .forecast_research import (
    ForecastDisagreement,
    PITForecastContext,
    ProviderOutcomeCase,
    ProviderReliability,
    build_pit_context,
    score_provider_cases,
)
from .market_clock import BrokerMarketSchedule, SessionKind
from .mt5worker import MT5Bar, MT5BarRequest, ReadOnlyMT5Worker
from .provider_forecast_adapter import ForecastEvidence
from .research_brain import ResearchMandate, ResearchMetrics, ResearchSchool, SchoolDecision, evaluate_school
from .research_runtime import (
    BlackboardItem,
    BlackboardKind,
    CycleCheckpoint,
    ResearchBlackboard,
    ResearchStage,
    SQLiteResearchCycleStore,
    heartbeat,
    next_stage,
)


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _sha(value: str, label: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value.lower()):
        raise ValueError(f"{label} requires SHA-256 identity")


# M136 -----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BrokerSymbolBinding:
    requested_symbol: str
    native_symbol: str

    def __post_init__(self) -> None:
        if not self.requested_symbol.strip() or not self.native_symbol.strip():
            raise ValueError("symbol binding requires requested and native symbols")


@dataclass(frozen=True, slots=True)
class ResearchBarBatch:
    symbol: str
    native_symbol: str
    timeframe: str
    start: datetime
    end: datetime
    terminal_path_sha256: str
    raw_bars: tuple[MT5Bar, ...]
    completed_bars: tuple[FeatureBar, ...]
    broker_write_authority: bool = False

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.native_symbol.strip() or not self.timeframe.strip():
            raise ValueError("research bar batch identity required")
        _aware(self.start, "research batch start")
        _aware(self.end, "research batch end")
        _sha(self.terminal_path_sha256, "terminal path")
        if self.end <= self.start or len(self.raw_bars) < 2 or not self.completed_bars:
            raise ValueError("research bar batch requires a usable historical interval")
        if tuple(sorted(row.at for row in self.raw_bars)) != tuple(row.at for row in self.raw_bars):
            raise ValueError("research raw bars must be chronological")
        if len({row.at for row in self.raw_bars}) != len(self.raw_bars):
            raise ValueError("research raw bars must be unique")
        if self.broker_write_authority:
            raise ValueError("research data service cannot receive broker authority")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "symbol": self.symbol.upper(),
                "native_symbol": self.native_symbol,
                "timeframe": self.timeframe.upper(),
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "terminal": self.terminal_path_sha256,
                "raw": tuple(
                    (
                        row.at.isoformat(), row.open, row.high, row.low, row.close,
                        row.tick_volume, row.spread, row.real_volume,
                    )
                    for row in self.raw_bars
                ),
                "broker_write_authority": self.broker_write_authority,
            }
        )


class MT5ResearchDataService:
    """Read-only history adapter with explicit broker-symbol binding."""

    def __init__(self, worker: ReadOnlyMT5Worker | None = None) -> None:
        self.worker = worker or ReadOnlyMT5Worker()

    @property
    def broker_write_authorized(self) -> bool:
        return False

    def load(self, request: MT5BarRequest, *, binding: BrokerSymbolBinding | None = None) -> ResearchBarBatch:
        native = request.symbol
        requested = request.symbol
        if binding is not None:
            if request.symbol.upper() not in {binding.requested_symbol.upper(), binding.native_symbol.upper()}:
                raise ValueError("symbol binding does not match request")
            requested = binding.requested_symbol
            native = binding.native_symbol
        native_request = MT5BarRequest(
            terminal_path=request.terminal_path,
            symbol=native,
            timeframe=request.timeframe,
            start=request.start,
            end=request.end,
            chunk_days=request.chunk_days,
        )
        raw = tuple(self.worker.stream_bars(native_request))
        completed = completed_feature_bars_from_mt5(raw)
        return ResearchBarBatch(
            symbol=requested.upper(),
            native_symbol=native,
            timeframe=request.timeframe.upper(),
            start=request.start.astimezone(timezone.utc),
            end=request.end.astimezone(timezone.utc),
            terminal_path_sha256=sha256(request.terminal_path.encode("utf-8")).hexdigest(),
            raw_bars=raw,
            completed_bars=completed,
        )


# M137 -----------------------------------------------------------------------

_TIMEFRAME_MINUTES: Mapping[str, int] = {
    "M5": 5,
    "M15": 15,
    "M30": 30,
    "H1": 60,
    "H4": 240,
}


@dataclass(frozen=True, slots=True)
class SessionForecastHorizon:
    as_of: datetime
    timeframe: str
    horizon_steps: int
    future_times: tuple[datetime, ...]
    schedule_fingerprint: str
    skill_certification_eligible: bool = True

    def __post_init__(self) -> None:
        _aware(self.as_of, "forecast horizon as_of")
        _sha(self.schedule_fingerprint, "schedule")
        if self.timeframe.upper() not in _TIMEFRAME_MINUTES or self.horizon_steps < 1:
            raise ValueError("forecast horizon timeframe/steps invalid")
        if len(self.future_times) != self.horizon_steps:
            raise ValueError("forecast horizon must contain every requested step")
        if any(value <= self.as_of for value in self.future_times):
            raise ValueError("forecast horizon cannot contain past timestamps")
        if any(a >= b for a, b in zip(self.future_times, self.future_times[1:])):
            raise ValueError("forecast horizon must be strictly chronological")


def _in_trade_session(schedule: BrokerMarketSchedule, value: datetime) -> bool:
    server_tz = timezone(timedelta(seconds=schedule.server_utc_offset_seconds))
    local = value.astimezone(server_tz)
    closed = set(schedule.closed_dates)
    sessions = tuple(row for row in schedule.sessions if row.kind is SessionKind.TRADE)
    for base_date in (local.date() - timedelta(days=1), local.date()):
        if base_date in closed:
            continue
        midnight = datetime.combine(base_date, datetime.min.time(), tzinfo=server_tz)
        for row in sessions:
            if row.weekday != base_date.weekday():
                continue
            start = midnight + timedelta(seconds=row.start_second)
            if row.end_second > row.start_second:
                end = midnight + timedelta(seconds=row.end_second)
            else:
                end = midnight + timedelta(days=1, seconds=row.end_second)
            if start <= local < end:
                return True
    return False


def build_session_forecast_horizon(
    schedule: BrokerMarketSchedule,
    *,
    as_of: datetime,
    timeframe: str,
    horizon_steps: int,
    maximum_calendar_days: int = 14,
) -> SessionForecastHorizon:
    """Build future observation timestamps using only a schedule known at T."""

    _aware(as_of, "forecast horizon as_of")
    tf = timeframe.strip().upper()
    minutes = _TIMEFRAME_MINUTES.get(tf)
    if minutes is None or horizon_steps < 1 or maximum_calendar_days < 1:
        raise ValueError("unsupported research timeframe/horizon")
    if schedule.captured_at > as_of:
        raise ValueError("future broker schedule leaked into historical horizon")
    step = timedelta(minutes=minutes)
    candidate = as_of + step
    deadline = as_of + timedelta(days=maximum_calendar_days)
    result: list[datetime] = []
    while candidate <= deadline and len(result) < horizon_steps:
        if _in_trade_session(schedule, candidate):
            result.append(candidate)
        candidate += step
    if len(result) != horizon_steps:
        raise ValueError("broker schedule cannot supply requested forecast horizon")
    return SessionForecastHorizon(
        as_of=as_of,
        timeframe=tf,
        horizon_steps=horizon_steps,
        future_times=tuple(result),
        schedule_fingerprint=schedule.fingerprint,
    )


# M138-M139 ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class PITCampaignPoint:
    ordinal: int
    context: PITForecastContext
    target_at: datetime
    horizon_steps: int

    def __post_init__(self) -> None:
        if self.ordinal < 0 or self.horizon_steps < 1:
            raise ValueError("campaign point ordinal/horizon invalid")
        _aware(self.target_at, "campaign target")
        if self.target_at <= self.context.as_of:
            raise ValueError("campaign target must follow context")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "ordinal": self.ordinal,
                "context": self.context.context_hash,
                "target_at": self.target_at.isoformat(),
                "horizon_steps": self.horizon_steps,
            }
        )


def build_pit_campaign(
    bars: Sequence[FeatureBar],
    *,
    symbol: str,
    timeframe: str,
    horizon_steps: int,
    min_context_observations: int = 64,
    stride: int = 1,
) -> tuple[PITCampaignPoint, ...]:
    """Replay actual completed-bar timestamps; no synthetic weekend cadence."""

    rows = tuple(bars)
    if min_context_observations < 1 or horizon_steps < 1 or stride < 1:
        raise ValueError("campaign bounds must be positive")
    if any(a.at >= b.at for a, b in zip(rows, rows[1:])):
        raise ValueError("campaign bars must be chronological and unique")
    result: list[PITCampaignPoint] = []
    first = min_context_observations - 1
    final_exclusive = len(rows) - horizon_steps
    for ordinal, index in enumerate(range(first, final_exclusive, stride)):
        context = build_pit_context(
            rows[: index + 1],
            symbol=symbol,
            timeframe=timeframe,
            as_of=rows[index].at,
            max_observations=max(min_context_observations, 2048),
        )
        result.append(PITCampaignPoint(ordinal, context, rows[index + horizon_steps].at, horizon_steps))
    return tuple(result)


def realize_campaign_forecast(
    evidence: ForecastEvidence,
    point: PITCampaignPoint,
    bars: Sequence[FeatureBar],
    *,
    regime: str = "unclassified",
    session: str = "unclassified",
) -> ProviderOutcomeCase:
    if (
        evidence.symbol.upper() != point.context.symbol.upper()
        or evidence.timeframe.upper() != point.context.timeframe.upper()
        or evidence.as_of != point.context.as_of
        or evidence.horizon_steps != point.horizon_steps
        or evidence.context_sha256 != point.context.context_hash
    ):
        raise ValueError("forecast evidence does not bind to campaign point")
    matches = tuple(row for row in bars if row.at == point.target_at)
    if len(matches) != 1:
        raise ValueError("campaign realization requires exact target observation")
    return ProviderOutcomeCase(evidence, matches[0].close, point.target_at, regime, session)


# M140 -----------------------------------------------------------------------

class SQLiteProviderSkillStore:
    """Append-only provider score snapshots; never a model-selection authority."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS provider_skill("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, captured_at TEXT NOT NULL,"
            "fingerprint TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        self._db.commit()

    def append_cases(self, cases: Iterable[ProviderOutcomeCase], *, captured_at: datetime) -> tuple[ProviderReliability, ...]:
        _aware(captured_at, "provider skill capture")
        rows = score_provider_cases(cases)
        with self._db:
            for row in rows:
                payload = _canonical(
                    {
                        "provider_id": row.provider_id,
                        "model_id": row.model_id,
                        "model_revision": row.model_revision,
                        "symbol": row.symbol,
                        "timeframe": row.timeframe,
                        "horizon_steps": row.horizon_steps,
                        "regime": row.regime,
                        "session": row.session,
                        "count": row.count,
                        "mae": row.mae,
                        "directional_accuracy": row.directional_accuracy,
                        "interval_coverage": row.interval_coverage,
                        "mean_interval_width_fraction": row.mean_interval_width_fraction,
                        "bias_fraction": row.bias_fraction,
                    }
                )
                self._db.execute(
                    "INSERT INTO provider_skill(captured_at,fingerprint,payload) VALUES(?,?,?)",
                    (captured_at.isoformat(), _digest(json.loads(payload)), payload),
                )
        return rows

    def history(self) -> tuple[ProviderReliability, ...]:
        values = []
        for (payload,) in self._db.execute("SELECT payload FROM provider_skill ORDER BY seq"):
            values.append(ProviderReliability(**json.loads(payload)))
        return tuple(values)

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()


# M141-M142 ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DisagreementOutcomeCase:
    disagreement: ForecastDisagreement
    symbol: str
    timeframe: str
    horizon_steps: int
    regime: str
    session: str
    realized_change_fraction: float

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip() or not self.regime.strip() or not self.session.strip():
            raise ValueError("disagreement case identity required")
        if self.horizon_steps < 1 or not math.isfinite(self.realized_change_fraction):
            raise ValueError("disagreement case outcome invalid")
        if self.disagreement.decision_authority:
            raise ValueError("disagreement outcome cannot gain trade authority")


@dataclass(frozen=True, slots=True)
class DisagreementReliability:
    state: str
    symbol: str
    timeframe: str
    horizon_steps: int
    regime: str
    session: str
    count: int
    up_rate: float
    down_rate: float
    mean_abs_move_fraction: float


def score_disagreement_cases(cases: Iterable[DisagreementOutcomeCase]) -> tuple[DisagreementReliability, ...]:
    groups: dict[tuple[str, str, str, int, str, str], list[DisagreementOutcomeCase]] = {}
    for case in cases:
        key = (
            case.disagreement.state.value,
            case.symbol.upper(),
            case.timeframe.upper(),
            case.horizon_steps,
            case.regime,
            case.session,
        )
        groups.setdefault(key, []).append(case)
    results = []
    for key, rows in sorted(groups.items()):
        state, symbol, timeframe, horizon, regime, session = key
        results.append(
            DisagreementReliability(
                state,
                symbol,
                timeframe,
                horizon,
                regime,
                session,
                len(rows),
                fmean(float(row.realized_change_fraction > 0) for row in rows),
                fmean(float(row.realized_change_fraction < 0) for row in rows),
                fmean(abs(row.realized_change_fraction) for row in rows),
            )
        )
    return tuple(results)


@dataclass(frozen=True, slots=True)
class DeterministicQuantScorecard:
    as_of: datetime
    provider_skill: tuple[ProviderReliability, ...]
    disagreement_skill: tuple[DisagreementReliability, ...]
    source_fingerprints: tuple[str, ...]
    decision_authority: bool = False

    def __post_init__(self) -> None:
        _aware(self.as_of, "quant scorecard time")
        if any(len(value) != 64 for value in self.source_fingerprints):
            raise ValueError("quant scorecard sources require SHA-256 identity")
        if self.decision_authority:
            raise ValueError("quant scorecard is evidence, not trade authority")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "as_of": self.as_of.isoformat(),
                "provider_skill": tuple(
                    (
                        row.provider_id, row.model_id, row.model_revision, row.symbol, row.timeframe,
                        row.horizon_steps, row.regime, row.session, row.count, row.mae,
                        row.directional_accuracy, row.interval_coverage,
                        row.mean_interval_width_fraction, row.bias_fraction,
                    )
                    for row in self.provider_skill
                ),
                "disagreement_skill": tuple(
                    (
                        row.state, row.symbol, row.timeframe, row.horizon_steps, row.regime,
                        row.session, row.count, row.up_rate, row.down_rate, row.mean_abs_move_fraction,
                    )
                    for row in self.disagreement_skill
                ),
                "sources": self.source_fingerprints,
                "decision_authority": self.decision_authority,
            }
        )

    def render(self) -> str:
        payload = {
            "protocol": "dusty-deterministic-quant-scorecard-v1",
            "as_of": self.as_of.isoformat(),
            "provider_skill": [
                {
                    "provider": row.provider_id,
                    "model": row.model_id,
                    "symbol": row.symbol,
                    "timeframe": row.timeframe,
                    "horizon": row.horizon_steps,
                    "regime": row.regime,
                    "session": row.session,
                    "n": row.count,
                    "mae": row.mae,
                    "directional_accuracy": row.directional_accuracy,
                    "interval_coverage": row.interval_coverage,
                    "bias_fraction": row.bias_fraction,
                }
                for row in self.provider_skill
            ],
            "disagreement_skill": [
                {
                    "state": row.state,
                    "symbol": row.symbol,
                    "timeframe": row.timeframe,
                    "horizon": row.horizon_steps,
                    "regime": row.regime,
                    "session": row.session,
                    "n": row.count,
                    "up_rate": row.up_rate,
                    "down_rate": row.down_rate,
                    "mean_abs_move_fraction": row.mean_abs_move_fraction,
                }
                for row in self.disagreement_skill
            ],
            "authority": "research_only",
        }
        return _canonical(payload)


def build_quant_scorecard(
    provider_cases: Iterable[ProviderOutcomeCase],
    disagreement_cases: Iterable[DisagreementOutcomeCase],
    *,
    as_of: datetime,
    source_fingerprints: Iterable[str],
) -> DeterministicQuantScorecard:
    return DeterministicQuantScorecard(
        as_of,
        score_provider_cases(provider_cases),
        score_disagreement_cases(disagreement_cases),
        tuple(sorted(set(source_fingerprints))),
    )


# M149-M151 ------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ResearchFunnelResult:
    a1: SchoolDecision
    a2: SchoolDecision
    a3: SchoolDecision

    @property
    def deepest_passed_school(self) -> ResearchSchool | None:
        if self.a3.passed:
            return ResearchSchool.A3_VELOCITY
        if self.a2.passed:
            return ResearchSchool.A2_PROFITABILITY
        if self.a1.passed:
            return ResearchSchool.A1_EDGE
        return None


def run_research_funnel(
    metrics: ResearchMetrics,
    mandate: ResearchMandate = ResearchMandate(),
) -> ResearchFunnelResult:
    return ResearchFunnelResult(
        evaluate_school(ResearchSchool.A1_EDGE, metrics, mandate),
        evaluate_school(ResearchSchool.A2_PROFITABILITY, metrics, mandate),
        evaluate_school(ResearchSchool.A3_VELOCITY, metrics, mandate),
    )


@dataclass(frozen=True, slots=True)
class ProfitVelocityObservation:
    realized_favorable_movement: float
    maximum_favorable_excursion: float
    giveback_movement: float

    def __post_init__(self) -> None:
        values = (
            self.realized_favorable_movement,
            self.maximum_favorable_excursion,
            self.giveback_movement,
        )
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("profit velocity observation must be finite and nonnegative")
        if self.realized_favorable_movement > self.maximum_favorable_excursion:
            raise ValueError("realized movement cannot exceed MFE")
        if self.giveback_movement > self.maximum_favorable_excursion:
            raise ValueError("giveback cannot exceed MFE")

    @property
    def capture_efficiency(self) -> float:
        if self.maximum_favorable_excursion == 0:
            return 0.0
        return self.realized_favorable_movement / self.maximum_favorable_excursion


@dataclass(frozen=True, slots=True)
class ProfitVelocitySummary:
    sample_count: int
    mean_capture_efficiency: float
    mean_giveback_fraction: float


def summarize_profit_velocity(rows: Iterable[ProfitVelocityObservation]) -> ProfitVelocitySummary:
    values = tuple(rows)
    if not values:
        return ProfitVelocitySummary(0, 0.0, 0.0)
    return ProfitVelocitySummary(
        len(values),
        fmean(row.capture_efficiency for row in values),
        fmean(
            0.0 if row.maximum_favorable_excursion == 0 else row.giveback_movement / row.maximum_favorable_excursion
            for row in values
        ),
    )


# M154 -----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class StageWork:
    items: tuple[BlackboardItem, ...] = ()
    completed_job_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if any(len(value) != 64 for value in self.completed_job_fingerprints):
            raise ValueError("stage work jobs require SHA-256 identity")


class SQLiteResearchOrganismStore:
    """Companion payload store plus the existing append-only checkpoint store."""

    def __init__(self, path: str | Path) -> None:
        if str(path) == ":memory:":
            raise ValueError("organism store requires a durable filesystem path")
        self.path = Path(path)
        self.cycle_store = SQLiteResearchCycleStore(self.path)
        self._db = sqlite3.connect(str(self.path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS research_boards("
            "fingerprint TEXT PRIMARY KEY, cycle_id TEXT NOT NULL, as_of TEXT NOT NULL, payload TEXT NOT NULL)"
        )
        self._db.commit()

    def save_board(self, board: ResearchBlackboard) -> None:
        payload = _canonical(
            {
                "cycle_id": board.cycle_id,
                "as_of": board.as_of.isoformat(),
                "items": [
                    {
                        "kind": item.kind.value,
                        "identity": item.identity,
                        "payload_sha256": item.payload_sha256,
                        "parents": item.parents,
                    }
                    for item in board.items
                ],
                "live_write_authorized": board.live_write_authorized,
            }
        )
        with self._db:
            self._db.execute(
                "INSERT OR IGNORE INTO research_boards(fingerprint,cycle_id,as_of,payload) VALUES(?,?,?,?)",
                (board.fingerprint, board.cycle_id, board.as_of.isoformat(), payload),
            )

    def load_board(self, fingerprint: str) -> ResearchBlackboard:
        _sha(fingerprint, "board")
        row = self._db.execute(
            "SELECT payload FROM research_boards WHERE fingerprint=?",
            (fingerprint,),
        ).fetchone()
        if row is None:
            raise LookupError("research board payload unavailable for checkpoint")
        raw = json.loads(row[0])
        board = ResearchBlackboard(
            cycle_id=raw["cycle_id"],
            as_of=datetime.fromisoformat(raw["as_of"]),
            items=tuple(
                BlackboardItem(
                    BlackboardKind(item["kind"]),
                    item["identity"],
                    item["payload_sha256"],
                    tuple(item["parents"]),
                )
                for item in raw["items"]
            ),
            live_write_authorized=bool(raw["live_write_authorized"]),
        )
        if board.fingerprint != fingerprint:
            raise ValueError("persisted blackboard fingerprint mismatch")
        return board

    def integrity_ok(self) -> bool:
        return self.cycle_store.integrity_ok() and self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()
        self.cycle_store.close()


@dataclass(frozen=True, slots=True)
class ResearchOrganismResult:
    board: ResearchBlackboard
    checkpoint: CycleCheckpoint
    stages_completed: tuple[ResearchStage, ...]
    broker_write_authority: bool = False
    promotion_authority: bool = False

    def __post_init__(self) -> None:
        if self.broker_write_authority or self.promotion_authority:
            raise ValueError("research organism cannot receive operational authority")


StageHandler = Callable[[ResearchBlackboard], StageWork]


class ResearchOrganism:
    """One authoritative, restartable research cycle over pure stage handlers."""

    def __init__(
        self,
        store: SQLiteResearchOrganismStore,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.store = store
        self.clock = clock

    @property
    def broker_write_authorized(self) -> bool:
        return False

    def run_until_complete(
        self,
        initial_board: ResearchBlackboard,
        handlers: Mapping[ResearchStage, StageHandler],
        *,
        maximum_stage_advances: int = 32,
    ) -> ResearchOrganismResult:
        if maximum_stage_advances < 1:
            raise ValueError("maximum stage advances must be positive")
        latest = self.store.cycle_store.latest(initial_board.cycle_id)
        if latest is None:
            board = initial_board
        else:
            board = self.store.load_board(latest.blackboard_fingerprint)
            if board.cycle_id != initial_board.cycle_id:
                raise ValueError("resumed board cycle mismatch")
            if latest.stage is ResearchStage.COMPLETE:
                return ResearchOrganismResult(board, latest, ())

        completed: list[ResearchStage] = []
        checkpoint = latest
        for _ in range(maximum_stage_advances):
            stage = ResearchStage.ACQUIRE if checkpoint is None else next_stage(checkpoint.stage)
            work = StageWork()
            if stage not in (ResearchStage.CHECKPOINT, ResearchStage.COMPLETE):
                handler = handlers.get(stage)
                if handler is None:
                    raise KeyError(f"research organism handler missing for {stage.name}")
                work = handler(board)
                if not isinstance(work, StageWork):
                    raise TypeError("research stage handler must return StageWork")
                for item in work.items:
                    board = board.add(item)

            self.store.save_board(board)
            beat = heartbeat(
                self.store.cycle_store,
                board,
                now=self.clock(),
                completed_job_fingerprints=work.completed_job_fingerprints,
            )
            checkpoint = beat.checkpoint
            completed.append(checkpoint.stage)
            if checkpoint.stage is ResearchStage.COMPLETE:
                return ResearchOrganismResult(board, checkpoint, tuple(completed))
        raise RuntimeError("research organism exceeded bounded stage advances")
