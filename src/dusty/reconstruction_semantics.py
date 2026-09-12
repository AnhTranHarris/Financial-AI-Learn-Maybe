from __future__ import annotations

"""Deterministic semantic preflight for reconstructed research strategies.

This module does not reinterpret an LLM's rule. It asks a narrower question:
can the declared entry logic ever activate on the bounded point-in-time feature
sample supplied by research? A reconstruction that cannot activate is evidence
of a semantic/reconstruction defect, not evidence that the trading theory lost.
"""

from dataclasses import dataclass
from typing import Iterable

from .runtime import RuntimeBar, compile_strategy
from .strategy_ir import GroupMode, StrategySpecV2


@dataclass(frozen=True, slots=True)
class ClauseActivation:
    group_index: int
    clause_index: int
    feature: str
    operator: str
    threshold: object
    available_count: int
    true_count: int
    observed_min: float | None
    observed_max: float | None

    @property
    def dead(self) -> bool:
        return self.available_count > 0 and self.true_count == 0


@dataclass(frozen=True, slots=True)
class ReconstructionSemanticAssessment:
    total_rows: int
    session_eligible_rows: int
    entry_match_count: int
    clauses: tuple[ClauseActivation, ...]
    reason: str
    status: str

    @property
    def valid(self) -> bool:
        return self.status == "activatable"


def assess_reconstruction_semantics(
    spec: StrategySpecV2,
    runtime_bars: Iterable[RuntimeBar],
) -> ReconstructionSemanticAssessment:
    """Measure entry-rule activation without trading or changing strategy state.

    Runtime bars must already carry any required session labels. Event-filtered
    reconstructions are intentionally refused because their activation depends on
    separate point-in-time event evidence.
    """

    if spec.event_exclusion_minutes:
        raise ValueError("semantic preflight requires eventless strategy or explicit event binding")

    rows = tuple(runtime_bars)
    compiled = compile_strategy(spec)
    allowed_sessions = {value.upper() for value in spec.session_filters}
    eligible = tuple(
        row for row in rows
        if not allowed_sessions or row.session.upper() in allowed_sessions
    )

    clause_rows: list[ClauseActivation] = []
    for group_index, group in enumerate(spec.entry_groups):
        for clause_index, clause in enumerate(group.clauses):
            available = 0
            true_count = 0
            numerics: list[float] = []
            for row in eligible:
                features = row.feature_map()
                if clause.feature not in features:
                    continue
                available += 1
                actual = features[clause.feature]
                if isinstance(actual, (int, float)) and not isinstance(actual, bool):
                    numerics.append(float(actual))
                if clause.evaluate(features):
                    true_count += 1
            clause_rows.append(
                ClauseActivation(
                    group_index,
                    clause_index,
                    clause.feature,
                    clause.op.value,
                    clause.value,
                    available,
                    true_count,
                    min(numerics) if numerics else None,
                    max(numerics) if numerics else None,
                )
            )

    entry_matches = sum(
        1 for row in eligible
        if compiled.entry_matches(row.feature_map(), session=row.session, event_blocked=False)
    )

    if not rows:
        status, reason = "insufficient", "no runtime rows supplied"
    elif not eligible:
        status, reason = "insufficient", "no rows satisfy declared session filters"
    elif entry_matches == 0:
        status = "dead_reconstruction"
        dead = [
            f"g{row.group_index}.c{row.clause_index}:{row.feature}:{row.operator}:{row.threshold}"
            for row in clause_rows if row.dead
        ]
        if dead:
            reason = "entry logic never activates; dead clauses=" + ",".join(dead)
        else:
            reason = "entry logic never activates although individual clauses may activate separately"
    else:
        status, reason = "activatable", "entry logic activates on bounded PIT sample"

    return ReconstructionSemanticAssessment(
        len(rows),
        len(eligible),
        entry_matches,
        tuple(clause_rows),
        reason,
        status,
    )


def dead_hypothesis_clause_coordinates(
    assessment: ReconstructionSemanticAssessment,
) -> tuple[tuple[int, int], ...]:
    """Return only empirically impossible clause coordinates; never mutate them."""

    return tuple(
        (row.group_index, row.clause_index)
        for row in assessment.clauses
        if row.dead
    )
