from __future__ import annotations

"""Production envelope for M189 Demo execution-cost learning.

Each learned sample must descend from an M188 production reconciliation tied to
one M185 production Champion custody envelope.  The mature M189 statistical core
remains unchanged and authority-free.
"""

from dataclasses import dataclass
from hashlib import sha256
import json

from .broker_calibration import BrokerCalibrationPolicy
from .demo_execution_cost_learning import (
    DemoExecutionCostLearning,
    DemoExecutionCostSample,
    learn_demo_execution_costs,
    sample_from_reconciliation,
)
from .execution_reconciliation import ExecutionReconciliation
from .m188_production_reconciliation import M188ProductionReconciliationEnvelope
from .shadow_execution import ShadowExecutionIntent


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class M189ProductionCostSampleEnvelope:
    production_custody_fingerprint: str
    m188_production_reconciliation_fingerprint: str
    reconciliation_fingerprint: str
    sample_fingerprint: str

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    position_mutation_authority = False
    promotion_authority = False
    risk_override_authority = False

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m189-production-cost-sample-v1",
            "production_custody_fingerprint": self.production_custody_fingerprint,
            "m188_production_reconciliation_fingerprint": self.m188_production_reconciliation_fingerprint,
            "reconciliation_fingerprint": self.reconciliation_fingerprint,
            "sample_fingerprint": self.sample_fingerprint,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "retry": False,
                "position_mutation": False,
                "promotion": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


@dataclass(frozen=True, slots=True)
class M189ProductionCostLearningEnvelope:
    production_custody_fingerprint: str
    sample_envelope_fingerprints: tuple[str, ...]
    learning_fingerprint: str

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    promotion_authority = False
    risk_override_authority = False

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m189-production-cost-learning-v1",
            "production_custody_fingerprint": self.production_custody_fingerprint,
            "sample_envelope_fingerprints": list(self.sample_envelope_fingerprints),
            "learning_fingerprint": self.learning_fingerprint,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "retry": False,
                "promotion": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def sample_from_production_reconciliation(
    *,
    production_reconciliation: M188ProductionReconciliationEnvelope,
    shadow: ShadowExecutionIntent,
    reconciliation: ExecutionReconciliation,
    broker_profile_fingerprint: str,
    point_size: float,
) -> tuple[M189ProductionCostSampleEnvelope, DemoExecutionCostSample]:
    if production_reconciliation.reconciliation_fingerprint != reconciliation.fingerprint:
        raise PermissionError("M189 production reconciliation/core identity drift")
    if reconciliation.shadow_fingerprint != shadow.fingerprint:
        raise PermissionError("M189 production reconciliation/shadow identity drift")
    sample = sample_from_reconciliation(
        shadow,
        reconciliation,
        broker_profile_fingerprint=broker_profile_fingerprint,
        point_size=point_size,
    )
    envelope = M189ProductionCostSampleEnvelope(
        production_custody_fingerprint=production_reconciliation.production_custody_fingerprint,
        m188_production_reconciliation_fingerprint=production_reconciliation.fingerprint,
        reconciliation_fingerprint=reconciliation.fingerprint,
        sample_fingerprint=sample.fingerprint,
    )
    return envelope, sample


def learn_production_demo_execution_costs(
    rows: tuple[tuple[M189ProductionCostSampleEnvelope, DemoExecutionCostSample, ExecutionReconciliation], ...],
    *,
    broker_profile_fingerprint: str,
    symbol: str,
    policy: BrokerCalibrationPolicy = BrokerCalibrationPolicy(),
) -> tuple[M189ProductionCostLearningEnvelope, DemoExecutionCostLearning]:
    if not rows:
        raise ValueError("production M189 learning requires production-bound samples")
    custody = {row[0].production_custody_fingerprint for row in rows}
    if len(custody) != 1:
        raise ValueError("production M189 cannot mix Champion custody identities")
    envelope_fps = tuple(row[0].fingerprint for row in rows)
    if len(envelope_fps) != len(set(envelope_fps)):
        raise ValueError("production M189 cannot contain duplicate sample envelopes")
    for envelope, sample, reconciliation in rows:
        if envelope.sample_fingerprint != sample.fingerprint:
            raise ValueError("production M189 sample envelope/sample identity drift")
        if envelope.reconciliation_fingerprint != reconciliation.fingerprint:
            raise ValueError("production M189 sample envelope/reconciliation identity drift")
        if sample.reconciliation_fingerprint != reconciliation.fingerprint:
            raise ValueError("production M189 sample/core reconciliation identity drift")
    learning = learn_demo_execution_costs(
        tuple((sample, reconciliation) for _, sample, reconciliation in rows),
        broker_profile_fingerprint=broker_profile_fingerprint,
        symbol=symbol,
        policy=policy,
    )
    result = M189ProductionCostLearningEnvelope(
        production_custody_fingerprint=next(iter(custody)),
        sample_envelope_fingerprints=tuple(sorted(envelope_fps)),
        learning_fingerprint=learning.fingerprint,
    )
    return result, learning
