from __future__ import annotations

import json
import math
import random
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean, median, pstdev
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    mean: float
    lower: float
    upper: float
    confidence: float
    sample_count: int


@dataclass(frozen=True, slots=True)
class SelectionBiasAssessment:
    passed: bool
    sample_count: int
    trial_count: int
    mean_return: float
    standard_error: float
    raw_signal_score: float
    search_penalty: float
    deflated_signal_score: float
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProfitConcentration:
    positive_total: float
    largest_winner: float
    largest_winner_fraction: float


@dataclass(frozen=True, slots=True)
class CandidateFoldScore:
    fold_id: str
    strategy_hash: str
    train_score: float
    test_score: float


@dataclass(frozen=True, slots=True)
class SelectionOverfitAssessment:
    fold_count: int
    selected_below_median_count: int
    overfit_rate: float


@dataclass(frozen=True, slots=True)
class TrialRecord:
    strategy_hash: str
    family: str
    score: float
    passed: bool
    fingerprint: str


class SQLiteTrialRegistry:
    """Append-only record of every trial, including failures, for selection-bias accounting."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._db = sqlite3.connect(str(path))
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS trials("
            "seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            "strategy_hash TEXT NOT NULL,"
            "family TEXT NOT NULL,"
            "score REAL NOT NULL,"
            "passed INTEGER NOT NULL,"
            "fingerprint TEXT NOT NULL)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_trials_family ON trials(family)")
        self._db.commit()

    def record(self, trial: TrialRecord) -> None:
        if not trial.strategy_hash.strip() or not trial.family.strip() or not trial.fingerprint.strip():
            raise ValueError("trial identity, family, and fingerprint are required")
        if not math.isfinite(trial.score):
            raise ValueError("trial score must be finite")
        with self._db:
            self._db.execute(
                "INSERT INTO trials(strategy_hash,family,score,passed,fingerprint) VALUES(?,?,?,?,?)",
                (trial.strategy_hash, trial.family, trial.score, int(trial.passed), trial.fingerprint),
            )

    def count(self, family: str | None = None) -> int:
        if family is None:
            row = self._db.execute("SELECT COUNT(*) FROM trials").fetchone()
        else:
            row = self._db.execute("SELECT COUNT(*) FROM trials WHERE family=?", (family,)).fetchone()
        return int(row[0])

    def history(self, family: str | None = None) -> tuple[TrialRecord, ...]:
        if family is None:
            rows = self._db.execute(
                "SELECT strategy_hash,family,score,passed,fingerprint FROM trials ORDER BY seq"
            )
        else:
            rows = self._db.execute(
                "SELECT strategy_hash,family,score,passed,fingerprint FROM trials WHERE family=? ORDER BY seq",
                (family,),
            )
        return tuple(
            TrialRecord(str(row[0]), str(row[1]), float(row[2]), bool(row[3]), str(row[4]))
            for row in rows
        )

    def integrity_ok(self) -> bool:
        return self._db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def close(self) -> None:
        self._db.close()


def bootstrap_mean_interval(
    values: Iterable[float],
    *,
    confidence: float = 0.95,
    resamples: int = 1000,
    seed: int = 0,
) -> ConfidenceInterval:
    rows = tuple(float(value) for value in values)
    if not rows:
        raise ValueError("bootstrap requires observations")
    if not 0.0 < confidence < 1.0 or resamples < 100:
        raise ValueError("invalid bootstrap configuration")
    if any(not math.isfinite(value) for value in rows):
        raise ValueError("bootstrap observations must be finite")
    rng = random.Random(seed)
    n = len(rows)
    means = sorted(fmean(rows[rng.randrange(n)] for _ in range(n)) for _ in range(resamples))
    tail = (1.0 - confidence) / 2.0
    low_index = max(0, min(resamples - 1, int(tail * resamples)))
    high_index = max(0, min(resamples - 1, int((1.0 - tail) * resamples) - 1))
    return ConfidenceInterval(fmean(rows), means[low_index], means[high_index], confidence, n)


def assess_selection_bias(
    returns: Iterable[float],
    *,
    trial_count: int,
    min_deflated_score: float = 0.0,
) -> SelectionBiasAssessment:
    """Conservative stdlib diagnostic inspired by multiple-testing/deflated-score research.

    This is intentionally not labeled an exact Deflated Sharpe Ratio implementation. It uses a
    standardized mean score and subtracts sqrt(2*log(number of tried variants)), making the
    burden of evidence rise as Dusty searches more alternatives. Zero-variance results are
    rejected as suspicious rather than treated as mathematically perfect.
    """
    rows = tuple(float(value) for value in returns)
    if len(rows) < 2 or trial_count < 1:
        raise ValueError("selection-bias assessment requires >=2 returns and >=1 trial")
    if any(not math.isfinite(value) for value in rows):
        raise ValueError("returns must be finite")
    mean = fmean(rows)
    sigma = pstdev(rows)
    standard_error = sigma / math.sqrt(len(rows)) if sigma > 0 else 0.0
    raw_score = mean / standard_error if standard_error > 0 else 0.0
    penalty = math.sqrt(2.0 * math.log(max(1, trial_count)))
    deflated = raw_score - penalty
    reasons: list[str] = []
    if sigma == 0:
        reasons.append("zero_variance_returns")
    if mean <= 0:
        reasons.append("non_positive_mean")
    if deflated <= min_deflated_score:
        reasons.append("search_adjusted_signal_failed")
    return SelectionBiasAssessment(
        passed=not reasons,
        sample_count=len(rows),
        trial_count=trial_count,
        mean_return=mean,
        standard_error=standard_error,
        raw_signal_score=raw_score,
        search_penalty=penalty,
        deflated_signal_score=deflated,
        reasons=tuple(reasons),
    )


def adjusted_pvalue(raw_pvalue: float, *, trial_count: int) -> float:
    """Bonferroni family-wise correction: simple, explicit, and conservative."""
    if not 0.0 <= raw_pvalue <= 1.0 or trial_count < 1:
        raise ValueError("invalid p-value or trial count")
    return min(1.0, raw_pvalue * trial_count)


def profit_concentration(returns: Iterable[float]) -> ProfitConcentration:
    positives = tuple(value for value in (float(item) for item in returns) if value > 0)
    if not positives:
        return ProfitConcentration(0.0, 0.0, 0.0)
    total = sum(positives)
    largest = max(positives)
    return ProfitConcentration(total, largest, largest / total)


def parameter_neighborhood_stable(
    scores: Iterable[float],
    *,
    max_spread: float,
    min_positive_fraction: float = 0.6,
) -> bool:
    rows = tuple(float(score) for score in scores)
    if not rows or max_spread < 0 or not 0.0 <= min_positive_fraction <= 1.0:
        raise ValueError("invalid parameter stability inputs")
    return (
        max(rows) - min(rows) <= max_spread
        and sum(score > 0 for score in rows) / len(rows) >= min_positive_fraction
    )


def estimate_selection_overfit(
    scores: Iterable[CandidateFoldScore],
) -> SelectionOverfitAssessment:
    """Bounded PBO-style proxy: did each fold's in-sample winner disappoint out of sample?"""
    by_fold: dict[str, list[CandidateFoldScore]] = {}
    for score in scores:
        by_fold.setdefault(score.fold_id, []).append(score)
    if not by_fold:
        raise ValueError("selection overfit assessment requires fold scores")
    below = 0
    for fold_id, rows in sorted(by_fold.items()):
        if len(rows) < 2:
            raise ValueError(f"fold {fold_id} requires at least two candidates")
        winner = max(rows, key=lambda row: (row.train_score, row.strategy_hash))
        test_median = median(row.test_score for row in rows)
        below += winner.test_score < test_median
    return SelectionOverfitAssessment(len(by_fold), below, below / len(by_fold))


def trial_fingerprint(trial: TrialRecord) -> str:
    """Stable payload representation for certification bundles."""
    return json.dumps(
        {
            "strategy_hash": trial.strategy_hash,
            "family": trial.family,
            "score": trial.score,
            "passed": trial.passed,
            "fingerprint": trial.fingerprint,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
