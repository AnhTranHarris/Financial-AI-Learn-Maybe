from __future__ import annotations

"""Compact research brain for M115-M134.

The brain chooses what to investigate and how rigorously to test it. It cannot
place trades, weaken the Constitution, rewrite a Champion, or promote itself.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable


class ResearchSchool(StrEnum):
    A1_EDGE = "a1_edge_discovery"
    A2_PROFITABILITY = "a2_quant_profitability"
    A3_VELOCITY = "a3_profit_velocity"


@dataclass(frozen=True, slots=True)
class ResearchPrior:
    prior_id: str
    statement: str
    falsification: str
    tags: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.prior_id.strip() or not self.statement.strip() or not self.falsification.strip():
            raise ValueError("research prior is incomplete")


def human_durable_priors() -> tuple[ResearchPrior, ...]:
    """Twenty falsifiable starting priors distilled from durable trading practice.

    These are priors, not alpha claims. Dusty must test them independently.
    """

    values = (
        ("P01", "Preserving capital outranks forcing activity.", "Compare abstention versus forced-entry cohorts.", ("risk", "abstention")),
        ("P02", "Fixed fractional risk is more durable than loss-recovery sizing.", "Stress equal-signal cohorts under fixed risk versus loss-recovery sizing.", ("risk", "sizing")),
        ("P03", "Every trade thesis needs a predefined invalidation.", "Compare bounded-invalidation strategies with otherwise identical unbounded exits.", ("risk", "exit")),
        ("P04", "Widening stops after entry degrades process integrity.", "Compare frozen/tightening stops with otherwise identical stop-widening variants.", ("risk", "discipline")),
        ("P05", "A valid thesis does not imply that action is valid now.", "Compare immediate entries with independently defined readiness timing.", ("timing", "patience")),
        ("P06", "FOMO or forced entries do not constitute an edge.", "Measure setups entered outside declared readiness rules.", ("timing", "discipline")),
        ("P07", "Overtrading and revenge behavior do not create expectancy.", "Measure incremental entries after losses and high-frequency bursts.", ("frequency", "discipline")),
        ("P08", "A measured strategy definition should stay fixed through its evaluation window.", "Compare frozen specifications with mid-sample rule changes.", ("reproducibility", "overfit")),
        ("P09", "Most edges are regime-dependent rather than universal.", "Stratify OOS expectancy by trend/range/volatility regimes.", ("regime",)),
        ("P10", "Session and time-of-day can materially change an edge.", "Stratify the same setup by broker/session clock.", ("session", "timing")),
        ("P11", "Stops should respect current volatility rather than arbitrary distance alone.", "Compare volatility-normalized and fixed-distance invalidation.", ("volatility", "risk")),
        ("P12", "Macro events can invalidate stale forecasts and normal execution assumptions.", "Compare pre/post-event forecast error and cost distributions.", ("events", "forecast")),
        ("P13", "A durable edge must survive realistic costs, slippage, and ordinary delay.", "Stress spreads, commissions, slippage, and latency.", ("cost", "execution")),
        ("P14", "Sample size and regime distribution matter more than headline return.", "Compare equal-return candidates with different sample/regime breadth.", ("statistics", "sample")),
        ("P15", "Out-of-sample and walk-forward evidence outrank in-sample optimization.", "Compare IS rankings with purged walk-forward transfer.", ("oos", "walk_forward")),
        ("P16", "Stable parameter neighborhoods are more credible than isolated optima.", "Perturb parameters around the selected point and measure degradation.", ("robustness", "parameters")),
        ("P17", "Backtest performance should be empirically haircut by observed forward decay.", "Learn backtest-to-forward transfer by strategy family.", ("forward", "calibration")),
        ("P18", "Expectancy and payoff distribution matter more than win rate alone.", "Compare strategies with equal win rate but different payoff distributions.", ("expectancy", "payoff")),
        ("P19", "High win rate plus rare very large losses is a tail-risk warning.", "Measure loss concentration, expected shortfall, and drawdown clustering.", ("tail_risk", "win_rate")),
        ("P20", "Diversification requires different failure modes, not cosmetic strategy variants.", "Cluster losses/exposures and test portfolio drawdown under common shocks.", ("portfolio", "correlation")),
    )
    return tuple(ResearchPrior(pid, statement, falsification, tags) for pid, statement, falsification, tags in values)


@dataclass(frozen=True, slots=True)
class ResearchMandate:
    min_a1_samples: int = 50
    max_a2_drawdown_fraction: float = 0.20
    min_walk_forward_efficiency: float = 0.50
    min_forward_samples: int = 20
    max_entries_per_hour: float = 3.0

    def __post_init__(self) -> None:
        if self.min_a1_samples < 1 or self.min_forward_samples < 1:
            raise ValueError("research sample floors must be positive")
        if not 0 <= self.max_a2_drawdown_fraction < 1:
            raise ValueError("maximum drawdown must lie in [0,1)")
        if self.min_walk_forward_efficiency < 0 or self.max_entries_per_hour <= 0:
            raise ValueError("research mandate thresholds invalid")


@dataclass(frozen=True, slots=True)
class ResearchMetrics:
    sample_count: int
    oos_expectancy: float
    cost_stress_expectancy: float
    max_drawdown_fraction: float
    walk_forward_efficiency: float
    parameter_stable: bool
    constitution_compliant: bool
    forward_sample_count: int = 0
    forward_expectancy: float = 0.0
    entries_per_hour: float = 0.0
    resource_seconds: float = 0.0

    def __post_init__(self) -> None:
        numeric = (
            self.oos_expectancy,
            self.cost_stress_expectancy,
            self.max_drawdown_fraction,
            self.walk_forward_efficiency,
            self.forward_expectancy,
            self.entries_per_hour,
            self.resource_seconds,
        )
        if self.sample_count < 0 or self.forward_sample_count < 0:
            raise ValueError("sample counts cannot be negative")
        if any(not math.isfinite(value) for value in numeric):
            raise ValueError("research metrics must be finite")
        if self.max_drawdown_fraction < 0 or self.entries_per_hour < 0 or self.resource_seconds < 0:
            raise ValueError("research metrics contain negative magnitude")


@dataclass(frozen=True, slots=True)
class SchoolDecision:
    school: ResearchSchool
    passed: bool
    reasons: tuple[str, ...]
    score: float


def evaluate_school(
    school: ResearchSchool,
    metrics: ResearchMetrics,
    mandate: ResearchMandate = ResearchMandate(),
) -> SchoolDecision:
    """Sequential research schools: later schools cannot bypass earlier proof."""

    a1_reasons: list[str] = []
    if metrics.sample_count < mandate.min_a1_samples:
        a1_reasons.append("insufficient_oos_samples")
    if metrics.oos_expectancy <= 0:
        a1_reasons.append("nonpositive_oos_expectancy")
    if not metrics.parameter_stable:
        a1_reasons.append("parameter_neighborhood_unstable")
    if not metrics.constitution_compliant:
        a1_reasons.append("constitution_failed")
    a1_pass = not a1_reasons
    if school is ResearchSchool.A1_EDGE:
        return SchoolDecision(school, a1_pass, tuple(a1_reasons), metrics.oos_expectancy)

    a2_reasons = list(a1_reasons)
    if not a1_pass:
        a2_reasons.insert(0, "a1_not_proven")
    if metrics.cost_stress_expectancy <= 0:
        a2_reasons.append("cost_stress_failed")
    if metrics.max_drawdown_fraction > mandate.max_a2_drawdown_fraction:
        a2_reasons.append("drawdown_failed")
    if metrics.walk_forward_efficiency < mandate.min_walk_forward_efficiency:
        a2_reasons.append("walk_forward_transfer_failed")
    a2_pass = not a2_reasons
    if school is ResearchSchool.A2_PROFITABILITY:
        score = metrics.cost_stress_expectancy * max(metrics.walk_forward_efficiency, 0.0)
        return SchoolDecision(school, a2_pass, tuple(a2_reasons), score)

    a3_reasons = list(a2_reasons)
    if not a2_pass:
        a3_reasons.insert(0, "a2_not_proven")
    if metrics.entries_per_hour > mandate.max_entries_per_hour:
        a3_reasons.append("entry_rate_constitution_failed")
    robust_expectancy = metrics.cost_stress_expectancy
    if metrics.forward_sample_count >= mandate.min_forward_samples:
        robust_expectancy = min(robust_expectancy, metrics.forward_expectancy)
        if metrics.forward_expectancy <= 0:
            a3_reasons.append("forward_expectancy_failed")
    if robust_expectancy <= 0:
        a3_reasons.append("robust_expectancy_failed")
    efficiency = robust_expectancy / max(metrics.resource_seconds, 1.0)
    return SchoolDecision(school, not a3_reasons, tuple(a3_reasons), efficiency)


class MutationAxis(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    HOLD = "hold"
    INDICATOR = "indicator"
    REGIME = "regime"
    SESSION = "session"
    ABSTENTION = "abstention"


@dataclass(frozen=True, slots=True)
class Mutation:
    axis: MutationAxis
    description: str

    def __post_init__(self) -> None:
        if not self.description.strip():
            raise ValueError("mutation description required")


@dataclass(frozen=True, slots=True)
class ChallengerPlan:
    parent_hash: str
    hypothesis: str
    mutations: tuple[Mutation, ...]
    champion_modified: bool = False

    def __post_init__(self) -> None:
        if len(self.parent_hash) != 64 or not self.hypothesis.strip():
            raise ValueError("challenger identity/hypothesis invalid")
        if not 1 <= len(self.mutations) <= 2:
            raise ValueError("challenger must contain one or two controlled mutations")
        if self.champion_modified:
            raise ValueError("the champion never rewrites itself")
        if len({mutation.axis for mutation in self.mutations}) != len(self.mutations):
            raise ValueError("duplicate mutation axis in one challenger")

    @property
    def fingerprint(self) -> str:
        payload = {
            "parent_hash": self.parent_hash,
            "hypothesis": self.hypothesis,
            "mutations": tuple((item.axis.value, item.description) for item in self.mutations),
            "champion_modified": self.champion_modified,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class AttributionRecord:
    subject_hash: str
    source_fingerprints: tuple[str, ...]
    experiment_fingerprints: tuple[str, ...]
    outcome: str
    lesson: str

    def __post_init__(self) -> None:
        values = (self.subject_hash, *self.source_fingerprints, *self.experiment_fingerprints)
        if any(len(value) != 64 for value in values):
            raise ValueError("attribution requires SHA-256 identities")
        if not self.outcome.strip() or not self.lesson.strip():
            raise ValueError("attribution outcome/lesson required")


def prior_ids(priors: Iterable[ResearchPrior] | None = None) -> tuple[str, ...]:
    values = human_durable_priors() if priors is None else tuple(priors)
    ids = tuple(item.prior_id for item in values)
    if len(ids) != len(set(ids)):
        raise ValueError("research prior ids must be unique")
    return ids
