from __future__ import annotations

"""M155 immutable experiment constitution and content-addressed manifest."""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .experiment_queue import ExperimentJobSpec, ExperimentResource


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = value.strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: str) -> str:
    rendered = value.strip().lower()
    if len(rendered) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError("software commit requires a Git SHA")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _clean_values(values: Iterable[str], label: str, *, upper: bool = False) -> tuple[str, ...]:
    rendered = tuple(str(value).strip() for value in values)
    if not rendered or any(not value for value in rendered):
        raise ValueError(f"{label} requires non-empty values")
    normalized = tuple(value.upper() if upper else value for value in rendered)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} values must be unique")
    return tuple(sorted(normalized))


def _pairs(values: Iterable[tuple[str, str]], label: str) -> tuple[tuple[str, str], ...]:
    rendered = tuple((str(key).strip(), str(value).strip()) for key, value in values)
    if any(not key or not value for key, value in rendered):
        raise ValueError(f"{label} key/value pairs must be non-empty")
    keys = tuple(key.lower() for key, _ in rendered)
    if len(keys) != len(set(keys)):
        raise ValueError(f"{label} keys must be unique")
    return tuple(sorted(rendered, key=lambda item: item[0].lower()))


class ManifestOrigin(StrEnum):
    USER_CARSON = "user_carson"
    VIBE = "vibe"
    EXTERNAL = "external"
    DUSTY = "dusty"


class EvaluationStage(StrEnum):
    A1 = "A1"
    A2 = "A2"
    A3 = "A3"


@dataclass(frozen=True, slots=True)
class FeatureRef:
    name: str
    version: str
    fingerprint: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("feature reference requires name/version")
        object.__setattr__(self, "fingerprint", _sha(self.fingerprint, "feature"))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "name": self.name.strip().lower(),
            "version": self.version.strip(),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ExperimentWindow:
    label: str
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValueError("experiment window label required")
        start = _aware(self.start, "experiment window start")
        end = _aware(self.end, "experiment window end")
        if end <= start:
            raise ValueError("experiment window end must follow start")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "label": self.label.strip().lower(),
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class BrokerAssumptions:
    profile_fingerprint: str
    cost_model_fingerprint: str
    account_currency: str
    initial_balance: float
    leverage: int
    execution_model: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_fingerprint", _sha(self.profile_fingerprint, "broker profile"))
        object.__setattr__(self, "cost_model_fingerprint", _sha(self.cost_model_fingerprint, "cost model"))
        if not self.account_currency.strip() or not self.execution_model.strip():
            raise ValueError("broker assumptions require currency/execution model")
        if not math.isfinite(self.initial_balance) or self.initial_balance <= 0:
            raise ValueError("broker initial balance must be positive and finite")
        if not 1 <= self.leverage <= 10000:
            raise ValueError("broker leverage out of range")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "profile_fingerprint": self.profile_fingerprint,
            "cost_model_fingerprint": self.cost_model_fingerprint,
            "account_currency": self.account_currency.strip().upper(),
            "initial_balance": float(self.initial_balance),
            "leverage": int(self.leverage),
            "execution_model": self.execution_model.strip().lower(),
        }


@dataclass(frozen=True, slots=True)
class EvaluationPlan:
    stage: EvaluationStage
    policy_fingerprint: str
    required_metrics: tuple[str, ...]
    minimum_trades: int
    walk_forward_required: bool
    cost_stress_required: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_fingerprint", _sha(self.policy_fingerprint, "evaluation policy"))
        object.__setattr__(self, "required_metrics", _clean_values(self.required_metrics, "evaluation metrics"))
        if self.minimum_trades < 1:
            raise ValueError("evaluation minimum_trades must be positive")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "stage": self.stage.value,
            "policy_fingerprint": self.policy_fingerprint,
            "required_metrics": self.required_metrics,
            "minimum_trades": self.minimum_trades,
            "walk_forward_required": self.walk_forward_required,
            "cost_stress_required": self.cost_stress_required,
        }


