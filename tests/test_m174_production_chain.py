from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.broker_calibration import BrokerEconomicsCalibration, CalibrationStatus
from dusty.cost_torture import CostTortureAssessment
from dusty.forward_decay import DecayStatus, HistoricalForwardDecay
from dusty.m166_production_admission import admit_production_walk_forward
from dusty.m174_production_chain import certify_production_robustness_chain
from dusty.m185_production_qualification import ProductionQualificationManifest, QUALIFICATION_STAGES
from dusty.parameter_stability import NeighborhoodAssessment, NeighborhoodStatus
from dusty.purged_validation import PurgedTemporalSplit
from dusty.regime_torture import RegimeTortureAssessment, RegimeTortureStatus
from dusty.robustness_gate import RobustnessCertificationPolicy, certify_robustness
from dusty.strategy_dependency import DependencyStatus, StrategyDependencyMatrix
from dusty.tail_risk import TailRiskReport, TailRiskStatus
from dusty.walk_forward_lab import WalkForwardMode, WalkForwardPlan, WalkForwardSummary, WalkForwardWindow


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M174ProductionEvidenceChainTests(unittest.TestCase):
    def fixtures(self):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        strategy = fp("strategy")
        dataset = fp("dataset")
        parameter = fp("parameter")
        window = WalkForwardWindow(1, when, when + timedelta(days=30), when + timedelta(days=30), when + timedelta(days=37))
        plan = WalkForwardPlan(strategy, parameter, dataset, WalkForwardMode.ANCHORED, (window,))
        calibration = BrokerEconomicsCalibration(
            CalibrationStatus.CALIBRATED,
            fp("broker"),
            "EURUSD",
            30,
            3,
            tuple(fp(f"obs-{i}") for i in range(30)),
            1.0, 2.0, 3.0,
            0.0, 0.5, 1.0,
            1.0, 1.0, 0.0,
            "calibrated",
        )
        admission = admit_production_walk_forward(calibration=calibration, plan=plan, expected_symbol="EURUSD")
        family = fp("family")
        manifest = ProductionQualificationManifest(
            source_commit="1" * 40,
            estate_sha256=fp("estate"),
            reconstruction_fingerprint=fp("reconstruction"),
            proposal_fingerprint=fp("proposal"),
            strategy_hash=strategy,
            source_id="unit",
            source_url="https://example.test/strategy",
            source_content_sha256=fp("source"),
            source_family_fingerprint=family,
            title="Unit strategy",
            symbol="EURUSD",
            timeframe="M15",
            lane_id=f"eurusd:m15:{family[:16]}",
            source_claim_complete=True,
            hypothesis_rule_count=3,
            required_stages=QUALIFICATION_STAGES,
            created_at=when,
        )
        walk = WalkForwardSummary(plan.fingerprint, 1, 1, 1.0, 0.02, 0.02, 0.03, 40)
        split = PurgedTemporalSplit(window.test_start, window.test_end, 0, (), (), (), ())
        neighborhood = NeighborhoodAssessment(parameter, NeighborhoodStatus.STABLE, 4, 4, 1.0, 1.0, 0.9, 0.8, 0.2, "stable")
        regime = RegimeTortureAssessment(
            RegimeTortureStatus.PASSED,
            window.test_end,
            3, 3, 1.0, -0.01, 0.05,
            (fp("r1"), fp("r2"), fp("r3")),
            "passed",
        )
        cost = CostTortureAssessment(calibration.fingerprint, 4, 1.0, True, -0.02, 0.06)
        decay = HistoricalForwardDecay(
            DecayStatus.MEASURED,
            strategy,
            fp("historical"),
            fp("forward"),
            2.0, 1.5, 0.75, 0.25, 40,
            "measured",
        )
        tail = TailRiskReport(TailRiskStatus.MEASURED, 100, 0.95, 0.10, 0.04, 0.07, -0.08, 3, 0.12, "measured")
        dependency = StrategyDependencyMatrix(
            DependencyStatus.DIVERSIFIED,
            tuple(sorted((strategy, fp("peer")))),
            100,
            (), 0.2, 0.3,
            "diversified",
        )
        robustness = certify_robustness(
            calibration=calibration,
            walk_forward=walk,
            neighborhood=neighborhood,
            regime=regime,
            cost=cost,
            decay=decay,
            tail=tail,
            dependency=dependency,
            policy=RobustnessCertificationPolicy(0.75, 0.50, 0.20, 0.10),
        )
        return manifest, admission, plan, walk, split, neighborhood, regime, cost, decay, tail, dependency, robustness

    def build(self, **overrides):
        manifest, admission, plan, walk, split, neighborhood, regime, cost, decay, tail, dependency, robustness = self.fixtures()
        values = dict(
            manifest=manifest,
            admission=admission,
            plan=plan,
            walk_forward=walk,
            purged_splits=(split,),
            purged_dataset_fingerprint=plan.dataset_fingerprint,
            neighborhood=neighborhood,
            regime=regime,
            regime_strategy_fingerprint=plan.strategy_execution_fingerprint,
            cost=cost,
            decay=decay,
            tail=tail,
            tail_strategy_fingerprint=plan.strategy_execution_fingerprint,
            dependency=dependency,
            robustness=robustness,
        )
        values.update(overrides)
        return certify_production_robustness_chain(**values)

    def test_complete_chain_is_content_addressed_and_authority_free(self):
        chain = self.build()
        self.assertEqual(len(chain.fingerprint), 64)
        self.assertFalse(chain.broker_write_authority)
        self.assertFalse(chain.live_write_authority)
        self.assertFalse(chain.retry_authority)
        self.assertFalse(chain.promotion_authority)
        self.assertFalse(chain.risk_override_authority)

    def test_rejects_m167_wrong_dataset_or_test_window(self):
        with self.assertRaisesRegex(ValueError, "M167 purged validation dataset"):
            self.build(purged_dataset_fingerprint=fp("wrong-dataset"))
        *_, robustness = self.fixtures()
        manifest, admission, plan, walk, split, neighborhood, regime, cost, decay, tail, dependency, _ = self.fixtures()
        wrong = PurgedTemporalSplit(split.test_start + timedelta(days=1), split.test_end + timedelta(days=1), 0, (), (), (), ())
        with self.assertRaisesRegex(ValueError, "M167 purged split"):
            self.build(purged_splits=(wrong,))

    def test_rejects_m168_m169_m171_m172_or_m173_lineage_drift(self):
        manifest, admission, plan, walk, split, neighborhood, regime, cost, decay, tail, dependency, robustness = self.fixtures()
        wrong_neighborhood = NeighborhoodAssessment(fp("wrong-param"), NeighborhoodStatus.STABLE, 4, 4, 1.0, 1.0, 0.9, 0.8, 0.2, "stable")
        with self.assertRaisesRegex(ValueError, "M168 center"):
            self.build(neighborhood=wrong_neighborhood)
        with self.assertRaisesRegex(ValueError, "M169 regime"):
            self.build(regime_strategy_fingerprint=fp("wrong-strategy"))
        wrong_decay = HistoricalForwardDecay(DecayStatus.MEASURED, fp("wrong-strategy"), fp("h2"), fp("f2"), 2.0, 1.5, 0.75, 0.25, 40, "measured")
        with self.assertRaisesRegex(ValueError, "M171 forward-decay"):
            self.build(decay=wrong_decay)
        with self.assertRaisesRegex(ValueError, "M172 tail-risk"):
            self.build(tail_strategy_fingerprint=fp("wrong-strategy"))
        wrong_dep = StrategyDependencyMatrix(DependencyStatus.DIVERSIFIED, tuple(sorted((fp("other1"), fp("other2")))), 100, (), 0.2, 0.3, "diversified")
        with self.assertRaisesRegex(ValueError, "M173 dependency"):
            self.build(dependency=wrong_dep)

    def test_rejects_cost_or_m174_artifact_substitution(self):
        manifest, admission, plan, walk, split, neighborhood, regime, cost, decay, tail, dependency, robustness = self.fixtures()
        bad_cost = CostTortureAssessment(admission.calibration_fingerprint, 4, 0.5, False, -0.05, 0.20)
        with self.assertRaises(PermissionError):
            self.build(cost=bad_cost)
        altered_tail = TailRiskReport(TailRiskStatus.MEASURED, 100, 0.95, 0.11, 0.04, 0.07, -0.08, 3, 0.12, "measured")
        with self.assertRaisesRegex(ValueError, "M174 robustness evidence"):
            self.build(tail=altered_tail)


if __name__ == "__main__":
    unittest.main()
