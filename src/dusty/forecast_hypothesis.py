from __future__ import annotations

import json
import math
from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ParameterBound:
    name: str
    lower: float
    upper: float

    def __post_init__(self) -> None:
        if not self.name.strip() or not all(math.isfinite(value) for value in (self.lower, self.upper)) or self.lower > self.upper:
            raise ValueError("forecast parameter bound is invalid")


@dataclass(frozen=True, slots=True)
class ForecastHypothesis:
    hypothesis_id: str
    parent_fingerprint: str | None
    model_artifact_hash: str
    feature_contract_hash: str
    parameters: tuple[tuple[str, float], ...]
    bounds: tuple[ParameterBound, ...]
    rationale: str
    source_refs: tuple[str, ...] = ()
    external_code_quarantined: bool = True

    def __post_init__(self) -> None:
        if not self.hypothesis_id.strip() or not self.rationale.strip():
            raise ValueError("forecast hypothesis identity/rationale is required")
        if any(len(value) != 64 for value in (self.model_artifact_hash, self.feature_contract_hash)):
            raise ValueError("forecast hypothesis artifacts require SHA-256 identity")
        if self.parent_fingerprint is not None and len(self.parent_fingerprint) != 64:
            raise ValueError("forecast hypothesis parent fingerprint is invalid")
        values = dict(self.parameters)
        if len(values) != len(self.parameters) or any(not key.strip() or not math.isfinite(value) for key, value in self.parameters):
            raise ValueError("forecast hypothesis parameters are invalid")
        bound_map = {row.name: row for row in self.bounds}
        if len(bound_map) != len(self.bounds) or set(values) != set(bound_map):
            raise ValueError("every forecast parameter requires one bound")
        if any(not bound_map[key].lower <= value <= bound_map[key].upper for key, value in values.items()):
            raise ValueError("forecast parameter lies outside declared bound")
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("forecast hypothesis sources must be unique")

    @property
    def fingerprint(self) -> str:
        payload = {
            "id": self.hypothesis_id,
            "parent": self.parent_fingerprint,
            "model": self.model_artifact_hash,
            "features": self.feature_contract_hash,
            "parameters": self.parameters,
            "bounds": tuple((row.name, row.lower, row.upper) for row in self.bounds),
            "rationale": self.rationale,
            "sources": self.source_refs,
            "external_code_quarantined": self.external_code_quarantined,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def refine_hypothesis(
    parent: ForecastHypothesis,
    *,
    challenger_id: str,
    changes: Mapping[str, float],
    rationale: str,
) -> ForecastHypothesis:
    if not challenger_id.strip() or not changes or not rationale.strip():
        raise ValueError("forecast refinement requires id, changes and rationale")
    parameters = dict(parent.parameters)
    unknown = set(changes) - set(parameters)
    if unknown:
        raise ValueError(f"forecast refinement introduced undeclared parameters:{','.join(sorted(unknown))}")
    parameters.update(changes)
    return ForecastHypothesis(
        challenger_id,
        parent.fingerprint,
        parent.model_artifact_hash,
        parent.feature_contract_hash,
        tuple(sorted(parameters.items())),
        parent.bounds,
        rationale,
        parent.source_refs,
        parent.external_code_quarantined,
    )
