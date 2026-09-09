from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.champion_registry import FrozenChampionRegistry, freeze_champion_record
from dusty.cognition import CognitionAssessment, RoleJustification
from dusty.core import AnalystState, Cognition, GuardianState, PatienceState, SkepticState
from dusty.experience import TradeSide
from dusty.m185_production_custody import ProductionChampionCustodyEnvelope
from dusty.m186_production_admission import capture_production_shadow_intent
from dusty.order_intent import OrderIntent
from dusty.robustness_gate import RobustnessCertification, RobustnessGateStatus
from dusty.shadow_execution import ShadowCapturePolicy, ShadowMarketQuote
from dusty.strategy_v3 import FrozenStrategyDeployment, OrderStyle

UTC = timezone.utc
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M186ProductionAdmissionTests(unittest.TestCase):
    def fixtures(self):
        robustness = RobustnessCertification(
            RobustnessGateStatus.SERIOUS_CHALLENGER,
            (
                ("broker_calibration", "calibrated"),
                ("walk_forward", "pass_fraction=1"),
                ("parameter_neighborhood", "stable"),
                ("regime_torture", "passed"),
                ("cost_torture", "passed=True"),
                ("historical_forward_decay", "retention=0.75"),
                ("tail_risk", "dd=0.1;cvar=0.07"),
                ("strategy_dependency", "diversified"),
            ),
            (),
            tuple(fp(f"evidence-{i}") for i in range(8)),
        )
        deployment = FrozenStrategyDeployment(fp("strategy"), fp("graph"), (fp("tool"),), "g1")
        champion = freeze_champion_record(
            lane_id="eurusd:m15:unit",
            strategy_family="unit",
            deployment=deployment,
            source_commit="1" * 40,
            selection_evidence_fingerprint=fp("selection"),
            robustness=robustness,
            forecast_integration=None,
            parent_champion_fingerprint=None,
            created_at=NOW - timedelta(minutes=1),
        )
        registry = FrozenChampionRegistry()
        registry.register(
            champion,
            robustness=robustness,
            forecast_integration=None,
            actor_fingerprint=fp("governance"),
            evidence_fingerprints=(champion.selection_evidence_fingerprint, champion.robustness_fingerprint),
            reason="production admission fixture",
        )
        custody = ProductionChampionCustodyEnvelope(
            champion_record=champion,
            qualification_manifest_fingerprint=fp("manifest"),
            production_chain_fingerprint=fp("chain"),
            selection_evidence_fingerprint=fp("selection"),
        )
        cognition = CognitionAssessment(
            Cognition(AnalystState.LONG, SkepticState.CLEAR, PatienceState.READY, GuardianState.NORMAL),
            (
                RoleJustification("analyst", "long", ("entry_rules_met",)),
                RoleJustification("skeptic", "clear", ("no_counterevidence",)),
                RoleJustification("patience", "ready", ("ready",)),
                RoleJustification("guardian", "normal", ("risk_normal",)),
            ),
            fp("cognition"),
        )
        intent = OrderIntent(
            fp("strategy"), fp("session"), "EURUSD", TradeSide.LONG, 0.01,
            1.1000, 1.0990, 1.1020, 0.001, 10.0,
            True, 1.0, True, True,
            NOW, NOW + timedelta(minutes=2), 0,
            order_style=OrderStyle.MARKET,
        )
        quote = ShadowMarketQuote("EURUSD", NOW, 1.0999, 1.1001, fp("quote-source"))
        return registry, champion, custody, intent, cognition, quote

    def test_production_shadow_binds_custody_and_has_no_authority(self):
        registry, champion, custody, intent, cognition, quote = self.fixtures()
        try:
            admission, shadow = capture_production_shadow_intent(
                registry=registry,
                custody=custody,
                intent=intent,
                cognition=cognition,
                capture_quote=quote,
                captured_at=NOW + timedelta(milliseconds=100),
                policy=ShadowCapturePolicy(1000),
            )
            self.assertEqual(admission.production_custody_fingerprint, custody.fingerprint)
            self.assertEqual(admission.champion_fingerprint, champion.fingerprint)
            self.assertEqual(admission.shadow_intent_fingerprint, shadow.fingerprint)
            self.assertFalse(admission.broker_write_authority)
            self.assertFalse(admission.live_write_authority)
            self.assertFalse(admission.order_send_authority)
            self.assertFalse(admission.retry_authority)
            self.assertFalse(admission.position_mutation_authority)
            self.assertFalse(admission.promotion_authority)
            self.assertFalse(admission.risk_override_authority)
        finally:
            registry.close()

    def test_wrong_strategy_or_inactive_champion_is_rejected(self):
        registry, champion, custody, intent, cognition, quote = self.fixtures()
        try:
            object.__setattr__(intent, "strategy_hash", fp("wrong-strategy"))
            with self.assertRaisesRegex(ValueError, "OrderIntent strategy"):
                capture_production_shadow_intent(
                    registry=registry, custody=custody, intent=intent, cognition=cognition,
                    capture_quote=quote, captured_at=NOW + timedelta(milliseconds=100),
                    policy=ShadowCapturePolicy(1000),
                )
        finally:
            registry.close()


if __name__ == "__main__":
    unittest.main()
