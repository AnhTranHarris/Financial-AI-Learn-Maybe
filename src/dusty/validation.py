from __future__ import annotations

from dataclasses import dataclass
from statistics import fmean
from typing import Iterable

from .research import Clause, ExperimentResult, StrategySpec


@dataclass(frozen=True, slots=True)
class TournamentEntry:
    spec: StrategySpec
    result: ExperimentResult

    def __post_init__(self) -> None:
        if self.spec.strategy_hash != self.result.strategy_hash:
            raise ValueError("tournament result does not belong to strategy")


@dataclass(frozen=True, slots=True)
class TournamentOutcome:
    champion_hash: str | None
    ranked_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ValidationFold:
    fold_id: str
    result: ExperimentResult


@dataclass(frozen=True, slots=True)
class RobustnessGate:
    min_folds: int = 3
    min_fold_mean_return: float = 0.0
    max_failed_folds: int = 0
    max_mean_return_spread: float = 1.0

    def __post_init__(self) -> None:
        if self.min_folds < 1 or self.max_failed_folds < 0:
            raise ValueError("invalid fold requirements")
        if self.max_mean_return_spread < 0:
            raise ValueError("return spread cannot be negative")


@dataclass(frozen=True, slots=True)
class RobustnessAssessment:
    passed: bool
    fold_count: int
    failed_folds: int
    mean_return: float
    worst_fold_mean_return: float
    mean_return_spread: float
    reasons: tuple[str, ...]


def mutate_numeric_clause(
    parent: StrategySpec,
    clause_index: int,
    values: Iterable[int | float],
    *,
    max_variants: int = 16,
) -> tuple[StrategySpec, ...]:
    """Generate a deliberately bounded local neighborhood; never explode a parameter grid."""
    if not 0 <= clause_index < len(parent.clauses):
        raise IndexError("clause index out of range")
    if max_variants < 1:
        raise ValueError("max_variants must be positive")
    original = parent.clauses[clause_index]
    if not isinstance(original.value, (int, float)) or isinstance(original.value, bool):
        raise TypeError("only numeric clauses may be mutated")

    variants: list[StrategySpec] = []
    seen_hashes = {parent.strategy_hash}
    for value in values:
        if len(variants) >= max_variants:
            break
        replacement = Clause(original.feature, original.op, value)
        clauses = list(parent.clauses)
        clauses[clause_index] = replacement
        candidate = StrategySpec(
            strategy_id=f"{parent.strategy_id}:m{clause_index}:{value}",
            direction=parent.direction,
            clauses=tuple(clauses),
            horizon_steps=parent.horizon_steps,
            cost_bps=parent.cost_bps,
        )
        if candidate.strategy_hash in seen_hashes:
            continue
        seen_hashes.add(candidate.strategy_hash)
        variants.append(candidate)
    return tuple(variants)


def rank_tournament(entries: Iterable[TournamentEntry]) -> TournamentOutcome:
    """Rank deterministic research results without voting agents or source popularity."""
    ordered = sorted(
        entries,
        key=lambda item: (
            -item.result.mean_return,
            -item.result.hit_rate,
            -item.result.max_loss,
            item.result.strategy_hash,
        ),
    )
    hashes = tuple(item.result.strategy_hash for item in ordered)
    return TournamentOutcome(hashes[0] if hashes else None, hashes)


def evaluate_walk_forward(
    folds: Iterable[ValidationFold],
    gate: RobustnessGate,
) -> RobustnessAssessment:
    collected = tuple(folds)
    if not collected:
        return RobustnessAssessment(False, 0, 0, 0.0, 0.0, 0.0, ("no_folds",))
    hashes = {fold.result.strategy_hash for fold in collected}
    if len(hashes) != 1:
        raise ValueError("walk-forward folds must refer to one strategy")

    returns = tuple(fold.result.mean_return for fold in collected)
    failed = sum(value <= gate.min_fold_mean_return for value in returns)
    spread = max(returns) - min(returns)
    reasons: list[str] = []
    if len(collected) < gate.min_folds:
        reasons.append("insufficient_folds")
    if failed > gate.max_failed_folds:
        reasons.append("too_many_failed_folds")
    if spread > gate.max_mean_return_spread:
        reasons.append("fold_instability")
    return RobustnessAssessment(
        passed=not reasons,
        fold_count=len(collected),
        failed_folds=failed,
        mean_return=fmean(returns),
        worst_fold_mean_return=min(returns),
        mean_return_spread=spread,
        reasons=tuple(reasons),
    )
