from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TrainingGateInput:
    m35_ready: bool
    symbol_curriculum_certified: bool
    curriculum_point_in_time_clean: bool
    reasoning_bridge_certified: bool
    hypothesis_falsification_ready: bool
    adaptive_acquisition_certified: bool
    regime_context_certified: bool
    mt5_read_only_worker_certified: bool
    multi_fidelity_validation_complete: bool


@dataclass(frozen=True, slots=True)
class TrainingQualification:
    ready_for_demo_execution_development: bool
    broker_write_authorized: bool
    reasons: tuple[str, ...] = ()


def qualify_training_phase(inputs: TrainingGateInput) -> TrainingQualification:
    """M45 gate: graduate research toward demo development without granting broker writes."""
    checks = {
        "m35_not_ready": inputs.m35_ready,
        "symbol_curriculum_not_certified": inputs.symbol_curriculum_certified,
        "curriculum_point_in_time_failed": inputs.curriculum_point_in_time_clean,
        "reasoning_bridge_not_certified": inputs.reasoning_bridge_certified,
        "hypothesis_falsification_not_ready": inputs.hypothesis_falsification_ready,
        "adaptive_acquisition_not_certified": inputs.adaptive_acquisition_certified,
        "regime_context_not_certified": inputs.regime_context_certified,
        "mt5_read_only_worker_not_certified": inputs.mt5_read_only_worker_certified,
        "multi_fidelity_validation_incomplete": inputs.multi_fidelity_validation_complete,
    }
    reasons = tuple(reason for reason, passed in checks.items() if not passed)
    return TrainingQualification(
        ready_for_demo_execution_development=not reasons,
        broker_write_authorized=False,
        reasons=reasons,
    )
