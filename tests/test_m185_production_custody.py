from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import unittest

from dusty.m174_production_chain import M174ProductionEvidenceChain
from dusty.m185_production_custody import freeze_production_champion
from dusty.m185_production_qualification import ProductionQualificationManifest, QUALIFICATION_STAGES
from dusty.robustness_gate import RobustnessCertification, RobustnessGateStatus
from dusty.strategy_v3 import FrozenStrategyDeployment


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M185ProductionChampionCustodyTests(unittest.TestCase):
    def fixtures(self):
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        strategy = fp("strategy")
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
        checks = (
            ("broker_calibration", "calibrated"),
            ("walk_forward", "pass_fraction=1"),
            ("parameter_neighborhood", "stable"),
            ("regime_torture", "passed"),
            ("cost_torture", "passed=True"),
            ("historical_forward_decay", "retention=0.75"),
            ("tail_risk", "dd=0.1;cvar=0.07"),
            ("strategy_dependency", "diversified"),
        )
        robustness = RobustnessCertification(
            RobustnessGateStatus.SERIOUS_CHALLENGER,
            checks,
            (),
            tuple(fp(f"evidence-{i}") for i in range(8)),
        )
        chain = M174ProductionEvidenceChain(
            qualification_manifest_fingerprint=manifest.fingerprint,
            strategy_fingerprint=strategy,
            dataset_fingerprint=fp("dataset"),
            m166_admission_fingerprint=fp("admission"),
            calibration_fingerprint=fp("calibration"),
            walk_forward_plan_fingerprint=fp("plan"),
            walk_forward_summary_plan_fingerprint=fp("plan"),
            purged_split_fingerprints=(fp("split"),),
            parameter_stability_fingerprint=fp("parameter-stability"),
            regime_torture_fingerprint=fp("regime"),
            cost_torture_fingerprint=fp("cost"),
            forward_decay_fingerprint=fp("decay"),
            tail_risk_fingerprint=fp("tail"),
            strategy_dependency_fingerprint=fp("dependency"),
            robustness_fingerprint=robustness.fingerprint,
        )
        deployment = FrozenStrategyDeployment(strategy, fp("graph"), (fp("tool"),), "generation-1")
        return when, manifest, robustness, chain, deployment

    def test_production_envelope_binds_manifest_chain_selection_and_record(self):
        when, manifest, robustness, chain, deployment = self.fixtures()
        envelope = freeze_production_champion(
            manifest=manifest,
            chain=chain,
            strategy_family="unit-family",
            deployment=deployment,
            selection_evidence_fingerprint=fp("selection"),
            robustness=robustness,
            forecast_integration=None,
            parent_champion_fingerprint=None,
            created_at=when,
        )
        self.assertEqual(envelope.qualification_manifest_fingerprint, manifest.fingerprint)
        self.assertEqual(envelope.production_chain_fingerprint, chain.fingerprint)
        self.assertEqual(envelope.champion_record.robustness_fingerprint, robustness.fingerprint)
        self.assertEqual(envelope.champion_record.strategy_fingerprint, manifest.strategy_hash)
        self.assertFalse(envelope.broker_write_authority)
        self.assertFalse(envelope.live_write_authority)
        self.assertFalse(envelope.retry_authority)
        self.assertFalse(envelope.promotion_authority)
        self.assertFalse(envelope.strategy_mutation_authority)
        self.assertFalse(envelope.risk_override_authority)

    def test_rejects_wrong_chain_manifest_strategy_or_robustness(self):
        when, manifest, robustness, chain, deployment = self.fixtures()
        wrong_chain = M174ProductionEvidenceChain(
            qualification_manifest_fingerprint=fp("wrong-manifest"),
            strategy_fingerprint=chain.strategy_fingerprint,
            dataset_fingerprint=chain.dataset_fingerprint,
            m166_admission_fingerprint=chain.m166_admission_fingerprint,
            calibration_fingerprint=chain.calibration_fingerprint,
            walk_forward_plan_fingerprint=chain.walk_forward_plan_fingerprint,
            walk_forward_summary_plan_fingerprint=chain.walk_forward_summary_plan_fingerprint,
            purged_split_fingerprints=chain.purged_split_fingerprints,
            parameter_stability_fingerprint=chain.parameter_stability_fingerprint,
            regime_torture_fingerprint=chain.regime_torture_fingerprint,
            cost_torture_fingerprint=chain.cost_torture_fingerprint,
            forward_decay_fingerprint=chain.forward_decay_fingerprint,
            tail_risk_fingerprint=chain.tail_risk_fingerprint,
            strategy_dependency_fingerprint=chain.strategy_dependency_fingerprint,
            robustness_fingerprint=chain.robustness_fingerprint,
        )
        with self.assertRaisesRegex(ValueError, "qualification manifest"):
            freeze_production_champion(
                manifest=manifest, chain=wrong_chain, strategy_family="unit-family",
                deployment=deployment, selection_evidence_fingerprint=fp("selection"),
                robustness=robustness, forecast_integration=None,
                parent_champion_fingerprint=None, created_at=when,
            )
        wrong_deployment = FrozenStrategyDeployment(fp("wrong-strategy"), fp("graph"), (fp("tool"),), "generation-1")
        with self.assertRaisesRegex(ValueError, "frozen deployment strategy"):
            freeze_production_champion(
                manifest=manifest, chain=chain, strategy_family="unit-family",
                deployment=wrong_deployment, selection_evidence_fingerprint=fp("selection"),
                robustness=robustness, forecast_integration=None,
                parent_champion_fingerprint=None, created_at=when,
            )


if __name__ == "__main__":
    unittest.main()