@dataclass(frozen=True, slots=True)
class ComputeRequest:
    resource: ExperimentResource
    max_wall_seconds: int
    max_ram_mb: int
    max_workers: int = 1
    gpu_allowed: bool = False

    def __post_init__(self) -> None:
        if not 1 <= self.max_wall_seconds <= 7 * 24 * 60 * 60:
            raise ValueError("compute max_wall_seconds out of range")
        if not 128 <= self.max_ram_mb <= 1024 * 1024:
            raise ValueError("compute max_ram_mb out of range")
        if not 1 <= self.max_workers <= 64:
            raise ValueError("compute max_workers out of range")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "resource": self.resource.value,
            "max_wall_seconds": self.max_wall_seconds,
            "max_ram_mb": self.max_ram_mb,
            "max_workers": self.max_workers,
            "gpu_allowed": self.gpu_allowed,
        }


@dataclass(frozen=True, slots=True)
class ExperimentManifest:
    experiment_id: str
    hypothesis_id: str
    hypothesis: str
    origin: ManifestOrigin
    proposal_fingerprint: str
    strategy_fingerprint: str
    variant_fingerprint: str
    context_fingerprint: str
    strategy_ancestry_fingerprints: tuple[str, ...]
    source_provenance_fingerprints: tuple[str, ...]
    parent_manifest_fingerprints: tuple[str, ...]
    software_commit: str
    dataset_fingerprint: str
    features: tuple[FeatureRef, ...]
    broker: BrokerAssumptions
    seed: int
    windows: tuple[ExperimentWindow, ...]
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    research_school: str
    fidelity: str
    evaluation: EvaluationPlan
    risk_policy_fingerprint: str
    risk_assumptions: tuple[tuple[str, str], ...]
    compute: ComputeRequest
    expected_outputs: tuple[str, ...]
    created_at: datetime
    broker_write_authority: bool = False
    risk_override_authority: bool = False
    entry_veto_authority: bool = False
    promotion_authority: bool = False

    def __post_init__(self) -> None:
        for field_name in ("experiment_id", "hypothesis_id", "hypothesis", "research_school", "fidelity"):
            if not str(getattr(self, field_name)).strip():
                raise ValueError(f"{field_name} required")
        for field_name in ("proposal_fingerprint", "strategy_fingerprint", "variant_fingerprint", "context_fingerprint"):
            object.__setattr__(self, field_name, _sha(getattr(self, field_name), field_name))
        object.__setattr__(
            self,
            "strategy_ancestry_fingerprints",
            tuple(sorted({_sha(value, "strategy ancestry") for value in self.strategy_ancestry_fingerprints})),
        )
        sources = tuple(sorted({_sha(value, "source provenance") for value in self.source_provenance_fingerprints}))
        if not sources:
            raise ValueError("source provenance requires at least one fingerprint")
        object.__setattr__(self, "source_provenance_fingerprints", sources)
        object.__setattr__(
            self,
            "parent_manifest_fingerprints",
            tuple(sorted({_sha(value, "parent manifest") for value in self.parent_manifest_fingerprints})),
        )
        object.__setattr__(self, "software_commit", _git_sha(self.software_commit))
        object.__setattr__(self, "dataset_fingerprint", _sha(self.dataset_fingerprint, "dataset"))
        object.__setattr__(self, "risk_policy_fingerprint", _sha(self.risk_policy_fingerprint, "risk policy"))
        if not 0 <= self.seed <= 2**63 - 1:
            raise ValueError("experiment seed out of range")
        if not self.features:
            raise ValueError("experiment requires feature references")
        feature_keys = tuple((row.name.strip().lower(), row.version.strip()) for row in self.features)
        if len(feature_keys) != len(set(feature_keys)):
            raise ValueError("experiment feature references must be unique by name/version")
        object.__setattr__(self, "features", tuple(sorted(self.features, key=lambda row: (row.name.lower(), row.version))))
        if not self.windows:
            raise ValueError("experiment requires at least one time window")
        window_labels = tuple(row.label.strip().lower() for row in self.windows)
        if len(window_labels) != len(set(window_labels)):
            raise ValueError("experiment window labels must be unique")
        object.__setattr__(self, "windows", tuple(sorted(self.windows, key=lambda row: (row.start, row.end, row.label.lower()))))
        object.__setattr__(self, "symbols", _clean_values(self.symbols, "symbols", upper=True))
        object.__setattr__(self, "timeframes", _clean_values(self.timeframes, "timeframes", upper=True))
        object.__setattr__(self, "risk_assumptions", _pairs(self.risk_assumptions, "risk assumptions"))
        object.__setattr__(self, "expected_outputs", _clean_values(self.expected_outputs, "expected outputs"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "manifest created_at"))
        if any((self.broker_write_authority, self.risk_override_authority, self.entry_veto_authority, self.promotion_authority)):
            raise ValueError("experiment manifest cannot receive operational trading authority")

    @property
    def execution_payload(self) -> dict[str, object]:
        """Fields that can change produced evidence; M163 can reuse this identity."""

        return {
            "strategy_fingerprint": self.strategy_fingerprint,
            "variant_fingerprint": self.variant_fingerprint,
            "context_fingerprint": self.context_fingerprint,
            "software_commit": self.software_commit,
            "dataset_fingerprint": self.dataset_fingerprint,
            "features": tuple(row.payload for row in self.features),
            "broker": self.broker.payload,
            "seed": self.seed,
            "windows": tuple(row.payload for row in self.windows),
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "research_school": self.research_school.strip().lower(),
            "fidelity": self.fidelity.strip().lower(),
            "evaluation": self.evaluation.payload,
            "risk_policy_fingerprint": self.risk_policy_fingerprint,
            "risk_assumptions": self.risk_assumptions,
            "authority": {"broker_write": False, "risk_override": False, "entry_veto": False, "promotion": False},
        }

    @property
    def execution_fingerprint(self) -> str:
        return _digest(self.execution_payload)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-experiment-manifest-v1",
            "hypothesis_id": self.hypothesis_id.strip(),
            "hypothesis": self.hypothesis.strip(),
            "origin": self.origin.value,
            "proposal_fingerprint": self.proposal_fingerprint,
            "strategy_fingerprint": self.strategy_fingerprint,
            "variant_fingerprint": self.variant_fingerprint,
            "context_fingerprint": self.context_fingerprint,
            "strategy_ancestry_fingerprints": self.strategy_ancestry_fingerprints,
            "source_provenance_fingerprints": self.source_provenance_fingerprints,
            "parent_manifest_fingerprints": self.parent_manifest_fingerprints,
            "execution": self.execution_payload,
            "compute": self.compute.payload,
            "expected_outputs": self.expected_outputs,
        }

    @property
    def fingerprint(self) -> str:
        """Scientific contract identity; display ID/time do not defeat deduplication."""

        return _digest(self.payload)

    @property
    def record_fingerprint(self) -> str:
        """Immutable ledger-record identity for the later M164 artifact vault."""

        return _digest(
            {
                "manifest_fingerprint": self.fingerprint,
                "experiment_id": self.experiment_id.strip(),
                "created_at": self.created_at.isoformat(),
            }
        )

    def to_queue_spec(
        self,
        *,
        symbol: str,
        timeframe: str,
        priority: int = 0,
        max_attempts: int = 3,
    ) -> ExperimentJobSpec:
        symbol_norm = symbol.strip().upper()
        timeframe_norm = timeframe.strip().upper()
        if symbol_norm not in self.symbols or timeframe_norm not in self.timeframes:
            raise ValueError("queue binding symbol/timeframe not declared by manifest")
        return ExperimentJobSpec(
            proposal_fingerprint=self.proposal_fingerprint,
            genome_fingerprint=self.strategy_fingerprint,
            variant_fingerprint=self.variant_fingerprint,
            context_fingerprint=self.fingerprint,
            symbol=symbol_norm,
            timeframe=timeframe_norm,
            school=self.research_school,
            fidelity=self.fidelity,
            resource=self.compute.resource,
            priority=priority,
            max_attempts=max_attempts,
        )

    def canonical_record(self) -> str:
        return _canonical(
            {
                "experiment_id": self.experiment_id.strip(),
                "created_at": self.created_at.isoformat(),
                "manifest_fingerprint": self.fingerprint,
                "execution_fingerprint": self.execution_fingerprint,
                "record_fingerprint": self.record_fingerprint,
                "payload": self.payload,
            }
        )
