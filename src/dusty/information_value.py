from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .adaptive import AcquisitionDecision, AcquisitionPolicy, CurriculumCycleMetrics, decide_acquisition
from .markets import SymbolResearchProfile
from .news import NewsSource


class SourceValueState(StrEnum):
    INSUFFICIENT = "insufficient"
    USEFUL = "useful"
    PAUSE = "pause"


@dataclass(frozen=True, slots=True)
class SourceValueObservation:
    source_id: str
    target_symbol: str
    event_class: str
    baseline_utility: float
    with_source_utility: float
    bytes_added: int = 0
    cpu_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.target_symbol.strip() or not self.event_class.strip():
            raise ValueError("source value observation requires source, symbol, and event class")
        if self.bytes_added < 0 or self.cpu_seconds < 0:
            raise ValueError("resource usage cannot be negative")

    @property
    def incremental_utility(self) -> float:
        """Caller defines utility so larger is better; Dusty only measures the incremental difference."""
        return self.with_source_utility - self.baseline_utility


@dataclass(frozen=True, slots=True)
class SourceValueGate:
    min_samples: int = 3
    min_mean_incremental_utility: float = 0.0
    min_positive_rate: float = 0.5

    def __post_init__(self) -> None:
        if self.min_samples < 1 or not 0.0 <= self.min_positive_rate <= 1.0:
            raise ValueError("invalid source value gate")


@dataclass(frozen=True, slots=True)
class SourceValueAssessment:
    source_id: str
    target_symbol: str
    sample_count: int
    mean_incremental_utility: float
    positive_rate: float
    bytes_added: int
    cpu_seconds: float
    state: SourceValueState
    reasons: tuple[str, ...] = ()


def evaluate_source_value(
    observations: Iterable[SourceValueObservation],
    gate: SourceValueGate = SourceValueGate(),
) -> SourceValueAssessment:
    rows = tuple(observations)
    if not rows:
        return SourceValueAssessment("", "", 0, 0.0, 0.0, 0, 0.0, SourceValueState.INSUFFICIENT, ("no_observations",))
    sources = {row.source_id for row in rows}
    symbols = {row.target_symbol for row in rows}
    if len(sources) != 1 or len(symbols) != 1:
        raise ValueError("source value assessment must refer to one source and target symbol")
    count = len(rows)
    increments = tuple(row.incremental_utility for row in rows)
    mean_incremental = sum(increments) / count
    positive_rate = sum(value > 0 for value in increments) / count
    reasons = []
    if count < gate.min_samples:
        reasons.append("insufficient_samples")
        state = SourceValueState.INSUFFICIENT
    else:
        if mean_incremental <= gate.min_mean_incremental_utility:
            reasons.append("no_incremental_value")
        if positive_rate < gate.min_positive_rate:
            reasons.append("low_positive_rate")
        state = SourceValueState.PAUSE if reasons else SourceValueState.USEFUL
    return SourceValueAssessment(
        source_id=rows[0].source_id,
        target_symbol=rows[0].target_symbol,
        sample_count=count,
        mean_incremental_utility=mean_incremental,
        positive_rate=positive_rate,
        bytes_added=sum(row.bytes_added for row in rows),
        cpu_seconds=sum(row.cpu_seconds for row in rows),
        state=state,
        reasons=tuple(reasons),
    )


def decide_news_acquisition(
    source: NewsSource,
    profile: SymbolResearchProfile,
    metrics: CurriculumCycleMetrics,
    *,
    knowledge_gap: str,
    requested_count: int,
    value: SourceValueAssessment | None = None,
    policy: AcquisitionPolicy = AcquisitionPolicy(),
) -> AcquisitionDecision:
    """A free source still has to be relevant, useful, and earned by consumed research backlog."""
    if not source.automatic_acquisition_allowed:
        return AcquisitionDecision(0, "source_not_free_for_automatic_acquisition")
    currency_match = bool(set(source.currencies) & set(profile.currencies))
    underlier_match = bool(set(source.underliers) & {profile.market.economic_underlier, *profile.related_underliers})
    asset_match = bool(set(source.asset_classes) & {profile.market.asset_class, *profile.allowed_asset_context})
    if not (currency_match or underlier_match or asset_match):
        return AcquisitionDecision(0, "source_not_symbol_relevant")
    if value is not None and value.state is SourceValueState.PAUSE:
        return AcquisitionDecision(0, "source_incremental_value_failed")
    return decide_acquisition(metrics, knowledge_gap=knowledge_gap, requested_count=requested_count, policy=policy)
