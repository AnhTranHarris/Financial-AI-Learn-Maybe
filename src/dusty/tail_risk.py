from __future__ import annotations

"""M172 deterministic tail-risk analysis for research return paths."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


class TailRiskStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    MEASURED = "measured"


@dataclass(frozen=True, slots=True)
class TailRiskPolicy:
    minimum_observations: int = 30
    confidence: float = 0.95

    def __post_init__(self) -> None:
        if not 2 <= int(self.minimum_observations) <= 10_000_000:
            raise ValueError("minimum_observations out of range")
        confidence = float(self.confidence)
        if not math.isfinite(confidence) or not 0.50 <= confidence < 1.0:
            raise ValueError("confidence must be finite in [0.50,1)")


@dataclass(frozen=True, slots=True)
class TailRiskReport:
    status: TailRiskStatus
    observation_count: int
    confidence: float
    max_drawdown: float | None
    value_at_risk: float | None
    conditional_value_at_risk: float | None
    worst_single_return: float | None
    max_consecutive_losses: int | None
    terminal_compound_return: float | None
    reason: str

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("tail-risk report reason required")
        metrics = (
            self.max_drawdown,
            self.value_at_risk,
            self.conditional_value_at_risk,
            self.worst_single_return,
            self.max_consecutive_losses,
            self.terminal_compound_return,
        )
        if self.status is TailRiskStatus.MEASURED and any(value is None for value in metrics):
            raise ValueError("measured tail-risk report requires all metrics")
        if self.status is TailRiskStatus.INSUFFICIENT and any(value is not None for value in metrics):
            raise ValueError("insufficient tail-risk report cannot expose inferred metrics")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m172-tail-risk-v1",
                "status": self.status.value,
                "observation_count": self.observation_count,
                "confidence": self.confidence,
                "max_drawdown": self.max_drawdown,
                "value_at_risk": self.value_at_risk,
                "conditional_value_at_risk": self.conditional_value_at_risk,
                "worst_single_return": self.worst_single_return,
                "max_consecutive_losses": self.max_consecutive_losses,
                "terminal_compound_return": self.terminal_compound_return,
                "reason": self.reason,
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


def _quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def analyze_tail_risk(
    returns: Iterable[float],
    *,
    policy: TailRiskPolicy = TailRiskPolicy(),
) -> TailRiskReport:
    rows = tuple(float(value) for value in returns)
    if any(not math.isfinite(value) or value <= -1.0 for value in rows):
        raise ValueError("tail-risk returns must be finite and greater than -1")
    if len(rows) < policy.minimum_observations:
        return TailRiskReport(
            TailRiskStatus.INSUFFICIENT,
            len(rows),
            policy.confidence,
            None, None, None, None, None, None,
            "insufficient observations for tail-risk measurement",
        )

    equity = 1.0
    peak = 1.0
    max_drawdown = 0.0
    loss_streak = 0
    max_loss_streak = 0
    for value in rows:
        equity *= 1.0 + value
        peak = max(peak, equity)
        drawdown = 0.0 if peak <= 0 else (peak - equity) / peak
        max_drawdown = max(max_drawdown, drawdown)
        if value < 0:
            loss_streak += 1
            max_loss_streak = max(max_loss_streak, loss_streak)
        else:
            loss_streak = 0

    losses = [-value for value in rows]
    var = _quantile(losses, policy.confidence)
    tail = [loss for loss in losses if loss >= var]
    cvar = sum(tail) / len(tail)
    return TailRiskReport(
        TailRiskStatus.MEASURED,
        len(rows),
        policy.confidence,
        max_drawdown,
        var,
        cvar,
        min(rows),
        max_loss_streak,
        equity - 1.0,
        "tail risk measured from complete return path",
    )
