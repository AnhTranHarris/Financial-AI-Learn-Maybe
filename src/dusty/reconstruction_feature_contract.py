from __future__ import annotations

"""Typed model-facing feature contract for strategy reconstruction.

M156 remains the authoritative feature registry. This module adds the narrower
semantic contract required at the LLM boundary: a feature may be valid for an
experiment while still being unsafe as a free scalar predicate authored by a
model. Raw price-valued indicators, prices, broker-scale points, and volumes are
therefore excluded until they are represented by an explicitly normalized
feature. This prevents period/value and cross-market unit confusion.
"""

from dataclasses import dataclass
from enum import StrEnum
import math
import re
from typing import Iterable

from .feature_registry import FeatureFamily, standard_feature_registry
from .strategy_ir import StrategySpecV2


class ReconstructionUnit(StrEnum):
    FRACTION = "fraction"
    OSCILLATOR_0_100 = "oscillator_0_100"


@dataclass(frozen=True, slots=True)
class ReconstructionFeature:
    name: str
    unit: ReconstructionUnit
    minimum: float
    maximum: float
    meaning: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.meaning.strip():
            raise ValueError("reconstruction feature requires name and meaning")
        if not math.isfinite(self.minimum) or not math.isfinite(self.maximum) or self.minimum >= self.maximum:
            raise ValueError("reconstruction feature domain is invalid")

    @property
    def prompt_payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "unit": self.unit.value,
            "minimum": self.minimum,
            "maximum": self.maximum,
            "meaning": self.meaning,
        }


_RSI = re.compile(r"^rsi(?:_\d+)?$", re.IGNORECASE)


def reconstruction_feature(name: str) -> ReconstructionFeature:
    """Resolve one model-facing feature or fail closed.

    The M156 registry is consulted for canonical period-specific features. Legacy
    runtime alias ``rsi`` remains accepted because it is dimensionless and has the
    same 0-100 semantics. Bare/raw price indicators remain deliberately excluded.
    """

    rendered = str(name).strip().lower()
    if rendered == "return_1":
        registry = standard_feature_registry()
        definition = registry.get("return_1@v1")
        if definition.family is not FeatureFamily.RETURN or not registry.decision_eligible("return_1@v1"):
            raise RuntimeError("M156 return_1 registry semantics are not decision eligible")
        # Bound model-authored one-bar return thresholds to +/-2%. This is not a
        # profitability setting; it is a unit sanity boundary. Empirical semantic
        # preflight remains authoritative for whether a conjunction actually fires.
        return ReconstructionFeature(
            "return_1",
            ReconstructionUnit.FRACTION,
            -0.02,
            0.02,
            "fractional one-bar close return; 0.01 means +1%, not 1 basis point",
        )
    if _RSI.fullmatch(rendered):
        registry = standard_feature_registry()
        canonical = "rsi_14" if rendered == "rsi" else rendered
        definition = registry.get(f"{canonical}@v1")
        if definition.family is not FeatureFamily.MOMENTUM or not registry.decision_eligible(f"{canonical}@v1"):
            raise RuntimeError("M156 RSI registry semantics are not decision eligible")
        return ReconstructionFeature(
            rendered,
            ReconstructionUnit.OSCILLATOR_0_100,
            0.0,
            100.0,
            "RSI oscillator value on a 0-100 scale; suffix is lookback period, not indicator value",
        )
    raise ValueError(
        f"feature {rendered!r} is not safe for model-authored scalar reconstruction; "
        "use an explicitly normalized/dimensionless feature contract"
    )


def validate_model_feature_universe(names: Iterable[str]) -> tuple[ReconstructionFeature, ...]:
    rows = tuple(reconstruction_feature(name) for name in names)
    if not rows:
        raise ValueError("model-facing reconstruction feature universe cannot be empty")
    if len({row.name for row in rows}) != len(rows):
        raise ValueError("model-facing reconstruction features must be unique")
    return rows


def validate_reconstruction_spec(spec: StrategySpecV2) -> None:
    """Validate model-authored clause units after structured-output parsing."""

    for group in spec.entry_groups:
        for clause in group.clauses:
            contract = reconstruction_feature(clause.feature)
            if isinstance(clause.value, bool) or not isinstance(clause.value, (int, float)):
                raise ValueError(f"{clause.feature} reconstruction threshold must be numeric")
            value = float(clause.value)
            if not math.isfinite(value) or value < contract.minimum or value > contract.maximum:
                raise ValueError(
                    f"{clause.feature} reconstruction threshold {value!r} outside "
                    f"{contract.unit.value} domain [{contract.minimum}, {contract.maximum}]"
                )


def prompt_feature_contract(names: Iterable[str]) -> tuple[dict[str, object], ...]:
    return tuple(row.prompt_payload for row in validate_model_feature_universe(names))


broker_write_authority = False
live_write_authority = False
promotion_authority = False
risk_override_authority = False
retry_authority = False
