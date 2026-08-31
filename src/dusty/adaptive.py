from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class CurriculumCycleMetrics:
    acquired: int
    classified: int
    hypotheses_tested: int
    useful_lessons: int
    bytes_added: int = 0
    cpu_seconds: float = 0.0

    def __post_init__(self) -> None:
        values = (self.acquired, self.classified, self.hypotheses_tested, self.useful_lessons, self.bytes_added)
        if any(value < 0 for value in values) or self.cpu_seconds < 0:
            raise ValueError("curriculum metrics cannot be negative")

    @property
    def unclassified_backlog(self) -> int:
        return max(0, self.acquired - self.classified)

    @property
    def untested_backlog(self) -> int:
        return max(0, self.classified - self.hypotheses_tested)


@dataclass(frozen=True, slots=True)
class AcquisitionPolicy:
    max_batch: int = 20
    max_unclassified_backlog: int = 20
    max_untested_backlog: int = 40
    min_tests_before_expand: int = 1

    def __post_init__(self) -> None:
        if min(self.max_batch, self.max_unclassified_backlog, self.max_untested_backlog, self.min_tests_before_expand) < 1:
            raise ValueError("acquisition policy values must be positive")


@dataclass(frozen=True, slots=True)
class AcquisitionDecision:
    approved_count: int
    reason: str


def decide_acquisition(
    metrics: CurriculumCycleMetrics,
    *,
    knowledge_gap: str,
    requested_count: int,
    policy: AcquisitionPolicy = AcquisitionPolicy(),
) -> AcquisitionDecision:
    """External acquisition is earned by a specific gap and consumed research backlog."""
    if requested_count < 1:
        return AcquisitionDecision(0, "nothing_requested")
    if not knowledge_gap.strip():
        return AcquisitionDecision(0, "no_measured_knowledge_gap")
    if metrics.acquired == 0:
        return AcquisitionDecision(min(requested_count, policy.max_batch), "bootstrap_gap")
    if metrics.unclassified_backlog >= policy.max_unclassified_backlog:
        return AcquisitionDecision(0, "classification_backlog")
    if metrics.untested_backlog >= policy.max_untested_backlog:
        return AcquisitionDecision(0, "experiment_backlog")
    if metrics.hypotheses_tested < policy.min_tests_before_expand:
        return AcquisitionDecision(0, "curriculum_not_consumed")
    return AcquisitionDecision(min(requested_count, policy.max_batch), "gap_approved")


@dataclass(frozen=True, slots=True)
class RegimeObservation:
    strategy_hash: str
    tags: tuple[str, ...]
    return_value: float

    def __post_init__(self) -> None:
        if not self.strategy_hash or not self.tags:
            raise ValueError("regime observation requires strategy and tags")


@dataclass(frozen=True, slots=True)
class RegimeStats:
    tags: tuple[str, ...]
    sample_count: int
    mean_return: float
    hit_rate: float


def summarize_regimes(
    observations: Iterable[RegimeObservation],
    *,
    max_regimes: int = 32,
) -> tuple[RegimeStats, ...]:
    """Bound context cardinality so regime learning cannot become a RAM sink."""
    if max_regimes < 1:
        raise ValueError("max_regimes must be positive")
    strategy_hash: str | None = None
    totals: dict[tuple[str, ...], list[float | int]] = {}
    for observation in observations:
        if strategy_hash is None:
            strategy_hash = observation.strategy_hash
        elif observation.strategy_hash != strategy_hash:
            raise ValueError("regime summary must refer to one strategy")
        tags = tuple(sorted({tag.strip().lower() for tag in observation.tags if tag.strip()}))
        if not tags:
            continue
        if tags not in totals and len(totals) >= max_regimes:
            raise ValueError("regime cardinality budget exceeded")
        state = totals.setdefault(tags, [0, 0.0, 0])
        state[0] = int(state[0]) + 1
        state[1] = float(state[1]) + observation.return_value
        state[2] = int(state[2]) + int(observation.return_value > 0)
    result = []
    for tags, state in sorted(totals.items()):
        count = int(state[0])
        result.append(
            RegimeStats(
                tags=tags,
                sample_count=count,
                mean_return=float(state[1]) / count,
                hit_rate=int(state[2]) / count,
            )
        )
    return tuple(result)
