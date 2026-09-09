from __future__ import annotations

"""Fail-closed production evidence chain for M165 through M174.

Research-stage primitives intentionally remain reusable and authority-free.  This
module adds the production custody boundary that proves one exact M185
qualification manifest, M165 calibration, M166 plan and M167-M174 artifact set
belong to the same strategy/dataset lineage before M185 may consume them.
"""

from dataclasses import dataclass
from hashlib import sha256
import json

from .cost_torture import CostTortureAssessment
from .forward_decay import HistoricalForwardDecay
from .m166_production_admission import M166ProductionAdmission
from .m185_production_qualification import ProductionQualificationManifest
from .parameter_stability import NeighborhoodAssessment
from .purged_validation import PurgedTemporalSplit
from .regime_torture import RegimeTortureAssessment
from .robustness_gate import RobustnessCertification, RobustnessGateStatus
from .strategy_dependency import StrategyDependencyMatrix
from .tail_risk import TailRiskReport
from .walk_forward_lab import WalkForwardPlan, WalkForwardSummary


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _cost_fingerprint(cost: CostTortureAssessment) -> str:
    return _digest(
        {
            "protocol": "dusty-m170-cost-torture-assessment-v1",
            "calibration_fingerprint": cost.calibration_fingerprint,
            "scenario_count": cost.scenario_count,
            "pass_fraction": cost.pass_fraction,
            "passed": cost.passed,
            "worst_net_return": cost.worst_net_return,
            "worst_max_drawdown": cost.worst_max_drawdown,
        }
    )


