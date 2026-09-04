from __future__ import annotations

"""Read-only workstation certification for the M135-M154 research organism.

This module is intentionally hardware-facing but remains research-only:
- MetaTrader 5 access is historical/read-only.
- Forecast contractors remain isolated and receive no broker credentials.
- Ollama/Qwen receives evidence hashes and a bounded scorecard only.
- No component can grant broker write, risk override, entry veto, or promotion.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from .integrated_research_cycle import (
    IntegratedResearchCycleConfig,
    IntegratedResearchCycleResult,
    run_integrated_research_cycle,
)
from .mt5worker import MT5BarRequest
from .ollama_quant_reviewer import OllamaQuantReviewer, LocalQuantReviewResult
from .provider_forecast_adapter import ForecastEvidence
from .provider_multi_service import ForecastContractorManager
from .provider_registry import ProviderRegistry
from .quant_reviewer import QuantReviewRequest
from .research_organism import (
    MT5ResearchDataService,
    ResearchOrganism,
    SQLiteResearchOrganismStore,
    StageWork,
)
from .research_runtime import (
    BlackboardItem,
    BlackboardKind,
    ResearchBlackboard,
    ResearchStage,
    SQLiteResearchCycleStore,
)


EXPECTED_PROVIDERS = ("chronos2", "kronos-small", "timesfm-2.5")


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class HardwareCertificationConfig:
    terminal_path: str
    provider_root: Path
    work_root: Path
    symbol: str = "EURUSD"
    native_symbol: str = "EURUSD"
    timeframe: str = "M15"
    history_days: int = 14
    context_observations: int = 256
    horizon_steps: int = 4
    ollama_model: str = "qwen3:1.7b"
    ollama_base_url: str = "http://127.0.0.1:11434"

    def __post_init__(self) -> None:
        if not self.terminal_path.strip():
            raise ValueError("hardware certification requires MT5 terminal path")
        if not self.symbol.strip() or not self.native_symbol.strip() or not self.timeframe.strip():
            raise ValueError("hardware certification market identity incomplete")
        if self.history_days < 2:
            raise ValueError("hardware certification history must span at least two days")
        if self.context_observations < 32:
            raise ValueError("hardware certification requires at least 32 context observations")
        if not 1 <= self.horizon_steps <= 64:
            raise ValueError("hardware certification horizon out of bounds")
        if not self.ollama_model.strip():
            raise ValueError("hardware certification requires Ollama model tag")


@dataclass(frozen=True, slots=True)
class HardwareCertificationResult:
    integrated_cycle: IntegratedResearchCycleResult
    quant_review: LocalQuantReviewResult
    organism_board_fingerprint: str
    organism_checkpoint_fingerprint: str
    organism_integrity_ok: bool
    mt5_raw_bar_count: int
    mt5_completed_bar_count: int
    provider_ids: tuple[str, ...]
    software_only_skill_claim: bool = False
    broker_write_authority: bool = False
    entry_veto_authority: bool = False
    promotion_authority: bool = False
    risk_override_authority: bool = False

    def __post_init__(self) -> None:
        if self.provider_ids != EXPECTED_PROVIDERS:
            raise ValueError("hardware certification requires all three forecast providers")
        if self.software_only_skill_claim:
            raise ValueError("hardware certification cannot claim forecast skill")
        if (
            self.broker_write_authority
            or self.entry_veto_authority
            or self.promotion_authority
            or self.risk_override_authority
        ):
            raise ValueError("hardware certification cannot receive operational authority")
        if len(self.organism_board_fingerprint) != 64 or len(self.organism_checkpoint_fingerprint) != 64:
            raise ValueError("hardware certification requires SHA-256 organism identities")
        if not self.organism_integrity_ok:
            raise ValueError("hardware certification organism store failed integrity")
        if self.mt5_raw_bar_count < 2 or self.mt5_completed_bar_count < 1:
            raise ValueError("hardware certification MT5 history is unusable")
        if not self.quant_review.available:
            raise ValueError("hardware certification requires successful local Qwen review")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "integrated_cycle": self.integrated_cycle.fingerprint,
                "quant_review": self.quant_review.evidence.fingerprint if self.quant_review.evidence else None,
                "organism_board": self.organism_board_fingerprint,
                "organism_checkpoint": self.organism_checkpoint_fingerprint,
                "organism_integrity_ok": self.organism_integrity_ok,
                "mt5_raw_bar_count": self.mt5_raw_bar_count,
                "mt5_completed_bar_count": self.mt5_completed_bar_count,
                "provider_ids": self.provider_ids,
                "software_only_skill_claim": self.software_only_skill_claim,
                "broker_write_authority": self.broker_write_authority,
                "entry_veto_authority": self.entry_veto_authority,
                "promotion_authority": self.promotion_authority,
                "risk_override_authority": self.risk_override_authority,
            }
        )


def _available_evidence(cycle: IntegratedResearchCycleResult) -> tuple[ForecastEvidence, ...]:
    rows = tuple(
        result.result.evidence
        for result in cycle.forecast_results
        if result.available and result.result.evidence is not None
    )
    if tuple(sorted(row.provider_id for row in rows)) != EXPECTED_PROVIDERS:
        raise RuntimeError("hardware certification forecast evidence incomplete")
    return rows


def _review_request(
    cycle: IntegratedResearchCycleResult,
    *,
    reviewer: OllamaQuantReviewer,
    model_tag: str,
) -> QuantReviewRequest:
    evidences = _available_evidence(cycle)
    model_digest = reviewer._model_digest(model_tag)  # internal certification probe
    scorecard = _canonical(
        {
            "protocol": "dusty-m1541-hardware-review-scorecard-v1",
            "purpose": "workstation_integration_only",
            "forecast_skill_claimed": False,
            "symbol": cycle.config.symbol.upper(),
            "timeframe": cycle.config.timeframe.upper(),
            "as_of": cycle.pit_context.as_of.isoformat(),
            "horizon_steps": cycle.config.horizon_steps,
            "future_schedule_basis": cycle.future_schedule_basis,
            "skill_certification_eligible": cycle.skill_certification_eligible,
            "disagreement": cycle.disagreement.state.value,
            "providers": [
                {
                    "provider_id": evidence.provider_id,
                    "model_id": evidence.model_id,
                    "model_revision": evidence.model_revision,
                    "p10": evidence.p10,
                    "p50": evidence.p50,
                    "p90": evidence.p90,
                    "origin": evidence.origin_value,
                    "fingerprint": evidence.fingerprint,
                }
                for evidence in evidences
            ],
            "authority": "research_only",
        }
    )
    return QuantReviewRequest(
        request_id=f"m1541-{cycle.cycle_id}",
        model_tag=model_tag,
        model_digest=model_digest,
        forecast_fingerprints=tuple(evidence.fingerprint for evidence in evidences),
        strategy_fingerprints=(),
        evidence_fingerprints=(cycle.blackboard.fingerprint,),
        scorecard_text=scorecard,
        question=(
            "Review only whether the three forecast evidence streams are coherent enough for "
            "continued research. Do not infer profitability, place a trade, choose position size, "
            "promote a strategy, or override risk. If evidence is weak or contradictory use WAIT, "
            "NO_TRADE, or RESEARCH_REQUIRED."
        ),
    )


def _seed_organism_board(
    cycle: IntegratedResearchCycleResult,
    review: LocalQuantReviewResult,
) -> ResearchBlackboard:
    if not review.available or review.evidence is None:
        raise ValueError("hardware organism seed requires successful quant review")
    item = BlackboardItem(
        BlackboardKind.LESSON,
        f"quant-review:{review.evidence.model_tag}:{cycle.pit_context.as_of.isoformat()}",
        review.evidence.fingerprint,
        tuple(sorted(value.fingerprint for value in cycle.blackboard.items)),
    )
    board = cycle.blackboard.add(item)
    return ResearchBlackboard(
        cycle_id=f"{cycle.cycle_id}-m1541",
        as_of=board.as_of,
        items=board.items,
    )


def _empty_handlers() -> dict[ResearchStage, object]:
    return {
        stage: (lambda _board: StageWork())
        for stage in ResearchStage
        if stage not in (ResearchStage.CHECKPOINT, ResearchStage.COMPLETE)
    }


def run_hardware_certification(
    config: HardwareCertificationConfig,
    *,
    now: datetime | None = None,
    data_service: MT5ResearchDataService | None = None,
    manager: ForecastContractorManager | None = None,
    reviewer: OllamaQuantReviewer | None = None,
) -> HardwareCertificationResult:
    """Exercise MT5 history + 3 forecast workers + Qwen + durable organism.

    This is a workstation integration proof, not a forecast-skill or profitability
    certification. Every downstream authority flag remains false.
    """

    when = now or datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("hardware certification clock must be timezone-aware")
    config.work_root.mkdir(parents=True, exist_ok=True)

    service = data_service or MT5ResearchDataService()
    request = MT5BarRequest(
        terminal_path=config.terminal_path,
        symbol=config.native_symbol,
        timeframe=config.timeframe,
        start=when - timedelta(days=config.history_days),
        end=when,
        chunk_days=min(config.history_days, 7),
    )
    batch = service.load(request)
    if len(batch.completed_bars) < config.context_observations:
        raise RuntimeError(
            "hardware certification insufficient completed MT5 history:"
            f"{len(batch.completed_bars)}"
        )

    registry = ProviderRegistry(config.provider_root)
    actual_manager = manager or ForecastContractorManager(registry)
    cycle_store_path = config.work_root / "m1541-integrated-cycle.sqlite"
    cycle_store = SQLiteResearchCycleStore(cycle_store_path)
    try:
        cycle = run_integrated_research_cycle(
            batch.raw_bars,
            actual_manager,
            cycle_store,
            config=IntegratedResearchCycleConfig(
                symbol=config.symbol,
                timeframe=config.timeframe,
                context_observations=config.context_observations,
                horizon_steps=config.horizon_steps,
                require_all_three=True,
            ),
            cycle_id=f"m1541-hardware-{config.symbol.upper()}-{config.timeframe.upper()}",
            created_at=when,
        )
        if not cycle_store.integrity_ok():
            raise RuntimeError("hardware certification integrated-cycle store integrity failed")
    finally:
        cycle_store.close()

    actual_reviewer = reviewer or OllamaQuantReviewer(base_url=config.ollama_base_url)
    review_request = _review_request(cycle, reviewer=actual_reviewer, model_tag=config.ollama_model)
    review = actual_reviewer.review(review_request)
    if not review.available:
        raise RuntimeError(f"hardware certification Qwen unavailable:{review.error}")

    organism_path = config.work_root / "m1541-research-organism.sqlite"
    organism_store = SQLiteResearchOrganismStore(organism_path)
    try:
        organism = ResearchOrganism(organism_store, clock=lambda: when)
        initial = _seed_organism_board(cycle, review)
        organism_result = organism.run_until_complete(
            initial,
            _empty_handlers(),
            maximum_stage_advances=16,
        )
        integrity = organism_store.integrity_ok()
    finally:
        organism_store.close()

    providers = tuple(sorted(evidence.provider_id for evidence in _available_evidence(cycle)))
    return HardwareCertificationResult(
        integrated_cycle=cycle,
        quant_review=review,
        organism_board_fingerprint=organism_result.board.fingerprint,
        organism_checkpoint_fingerprint=organism_result.checkpoint.fingerprint,
        organism_integrity_ok=integrity,
        mt5_raw_bar_count=len(batch.raw_bars),
        mt5_completed_bar_count=len(batch.completed_bars),
        provider_ids=providers,
    )


def render_hardware_report(result: HardwareCertificationResult) -> dict[str, object]:
    review = result.quant_review.evidence
    assert review is not None
    return {
        "protocol": "dusty-m1541-local-hardware-certification-v1",
        "status": "pass",
        "fingerprint": result.fingerprint,
        "market": {
            "symbol": result.integrated_cycle.config.symbol.upper(),
            "timeframe": result.integrated_cycle.config.timeframe.upper(),
            "raw_bars": result.mt5_raw_bar_count,
            "completed_bars": result.mt5_completed_bar_count,
            "as_of": result.integrated_cycle.pit_context.as_of.isoformat(),
        },
        "forecast_contractors": {
            "providers": list(result.provider_ids),
            "disagreement": result.integrated_cycle.disagreement.state.value,
            "skill_certification_eligible": result.integrated_cycle.skill_certification_eligible,
            "forecast_skill_claimed": False,
        },
        "qwen": {
            "model_tag": review.model_tag,
            "model_digest": review.model_digest,
            "state": review.state.value,
            "evidence_fingerprint": review.fingerprint,
        },
        "organism": {
            "board_fingerprint": result.organism_board_fingerprint,
            "checkpoint_fingerprint": result.organism_checkpoint_fingerprint,
            "integrity_ok": result.organism_integrity_ok,
        },
        "safety": {
            "mt5_orders": False,
            "broker_credentials": False,
            "broker_write": False,
            "entry_veto": False,
            "promotion": False,
            "risk_override": False,
        },
    }
