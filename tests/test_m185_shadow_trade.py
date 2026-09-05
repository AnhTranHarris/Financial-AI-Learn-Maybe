from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest

from dusty.artifact_vault import ResearchArtifactVault
from dusty.cognition import CognitionAssessment, RoleJustification
from dusty.core import AnalystState, Cognition, GuardianState, PatienceState, SkepticState
from dusty.experience import TradeSide
from dusty.order_intent import OrderIntent
from dusty.shadow_trade import (
    COMPARISON_CONTENT_TYPE,
    SHADOW_CONTENT_TYPE,
    ObservedBrokerFill,
    ShadowTradeRecorder,
    build_shadow_trade,
    compare_shadow_to_fills,
)
from dusty.strategy_v3 import OrderStyle


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M185ShadowTradeTests(unittest.TestCase):
    def cognition(self, side: TradeSide = TradeSide.LONG) -> CognitionAssessment:
        analyst = AnalystState.LONG if side is TradeSide.LONG else AnalystState.SHORT
        cognition = Cognition(analyst, SkepticState.CLEAR, PatienceState.READY, GuardianState.NORMAL)
        justifications = (
            RoleJustification("analyst", analyst.value, ("entry_rules_met",)),
            RoleJustification("skeptic", SkepticState.CLEAR.value, ("no_material_counterevidence",)),
            RoleJustification("patience", PatienceState.READY.value, ("setup_temporally_ready",)),
            RoleJustification("guardian", GuardianState.NORMAL.value, ("execution_and_risk_normal",)),
        )
        return CognitionAssessment(cognition, justifications, fp(f"cognition-{side.value}"))

    def intent(self, side: TradeSide = TradeSide.LONG, **changes: object) -> OrderIntent:
        if side is TradeSide.LONG:
            reference, stop, target = 1.1000, 1.0950, 1.1100
        else:
            reference, stop, target = 1.1000, 1.1050, 1.0900
        values = dict(
            strategy_hash=fp("strategy"),
            session_fingerprint=fp("session"),
            symbol="EURUSD",
            side=side,
            volume=0.10,
            reference_price=reference,
            stop_price=stop,
            target_price=target,
            approved_risk_fraction=0.0025,
            allowed_loss=50.0,
            pm_approved=True,
            growth_multiplier=1.0,
            risk_approved=True,
            guardian_approved=True,
            created_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            filling_mode=0,
            order_style=OrderStyle.MARKET,
        )
        values.update(changes)
        return OrderIntent(**values)

    def shadow(self, side: TradeSide = TradeSide.LONG, **changes: object):
        values = dict(
            recorded_at=NOW + timedelta(seconds=1),
            contract_size=100_000,
            spread_points=12.0,
            decision_latency_ms=18.0,
            stage="demo",
            shadow_reason="governance-approved pre-send capture",
        )
        values.update(changes)
        return build_shadow_trade(self.intent(side), self.cognition(side), **values)

    def fill(
        self,
        deal: int,
        *,
        order: int = 700,
        seconds: int = 2,
        volume: float = 0.05,
        price: float = 1.1002,
        source: str = "history-snapshot",
    ) -> ObservedBrokerFill:
        return ObservedBrokerFill(
            deal,
            order,
            NOW + timedelta(seconds=seconds),
            volume,
            price,
            fp(source),
        )

    def test_shadow_is_deterministic_and_never_gains_execution_authority(self) -> None:
        first = self.shadow()
        second = self.shadow()
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.planned_notional, 11_000.0)
        self.assertEqual(first.analyst_state, AnalystState.LONG)
        self.assertFalse(first.broker_write_authority)
        self.assertFalse(first.live_write_authority)
        self.assertFalse(first.strategy_mutation_authority)
        self.assertFalse(first.guardian_override_authority)

    def test_unapproved_intent_cannot_enter_shadow_execution_evidence(self) -> None:
        intent = self.intent(risk_approved=False)
        with self.assertRaisesRegex(ValueError, "governance-approved"):
            build_shadow_trade(
                intent,
                self.cognition(),
                recorded_at=NOW + timedelta(seconds=1),
                contract_size=100_000,
                spread_points=10,
                decision_latency_ms=10,
                stage="demo",
                shadow_reason="should fail",
            )

    def test_optional_analyst_score_requires_upstream_fingerprint(self) -> None:
        with self.assertRaisesRegex(ValueError, "appear together"):
            self.shadow(analyst_score=0.8)
        scored = self.shadow(analyst_score=0.8, analyst_score_fingerprint=fp("score-model"))
        self.assertEqual(scored.analyst_score, 0.8)
        self.assertIn(fp("score-model"), scored.source_fingerprints)

    def test_provider_evidence_requires_m184_integration_identity(self) -> None:
        with self.assertRaisesRegex(ValueError, "M184 integration"):
            self.shadow(provider_fingerprints=(fp("chronos"),))
        linked = self.shadow(
            forecast_integration_fingerprint=fp("m184-cert"),
            provider_fingerprints=(fp("chronos"), fp("kronos")),
        )
        self.assertEqual(len(linked.provider_fingerprints), 2)

    def test_partial_fills_preserve_deal_level_evidence_and_vwap(self) -> None:
        shadow = self.shadow()
        fills = (
            self.fill(101, seconds=2, volume=0.04, price=1.1002, source="snapshot-a"),
            self.fill(102, seconds=3, volume=0.03, price=1.1005, source="snapshot-a"),
        )
        result = compare_shadow_to_fills(shadow, fills, observed_at=NOW + timedelta(seconds=4))
        self.assertEqual(result.filled_volume, 0.07)
        self.assertAlmostEqual(result.fill_fraction, 0.7)
        self.assertAlmostEqual(result.weighted_average_fill_price or 0.0, (1.1002 * 0.04 + 1.1005 * 0.03) / 0.07)
        self.assertGreater(result.adverse_slippage_price or 0.0, 0.0)
        self.assertEqual(tuple(row.deal_ticket for row in result.fills), (101, 102))
        self.assertEqual(result.fill_source_fingerprints, (fp("snapshot-a"),))
        payload = result.payload
        self.assertEqual(payload["fills"][0]["order_ticket"], 700)
        self.assertEqual(payload["fills"][1]["deal_ticket"], 102)

    def test_short_slippage_sign_is_adverse_when_fill_is_lower_than_plan(self) -> None:
        shadow = self.shadow(TradeSide.SHORT)
        result = compare_shadow_to_fills(
            shadow,
            (self.fill(201, volume=0.10, price=1.0995),),
            observed_at=NOW + timedelta(seconds=4),
        )
        self.assertGreater(result.adverse_slippage_price or 0.0, 0.0)
        self.assertGreater(result.adverse_slippage_fraction or 0.0, 0.0)

    def test_duplicate_deal_ticket_and_overfill_fail_closed(self) -> None:
        shadow = self.shadow()
        duplicate = (self.fill(301), self.fill(301, seconds=3))
        with self.assertRaisesRegex(ValueError, "duplicate broker deal"):
            compare_shadow_to_fills(shadow, duplicate, observed_at=NOW + timedelta(seconds=4))
        with self.assertRaisesRegex(ValueError, "exceed frozen planned volume"):
            compare_shadow_to_fills(
                shadow,
                (self.fill(302, volume=0.07), self.fill(303, seconds=3, volume=0.04)),
                observed_at=NOW + timedelta(seconds=4),
            )

    def test_empty_fill_observation_is_not_invented_as_broker_rejection(self) -> None:
        result = compare_shadow_to_fills(
            self.shadow(),
            (),
            observed_at=NOW + timedelta(seconds=5),
        )
        self.assertEqual(result.fills, ())
        self.assertEqual(result.fill_fraction, 0.0)
        self.assertIsNone(result.weighted_average_fill_price)
        self.assertIsNone(result.adverse_slippage_fraction)

    def test_fill_or_observation_cannot_predate_frozen_intent(self) -> None:
        shadow = self.shadow()
        with self.assertRaisesRegex(ValueError, "observation predates"):
            compare_shadow_to_fills(shadow, (), observed_at=NOW)
        early = ObservedBrokerFill(401, 700, NOW, 0.10, 1.1001, fp("history"))
        with self.assertRaisesRegex(ValueError, "fill predates"):
            compare_shadow_to_fills(shadow, (early,), observed_at=NOW + timedelta(seconds=5))

    def test_direct_comparison_arithmetic_tamper_fails_closed(self) -> None:
        result = compare_shadow_to_fills(
            self.shadow(),
            (self.fill(501, volume=0.10),),
            observed_at=NOW + timedelta(seconds=5),
        )
        with self.assertRaisesRegex(ValueError, "filled volume"):
            replace(result, filled_volume=0.09)
        with self.assertRaisesRegex(ValueError, "VWAP"):
            replace(result, weighted_average_fill_price=1.2)

    def test_append_only_vault_is_idempotent_for_identical_shadow_and_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            vault = ResearchArtifactVault(Path(folder) / "vault")
            try:
                recorder = ShadowTradeRecorder(vault, producer_fingerprint=fp("m185-recorder"))
                shadow = self.shadow()
                first = recorder.record_shadow(shadow)
                second = recorder.record_shadow(shadow)
                self.assertEqual(first.record_fingerprint, second.record_fingerprint)
                self.assertEqual(first.content_type, SHADOW_CONTENT_TYPE)
                changed = replace(shadow, spread_points=13.0)
                with self.assertRaisesRegex(ValueError, "different shadow evidence"):
                    recorder.record_shadow(changed)
            finally:
                vault.close()

    def test_comparison_artifact_embeds_deals_and_binds_history_sources(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            vault = ResearchArtifactVault(Path(folder) / "vault")
            try:
                recorder = ShadowTradeRecorder(vault, producer_fingerprint=fp("m185-recorder"))
                shadow = self.shadow()
                recorder.record_shadow(shadow)
                comparison = compare_shadow_to_fills(
                    shadow,
                    (self.fill(601, volume=0.10, source="broker-history-export"),),
                    observed_at=NOW + timedelta(seconds=5),
                )
                record = recorder.record_comparison(comparison)
                self.assertEqual(record.content_type, COMPARISON_CONTENT_TYPE)
                self.assertIn(fp("broker-history-export"), record.source_fingerprints)
                self.assertIn(comparison.fills[0].fingerprint, record.source_fingerprints)
                body = json.loads(vault.read_bytes(record.record_fingerprint))
                self.assertEqual(body["fills"][0]["deal_ticket"], 601)
                self.assertEqual(body["fills"][0]["source_fingerprint"], fp("broker-history-export"))
                self.assertTrue(vault.integrity_check()[0])
            finally:
                vault.close()


if __name__ == "__main__":
    unittest.main()
