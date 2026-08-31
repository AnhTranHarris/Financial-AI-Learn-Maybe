from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable

from .curriculum import MethodInsight, resolve_symbol
from .research import Clause, StrategySpec


@dataclass(frozen=True, slots=True)
class HypothesisSeed:
    spec: StrategySpec
    source_ids: tuple[str, ...]
    family_hash: str

    def __post_init__(self) -> None:
        if not self.source_ids or not self.family_hash:
            raise ValueError("hypothesis seed requires provenance and family identity")


@dataclass(frozen=True, slots=True)
class HypothesisDraft:
    target_symbol: str
    spec: StrategySpec
    parent_hashes: tuple[str, ...]
    source_ids: tuple[str, ...]
    insight_ids: tuple[str, ...]
    falsifiers: tuple[str, ...]


def _clause_key(clause: Clause) -> tuple[str, str, str]:
    return clause.feature, clause.op.value, repr(clause.value)


def compose_hypotheses(
    seeds: Iterable[HypothesisSeed],
    insights: Iterable[MethodInsight],
    *,
    target_symbol: str,
    max_candidates: int = 16,
    max_clauses: int = 6,
) -> tuple[HypothesisDraft, ...]:
    """Create bounded pairwise rule unions; never generate executable source code."""
    if max_candidates < 1 or max_clauses < 1:
        raise ValueError("candidate and clause budgets must be positive")
    target = resolve_symbol(target_symbol).canonical
    seed_list = tuple(sorted(seeds, key=lambda item: item.spec.strategy_hash))
    insight_list = tuple(insight for insight in insights if insight.target_symbol == target)
    drafts: list[HypothesisDraft] = []
    seen: set[str] = set()
    for left, right in combinations(seed_list, 2):
        if len(drafts) >= max_candidates:
            break
        if left.spec.direction is not right.spec.direction:
            continue
        if left.spec.horizon_steps != right.spec.horizon_steps:
            continue
        clauses = {
            _clause_key(clause): clause
            for clause in (*left.spec.clauses, *right.spec.clauses)
        }
        ordered = tuple(clauses[key] for key in sorted(clauses))
        if len(ordered) > max_clauses:
            continue
        spec = StrategySpec(
            strategy_id=f"hyp:{left.spec.strategy_hash[:8]}:{right.spec.strategy_hash[:8]}",
            direction=left.spec.direction,
            clauses=ordered,
            horizon_steps=left.spec.horizon_steps,
            cost_bps=max(left.spec.cost_bps, right.spec.cost_bps),
        )
        if spec.strategy_hash in seen or spec.strategy_hash in {left.spec.strategy_hash, right.spec.strategy_hash}:
            continue
        seen.add(spec.strategy_hash)
        features = {clause.feature.lower() for clause in ordered}
        relevant_insights = tuple(
            insight.insight_id
            for insight in insight_list
            if features.intersection(insight.features)
        )
        drafts.append(
            HypothesisDraft(
                target_symbol=target,
                spec=spec,
                parent_hashes=tuple(sorted((left.spec.strategy_hash, right.spec.strategy_hash))),
                source_ids=tuple(sorted(set(left.source_ids) | set(right.source_ids))),
                insight_ids=tuple(sorted(set(relevant_insights))),
                falsifiers=(
                    "post_cost_mean_nonpositive",
                    "walk_forward_failure",
                    "mt5_reconciliation_failure",
                ),
            )
        )
    return tuple(drafts)