@dataclass(frozen=True, slots=True)
class M174ProductionEvidenceChain:
    qualification_manifest_fingerprint: str
    strategy_fingerprint: str
    dataset_fingerprint: str
    m166_admission_fingerprint: str
    calibration_fingerprint: str
    walk_forward_plan_fingerprint: str
    walk_forward_summary_plan_fingerprint: str
    purged_split_fingerprints: tuple[str, ...]
    parameter_stability_fingerprint: str
    regime_torture_fingerprint: str
    cost_torture_fingerprint: str
    forward_decay_fingerprint: str
    tail_risk_fingerprint: str
    strategy_dependency_fingerprint: str
    robustness_fingerprint: str

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    promotion_authority = False
    risk_override_authority = False

    def __post_init__(self) -> None:
        for name in (
            "qualification_manifest_fingerprint",
            "strategy_fingerprint",
            "dataset_fingerprint",
            "m166_admission_fingerprint",
            "calibration_fingerprint",
            "walk_forward_plan_fingerprint",
            "walk_forward_summary_plan_fingerprint",
            "parameter_stability_fingerprint",
            "regime_torture_fingerprint",
            "cost_torture_fingerprint",
            "forward_decay_fingerprint",
            "tail_risk_fingerprint",
            "strategy_dependency_fingerprint",
            "robustness_fingerprint",
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        splits = tuple(_sha(value, "M167 purged split") for value in self.purged_split_fingerprints)
        if not splits or len(splits) != len(set(splits)):
            raise ValueError("M174 production chain requires unique M167 split identities")
        object.__setattr__(self, "purged_split_fingerprints", splits)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m174-production-evidence-chain-v1",
            "qualification_manifest_fingerprint": self.qualification_manifest_fingerprint,
            "strategy_fingerprint": self.strategy_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "m166_admission_fingerprint": self.m166_admission_fingerprint,
            "m165_calibration_fingerprint": self.calibration_fingerprint,
            "m166_walk_forward_plan_fingerprint": self.walk_forward_plan_fingerprint,
            "m166_walk_forward_summary_plan_fingerprint": self.walk_forward_summary_plan_fingerprint,
            "m167_purged_split_fingerprints": list(self.purged_split_fingerprints),
            "m168_parameter_stability_fingerprint": self.parameter_stability_fingerprint,
            "m169_regime_torture_fingerprint": self.regime_torture_fingerprint,
            "m170_cost_torture_fingerprint": self.cost_torture_fingerprint,
            "m171_forward_decay_fingerprint": self.forward_decay_fingerprint,
            "m172_tail_risk_fingerprint": self.tail_risk_fingerprint,
            "m173_strategy_dependency_fingerprint": self.strategy_dependency_fingerprint,
            "m174_robustness_fingerprint": self.robustness_fingerprint,
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


def certify_production_robustness_chain(
    *,
    manifest: ProductionQualificationManifest,
    admission: M166ProductionAdmission,
    plan: WalkForwardPlan,
    walk_forward: WalkForwardSummary,
    purged_splits: tuple[PurgedTemporalSplit, ...],
    purged_dataset_fingerprint: str,
    neighborhood: NeighborhoodAssessment,
    regime: RegimeTortureAssessment,
    regime_strategy_fingerprint: str,
    cost: CostTortureAssessment,
    decay: HistoricalForwardDecay,
    tail: TailRiskReport,
    tail_strategy_fingerprint: str,
    dependency: StrategyDependencyMatrix,
    robustness: RobustnessCertification,
) -> M174ProductionEvidenceChain:
    strategy = _sha(plan.strategy_execution_fingerprint, "M166 strategy")
    dataset = _sha(plan.dataset_fingerprint, "M166 dataset")
    if manifest.strategy_hash != strategy:
        raise ValueError("M185 qualification strategy does not match M166 production strategy")
    if manifest.symbol != admission.symbol:
        raise ValueError("M185 qualification symbol does not match M166 admission")
    if admission.walk_forward_plan_fingerprint != plan.fingerprint:
        raise ValueError("M166 admission/plan identity drift")
    if admission.calibration_fingerprint != cost.calibration_fingerprint:
        raise ValueError("M170 cost assessment does not use admitted M165 calibration")
    if walk_forward.plan_fingerprint != plan.fingerprint:
        raise ValueError("M166 walk-forward summary does not match admitted plan")

    if _sha(purged_dataset_fingerprint, "M167 dataset provenance") != dataset:
        raise ValueError("M167 purged validation dataset provenance drift")
    splits = tuple(purged_splits)
    if len(splits) != len(plan.windows):
        raise ValueError("M167 production evidence requires one purged split per M166 fold")
    for window, split in zip(plan.windows, splits):
        if split.test_start != window.test_start or split.test_end != window.test_end:
            raise ValueError("M167 purged split does not match M166 test window")
    split_fps = tuple(split.fingerprint for split in splits)
    if len(split_fps) != len(set(split_fps)):
        raise ValueError("M167 production split identities must be unique")

    if neighborhood.center_parameter_fingerprint != plan.parameter_fingerprint:
        raise ValueError("M168 center parameter does not match M166 plan")
    if _sha(regime_strategy_fingerprint, "M169 strategy provenance") != strategy:
        raise ValueError("M169 regime evidence strategy provenance drift")
    if decay.strategy_fingerprint != strategy:
        raise ValueError("M171 forward-decay strategy does not match M166 plan")
    if _sha(tail_strategy_fingerprint, "M172 strategy provenance") != strategy:
        raise ValueError("M172 tail-risk strategy provenance drift")
    if strategy not in dependency.strategy_fingerprints:
        raise ValueError("M173 dependency evidence omits the production strategy")

    if robustness.status is not RobustnessGateStatus.SERIOUS_CHALLENGER or robustness.blockers:
        raise PermissionError("production chain requires M174 serious-challenger evidence")
    expected_m174 = {
        admission.calibration_fingerprint,
        plan.fingerprint,
        neighborhood.fingerprint,
        regime.fingerprint,
        cost.calibration_fingerprint,
        decay.fingerprint,
        tail.fingerprint,
        dependency.fingerprint,
    }
    actual_m174 = set(robustness.evidence_fingerprints)
    if actual_m174 != expected_m174:
        raise ValueError("M174 robustness evidence does not match supplied production artifacts")
    if not cost.passed:
        raise PermissionError("M170 cost torture must pass before production chain certification")

    return M174ProductionEvidenceChain(
        qualification_manifest_fingerprint=manifest.fingerprint,
        strategy_fingerprint=strategy,
        dataset_fingerprint=dataset,
        m166_admission_fingerprint=admission.fingerprint,
        calibration_fingerprint=admission.calibration_fingerprint,
        walk_forward_plan_fingerprint=plan.fingerprint,
        walk_forward_summary_plan_fingerprint=walk_forward.plan_fingerprint,
        purged_split_fingerprints=split_fps,
        parameter_stability_fingerprint=neighborhood.fingerprint,
        regime_torture_fingerprint=regime.fingerprint,
        cost_torture_fingerprint=_cost_fingerprint(cost),
        forward_decay_fingerprint=decay.fingerprint,
        tail_risk_fingerprint=tail.fingerprint,
        strategy_dependency_fingerprint=dependency.fingerprint,
        robustness_fingerprint=robustness.fingerprint,
    )
