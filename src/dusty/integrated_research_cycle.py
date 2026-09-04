from __future__ import annotations

"""M135 integrated research cycle.

This module assembles existing read-only MT5 history, point-in-time feature
construction, three isolated forecast contractors, disagreement evidence and
the durable research blackboard. It creates research evidence only and has no
broker-write, entry-veto, sizing, promotion or live-trading authority.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from typing import Iterable, Protocol, Sequence

from .features import FeatureBar, completed_feature_bars_from_mt5
from .forecast_research import (
    DisagreementState,
    ForecastDisagreement,
    PITForecastContext,
    build_pit_context,
)
from .mt5worker import MT5Bar
from .provider_forecast_adapter import MIN_CONTEXT_OBSERVATIONS, ForecastEvidence
from .provider_multi_contract import ContractorForecastResult
from .provider_multi_service import ForecastSelectionMode
from .provider_process import ProviderWorkerState
from .research_runtime import (
    BlackboardItem,
    BlackboardKind,
    CycleCheckpoint,
    ResearchBlackboard,
    ResearchStage,
    SQLiteResearchCycleStore,
    make_checkpoint,
)

_EXPECTED_PROVIDERS = ("chronos2", "kronos-small", "timesfm-2.5")


def _digest(payload: object) -> str:
    return sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=str,
        ).encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class IntegratedResearchCycleConfig:
    symbol: str = "EURUSD"
    timeframe: str = "M15"
    context_observations: int = 256
    horizon_steps: int = 4
    require_all_three: bool = True
    broker_write_authority: bool = False
    entry_veto_authority: bool = False
    promotion_authority: bool = False
    risk_override_authority: bool = False

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip():
            raise ValueError("integrated_cycle_market_identity_required")
        if self.context_observations < MIN_CONTEXT_OBSERVATIONS:
            raise ValueError(
                f"integrated_cycle_requires_at_least_{MIN_CONTEXT_OBSERVATIONS}_context_observations"
            )
        if not 1 <= self.horizon_steps <= 64:
            raise ValueError("integrated_cycle_horizon_out_of_bounds")
        if (
            self.broker_write_authority
            or self.entry_veto_authority
            or self.promotion_authority
            or self.risk_override_authority
        ):
            raise ValueError("integrated_research_cycle_cannot_receive_operational_authority")


class ForecastManager(Protocol):
    def select(self, mode: ForecastSelectionMode | str) -> ForecastSelectionMode: ...

    def start_selected(self) -> dict[str, ProviderWorkerState]: ...

    def forecast_selected(
        self,
        bars: Sequence[FeatureBar],
        *,
        symbol: str,
        timeframe: str,
        horizon_steps: int,
        future_times: Sequence[datetime] | None = None,
    ) -> tuple[ContractorForecastResult, ...]: ...

    def stop_all(self) -> dict[str, ProviderWorkerState]: ...


@dataclass(frozen=True, slots=True)
class IntegratedResearchCycleResult:
    cycle_id: str
    config: IntegratedResearchCycleConfig
    pit_context: PITForecastContext
    future_times: tuple[datetime, ...]
    forecast_results: tuple[ContractorForecastResult, ...]
    disagreement: ForecastDisagreement
    blackboard: ResearchBlackboard
    checkpoint: CycleCheckpoint
    future_schedule_basis: str = "nominal_timeframe_cadence"
    skill_certification_eligible: bool = False
    broker_write_authority: bool = False
    entry_veto_authority: bool = False
    promotion_authority: bool = False
    risk_override_authority: bool = False

    def __post_init__(self) -> None:
        if not self.cycle_id.strip():
            raise ValueError("integrated_cycle_id_required")
        if len(self.future_times) != self.config.horizon_steps:
            raise ValueError("integrated_cycle_future_schedule_length_mismatch")
        if self.skill_certification_eligible:
            raise ValueError("m135_integration_cycle_is_not_forecast_skill_certification")
        if (
            self.broker_write_authority
            or self.entry_veto_authority
            or self.promotion_authority
            or self.risk_override_authority
        ):
            raise ValueError("integrated_cycle_result_cannot_receive_operational_authority")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "cycle_id": self.cycle_id,
                "symbol": self.config.symbol.upper(),
                "timeframe": self.config.timeframe.upper(),
                "pit_context": self.pit_context.context_hash,
                "future_times": tuple(value.isoformat() for value in self.future_times),
                "forecast_fingerprints": tuple(
                    row.result.evidence.fingerprint
                    for row in self.forecast_results
                    if row.available and row.result.evidence is not None
                ),
                "disagreement": self.disagreement.state.value,
                "blackboard": self.blackboard.fingerprint,
                "checkpoint": self.checkpoint.fingerprint,
                "skill_certification_eligible": self.skill_certification_eligible,
            }
        )


def timeframe_delta(timeframe: str) -> timedelta:
    value = timeframe.strip().upper()
    if len(value) < 2:
        raise ValueError("unsupported_integrated_cycle_timeframe")
    unit, raw = value[0], value[1:]
    try:
        amount = int(raw)
    except ValueError as exc:
        raise ValueError("unsupported_integrated_cycle_timeframe") from exc
    if amount < 1:
        raise ValueError("unsupported_integrated_cycle_timeframe")
    if unit == "M":
        return timedelta(minutes=amount)
    if unit == "H":
        return timedelta(hours=amount)
    if unit == "D":
        return timedelta(days=amount)
    raise ValueError("unsupported_integrated_cycle_timeframe")


def nominal_future_times(
    as_of: datetime,
    *,
    timeframe: str,
    horizon_steps: int,
) -> tuple[datetime, ...]:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("integrated_cycle_as_of_must_be_timezone_aware")
    if not 1 <= horizon_steps <= 64:
        raise ValueError("integrated_cycle_horizon_out_of_bounds")
    step = timeframe_delta(timeframe)
    return tuple(as_of + step * index for index in range(1, horizon_steps + 1))


def _evidence(results: Iterable[ContractorForecastResult]) -> tuple[ForecastEvidence, ...]:
    values = []
    for row in results:
        evidence = row.result.evidence
        if row.available and evidence is not None:
            values.append(evidence)
    return tuple(values)


def _unavailable_detail(results: Iterable[ContractorForecastResult]) -> str:
    parts = []
    for row in results:
        if not row.available:
            parts.append(f"{row.result.provider_id}={row.result.error}")
    return ";".join(parts) or "unknown"


def _direction(evidence: ForecastEvidence) -> str:
    if evidence.p50 > evidence.origin_value:
        return "up"
    if evidence.p50 < evidence.origin_value:
        return "down"
    return "flat"


def _classify_provider_disagreement(
    evidences: Iterable[ForecastEvidence],
) -> ForecastDisagreement:
    """Classify providers that share one PIT board but use different input schemas.

    Chronos/TimesFM hash close-only request context while Kronos hashes OHLCV.
    Their provider request hashes therefore cannot be used as a cross-provider
    identity. M135 binds all evidence to one explicit PIT context on the
    blackboard and requires symbol/timeframe/as_of/horizon identity here.
    """

    rows = tuple(evidences)
    if not rows:
        return ForecastDisagreement(DisagreementState.UNAVAILABLE, (), ())
    identities = {
        (
            row.symbol.upper(),
            row.timeframe.upper(),
            row.as_of,
            row.horizon_steps,
        )
        for row in rows
    }
    if len(identities) != 1:
        raise ValueError("integrated_cycle_forecasts_do_not_share_pit_identity")
    if len({row.provider_id for row in rows}) != len(rows):
        raise ValueError("integrated_cycle_duplicate_provider_evidence")
    directions = tuple(sorted((row.provider_id, _direction(row)) for row in rows))
    fingerprints = tuple(sorted(row.fingerprint for row in rows))
    if {name for name, _ in directions} != set(_EXPECTED_PROVIDERS):
        return ForecastDisagreement(DisagreementState.PARTIAL, directions, fingerprints)
    counts = {
        name: sum(direction == name for _, direction in directions)
        for name in ("up", "down", "flat")
    }
    if counts["up"] == 3:
        state = DisagreementState.UNANIMOUS_UP
    elif counts["down"] == 3:
        state = DisagreementState.UNANIMOUS_DOWN
    elif counts["flat"] == 3:
        state = DisagreementState.UNANIMOUS_FLAT
    elif counts["up"] == 2 and counts["down"] == 1:
        state = DisagreementState.TWO_UP_ONE_DOWN
    elif counts["down"] == 2 and counts["up"] == 1:
        state = DisagreementState.TWO_DOWN_ONE_UP
    else:
        state = DisagreementState.MIXED_WITH_FLAT
    return ForecastDisagreement(state, directions, fingerprints)


def run_integrated_research_cycle(
    raw_bars: Iterable[MT5Bar],
    manager: ForecastManager,
    store: SQLiteResearchCycleStore,
    *,
    config: IntegratedResearchCycleConfig = IntegratedResearchCycleConfig(),
    cycle_id: str = "",
    created_at: datetime | None = None,
) -> IntegratedResearchCycleResult:
    """Run one bounded read-only research cycle.

    M135 intentionally uses a nominal future timestamp cadence for Kronos so
    the first hardware integration can be tested without feeding future prices
    or outcomes to any model. Because broker-session knowledge is not yet bound
    point-in-time, this result is explicitly ineligible for forecast-skill
    certification. M136+ can replace this schedule with a broker schedule known
    at decision time.
    """

    rows = tuple(raw_bars)
    feature_rows = completed_feature_bars_from_mt5(rows)
    if len(feature_rows) < config.context_observations:
        raise ValueError(
            f"integrated_cycle_insufficient_completed_history:{len(feature_rows)}"
        )
    context_rows = feature_rows[-config.context_observations :]
    as_of = context_rows[-1].at
    pit = build_pit_context(
        context_rows,
        symbol=config.symbol,
        timeframe=config.timeframe,
        as_of=as_of,
        max_observations=config.context_observations,
    )
    future_times = nominal_future_times(
        as_of,
        timeframe=config.timeframe,
        horizon_steps=config.horizon_steps,
    )
    resolved_cycle_id = cycle_id.strip() or (
        f"m135-{config.symbol.upper()}-{config.timeframe.upper()}-"
        f"{pit.context_hash[:16]}"
    )

    manager.select(ForecastSelectionMode.ALL_THREE)
    try:
        states = manager.start_selected()
        if config.require_all_three:
            not_ready = {
                provider_id: state.value
                for provider_id, state in states.items()
                if provider_id in _EXPECTED_PROVIDERS
                and state is not ProviderWorkerState.READY
            }
            if not_ready:
                raise RuntimeError(
                    "integrated_cycle_workers_not_ready:"
                    + json.dumps(not_ready, sort_keys=True, separators=(",", ":"))
                )

        forecast_results = manager.forecast_selected(
            context_rows,
            symbol=config.symbol,
            timeframe=config.timeframe,
            horizon_steps=config.horizon_steps,
            future_times=future_times,
        )
    finally:
        manager.stop_all()

    evidences = _evidence(forecast_results)
    if config.require_all_three:
        providers = {row.provider_id for row in evidences}
        if providers != set(_EXPECTED_PROVIDERS):
            raise RuntimeError(
                "integrated_cycle_forecast_evidence_incomplete:"
                + _unavailable_detail(forecast_results)
            )

    disagreement = _classify_provider_disagreement(evidences)

    source_item = BlackboardItem(
        BlackboardKind.SOURCE,
        f"pit:{config.symbol.upper()}:{config.timeframe.upper()}:{as_of.isoformat()}",
        pit.context_hash,
    )
    forecast_items = tuple(
        BlackboardItem(
            BlackboardKind.FORECAST,
            f"{evidence.provider_id}:{evidence.model_id}:{evidence.as_of.isoformat()}",
            evidence.fingerprint,
            (source_item.fingerprint,),
        )
        for evidence in evidences
    )
    disagreement_payload = _digest(
        {
            "state": disagreement.state.value,
            "provider_directions": disagreement.provider_directions,
            "evidence_fingerprints": disagreement.evidence_fingerprints,
            "pit_context_hash": pit.context_hash,
        }
    )
    score_item = BlackboardItem(
        BlackboardKind.SCORECARD,
        f"forecast-disagreement:{config.symbol.upper()}:{config.timeframe.upper()}:{as_of.isoformat()}",
        disagreement_payload,
        tuple(item.fingerprint for item in forecast_items),
    )
    board = ResearchBlackboard(
        resolved_cycle_id,
        as_of,
        (source_item,) + forecast_items + (score_item,),
    )
    now = created_at or datetime.now(timezone.utc)
    checkpoint = make_checkpoint(
        board,
        stage=ResearchStage.CHECKPOINT,
        completed_job_fingerprints=tuple(item.fingerprint for item in forecast_items),
        created_at=now,
    )
    store.append(checkpoint)
    return IntegratedResearchCycleResult(
        resolved_cycle_id,
        config,
        pit,
        future_times,
        forecast_results,
        disagreement,
        board,
        checkpoint,
    )
