from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Mapping

from .hypothesis import HypothesisDraft
from .research import ExperimentGate, ExperimentResult, screen


class DevelopmentStatus(StrEnum):
    UNTESTED = "untested"
    REJECTED = "rejected"
    PROMISING = "promising"


@dataclass(frozen=True, slots=True)
class DevelopmentLesson:
    strategy_hash: str
    status: DevelopmentStatus
    reasons: tuple[str, ...]
    parent_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DevelopmentSummary:
    tested: int
    rejected: int
    promising: int
    untested: int
    lessons: tuple[DevelopmentLesson, ...]


def evaluate_hypotheses(
    drafts: Iterable[HypothesisDraft],
    results: Mapping[str, ExperimentResult],
    gate: ExperimentGate,
) -> DevelopmentSummary:
    """Turn experiments into compact lessons rather than an ever-growing result pile."""
    lessons: list[DevelopmentLesson] = []
    tested = rejected = promising = untested = 0
    seen_lesson_keys: set[tuple[object, ...]] = set()
    for draft in sorted(drafts, key=lambda item: item.spec.strategy_hash):
        result = results.get(draft.spec.strategy_hash)
        if result is None:
            untested += 1
            lesson = DevelopmentLesson(
                draft.spec.strategy_hash,
                DevelopmentStatus.UNTESTED,
                ("missing_experiment",),
                draft.parent_hashes,
            )
        else:
            if result.strategy_hash != draft.spec.strategy_hash:
                raise ValueError("experiment result does not belong to hypothesis")
            tested += 1
            verdict = screen(result, gate)
            if verdict.passed:
                promising += 1
                lesson = DevelopmentLesson(
                    draft.spec.strategy_hash,
                    DevelopmentStatus.PROMISING,
                    (),
                    draft.parent_hashes,
                )
            else:
                rejected += 1
                lesson = DevelopmentLesson(
                    draft.spec.strategy_hash,
                    DevelopmentStatus.REJECTED,
                    verdict.reasons,
                    draft.parent_hashes,
                )
        key = (lesson.status.value, lesson.reasons, lesson.parent_hashes)
        if key not in seen_lesson_keys:
            seen_lesson_keys.add(key)
            lessons.append(lesson)
    return DevelopmentSummary(tested, rejected, promising, untested, tuple(lessons))
