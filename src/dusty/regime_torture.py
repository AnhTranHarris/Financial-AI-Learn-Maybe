from __future__ import annotations

"""M169 point-in-time regime torture assessment."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


class RegimeTortureStatus(StrEnum):
    INSUFFICIENT = "insufficient"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RegimeDefinition:
    name: str
    definition_fingerprint: str
    available_at: datetime

    def __post_init__(self) -> None:
        name = str(self.name).strip().lower()
        if not name or "\n" in name or "\r" in name:
            raise ValueError("regime name must be one line")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "definition_fingerprint", _sha(self.definition_fingerprint, "regime definition"))
        object.__setattr__(self, "available_at", _aware(self.available_at, "regime available_at"))

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "name": self.name,
                "definition_fingerprint": self.definition_fingerprint,
                "available_at": self.available_at.isoformat(),
            }
        )


@dataclass(frozen=True, slots=True)
class RegimeSliceResult:
    regime_definition_fingerprint: str
    sample_count: int
    net_return: float
    max_drawdown: float
    passed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "regime_definition_fingerprint", _sha(self.regime_definition_fingerprint, "regime result"))
        if isinstance(self.sample_count, bool) or int(self.sample_count) != self.sample_count or int(self.sample_count) < 0:
            raise ValueError("regime sample_count must be nonnegative")
        object.__setattr__(self, "sample_count", int(self.sample_count))
        object.__setattr__(self, "net_return", _finite(self.net_return, "regime net_return"))
        drawdown = _finite(self.max_drawdown, "regime max_drawdown")
        if drawdown < 0:
            raise ValueError("regime max_drawdown cannot be negative")
        object.__setattr__(self, "max_drawdown", drawdown)


@dataclass(frozen=True, slots=True)
class RegimeTorturePolicy:
    minimum_regimes: int = 3
    minimum_samples_per_regime: int = 20
    minimum_pass_fraction: float = 0.75

    def __post_init__(self) -> None:
        if not 1 <= int(self.minimum_regimes) <= 1000:
            raise ValueError("minimum_regimes out of range")
        if not 1 <= int(self.minimum_samples_per_regime) <= 1_000_000:
            raise ValueError("minimum_samples_per_regime out of range")
        fraction = _finite(self.minimum_pass_fraction, "minimum_pass_fraction")
        if not 0.0 <= fraction <= 1.0:
            raise ValueError("minimum_pass_fraction must be in [0,1]")


@dataclass(frozen=True, slots=True)
class RegimeTortureAssessment:
    status: RegimeTortureStatus
    evaluation_cutoff: datetime
    regime_count: int
    adequately_sampled_regimes: int
    pass_fraction: float
    worst_net_return: float | None
    worst_max_drawdown: float | None
    definition_fingerprints: tuple[str, ...]
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evaluation_cutoff", _aware(self.evaluation_cutoff, "regime evaluation cutoff"))
        object.__setattr__(self, "definition_fingerprints", tuple(sorted(_sha(value, "regime assessment definition") for value in self.definition_fingerprints)))
        if not self.reason.strip():
            raise ValueError("regime assessment reason required")

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m169-regime-torture-v1",
                "status": self.status.value,
                "evaluation_cutoff": self.evaluation_cutoff.isoformat(),
                "regime_count": self.regime_count,
                "adequately_sampled_regimes": self.adequately_sampled_regimes,
                "pass_fraction": self.pass_fraction,
                "worst_net_return": self.worst_net_return,
                "worst_max_drawdown": self.worst_max_drawdown,
                "definitions": list(self.definition_fingerprints),
                "reason": self.reason,
            }
        )

    @property
    def broker_write_authority(self) -> bool:
        return False


def assess_regime_torture(
    definitions: Iterable[RegimeDefinition],
    results: Iterable[RegimeSliceResult],
    *,
    evaluation_cutoff: datetime,
    policy: RegimeTorturePolicy = RegimeTorturePolicy(),
) -> RegimeTortureAssessment:
    cutoff = _aware(evaluation_cutoff, "regime evaluation cutoff")
    defs = tuple(definitions)
    if not defs:
        raise ValueError("regime torture requires explicit definitions")
    names = tuple(row.name for row in defs)
    fps = tuple(row.definition_fingerprint for row in defs)
    if len(names) != len(set(names)) or len(fps) != len(set(fps)):
        raise ValueError("regime definitions must be unique")
    if any(row.available_at > cutoff for row in defs):
        raise ValueError("regime definition is future knowledge at evaluation cutoff")

    rows = tuple(results)
    by_fp = {row.regime_definition_fingerprint: row for row in rows}
    if len(by_fp) != len(rows):
        raise ValueError("regime results contain duplicate definitions")
    unknown = set(by_fp) - set(fps)
    if unknown:
        raise ValueError("regime result references undeclared definition")
    if set(by_fp) != set(fps):
        return RegimeTortureAssessment(
            RegimeTortureStatus.INSUFFICIENT,
            cutoff,
            len(defs),
            0,
            0.0,
            None,
            None,
            fps,
            "one or more declared regimes have no result",
        )

    ordered = tuple(by_fp[row.definition_fingerprint] for row in defs)
    adequate = tuple(row for row in ordered if row.sample_count >= policy.minimum_samples_per_regime)
    if len(defs) < policy.minimum_regimes or len(adequate) < policy.minimum_regimes or len(adequate) != len(defs):
        return RegimeTortureAssessment(
            RegimeTortureStatus.INSUFFICIENT,
            cutoff,
            len(defs),
            len(adequate),
            0.0 if not adequate else sum(1 for row in adequate if row.passed) / len(adequate),
            None if not adequate else min(row.net_return for row in adequate),
            None if not adequate else max(row.max_drawdown for row in adequate),
            fps,
            "regime coverage/sample depth is insufficient",
        )
    pass_fraction = sum(1 for row in adequate if row.passed) / len(adequate)
    status = RegimeTortureStatus.PASSED if pass_fraction >= policy.minimum_pass_fraction else RegimeTortureStatus.FAILED
    return RegimeTortureAssessment(
        status,
        cutoff,
        len(defs),
        len(adequate),
        pass_fraction,
        min(row.net_return for row in adequate),
        max(row.max_drawdown for row in adequate),
        fps,
        "strategy survived declared point-in-time regimes" if status is RegimeTortureStatus.PASSED else "strategy failed too many declared regimes",
    )
