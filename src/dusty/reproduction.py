from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .research import ExperimentResult


class ReproductionStatus(StrEnum):
    MATCHED = "matched"
    DIVERGED = "diverged"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class PerformanceClaim:
    strategy_hash: str
    min_samples: int
    mean_return: float | None = None
    hit_rate: float | None = None
    mean_return_tolerance: float = 0.0
    hit_rate_tolerance: float = 0.0

    def __post_init__(self) -> None:
        if not self.strategy_hash:
            raise ValueError("strategy hash is required")
        if self.min_samples < 1:
            raise ValueError("minimum samples must be positive")
        if self.mean_return_tolerance < 0 or self.hit_rate_tolerance < 0:
            raise ValueError("claim tolerances cannot be negative")


@dataclass(frozen=True, slots=True)
class ReproductionAssessment:
    status: ReproductionStatus
    reasons: tuple[str, ...]


def assess_reproduction(
    claim: PerformanceClaim,
    independent: ExperimentResult,
) -> ReproductionAssessment:
    """Treat external performance as a claim until Dusty's own metric semantics reproduce it."""
    if independent.strategy_hash != claim.strategy_hash:
        return ReproductionAssessment(ReproductionStatus.DIVERGED, ("strategy_hash_mismatch",))
    if independent.sample_count < claim.min_samples:
        return ReproductionAssessment(ReproductionStatus.INSUFFICIENT, ("insufficient_samples",))

    reasons: list[str] = []
    if claim.mean_return is not None and abs(independent.mean_return - claim.mean_return) > claim.mean_return_tolerance:
        reasons.append("mean_return_diverged")
    if claim.hit_rate is not None and abs(independent.hit_rate - claim.hit_rate) > claim.hit_rate_tolerance:
        reasons.append("hit_rate_diverged")
    return ReproductionAssessment(
        ReproductionStatus.DIVERGED if reasons else ReproductionStatus.MATCHED,
        tuple(reasons),
    )
