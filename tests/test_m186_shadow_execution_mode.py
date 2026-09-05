from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from dusty.artifact_vault import ResearchArtifactVault
from dusty.champion_registry import (
    ChampionLifecycleEvent,
    ChampionLifecycleEventType,
    FrozenChampionRegistry,
    freeze_champion_record,
)
from dusty.cognition import CognitionAssessment, RoleJustification
from dusty.core import AnalystState, Cognition, GuardianState, PatienceState, SkepticState
from dusty.experience import TradeSide
from dusty.order_intent import OrderIntent
from dusty.robustness_gate import RobustnessCertification, RobustnessGateStatus
from dusty.shadow_execution import (
    SHADOW_ASSESSMENT_CONTENT_TYPE,
    SHADOW_INTENT_CONTENT_TYPE,
    ShadowAssessmentStatus,
    ShadowCapturePolicy,
    ShadowExecutionVault,
    ShadowMarketQuote,
    ShadowQuoteWindow,
    assess_shadow_execution,
    capture_shadow_intent,
)
from dusty.strategy_v3 import FrozenStrategyDeployment, OrderStyle


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 22, 0, tzinfo=UTC)
SOURCE_COMMIT = "596d996e156b530697158eadc596bf475f64ad68"


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M186ShadowExecutionModeTests(unittest.TestCase):
    def robust(self) -> RobustnessCertification:
        return RobustnessCertification(
            RobustnessGateStatus.SERIOUS_CHALLENGER,
            (
                ("broker_calibration", "calibrated"),
                ("walk_forward", "pass_fraction=0.9"),
                ("parameter_neighborhood", "stable"),
                ("regime_torture", "passed"),
                ("cost_torture", "passed=True"),
                ("historical_forward_decay", "retention=0.75"),
                ("tail_risk", "dd=0.1;cvar=0.08"),
                ("strategy_dependency", "diversified"),
            ),
            (),
            tuple(fp(f"robust-{i}") for i in range(8)),
        )

    def champion_registry(self):
        robust = self.robust()
        deployment = FrozenStrategyDeployment(fp("strategy"), fp("graph"), (fp("tool-a"), fp("tool-b")), "g1")
        champion = freeze_champion_record(
            lane_id="eurusd:m15:breakout",
            strategy_family="breakout",
            deployment=deployment,
            source_commit=SOURCE_COMMIT,
            selection_evidence_fingerprint=fp("selection"),
            robustness=robust,
            forecast_integration=None,
            parent_champion_fingerprint=None,
            created_at=NOW - timedelta(minutes=1),
        )
        registry = FrozenChampionRegistry()
        registry.register(
            champion,
            robustness=robust,
            forecast_integration=None,
            actor_fingerprint=fp("governance"),
            evidence_fingerprints=(champion.selection_evidence_fingerprint, champion.robustness_fingerprint),
            reason="frozen for shadow test",
        )
        return registry, champion

    def cognition(self, *, side: TradeSide = TradeSide.LONG, guardian: GuardianState = GuardianState.NORMAL) -> CognitionAssessment:
        analyst = AnalystState.LONG if side is TradeSide.LONG else AnalystState.SHORT
        cognition = Cognition(analyst, SkepticState.CLEAR, PatienceState.READY, guardian)
        rows = (
            RoleJustification("analyst", analyst.value, ("entry_rules_met",)),
            RoleJustification("skeptic", "clear", ("no_material_counterevidence",)),
            RoleJustification("patience", "ready", ("setup_temporally_ready",)),
            RoleJustification("guardian", guardian.value, ("execution_and_risk_normal",)),
        )
        return CognitionAssessment(cognition, rows, fp(f"cognition-{side.value}-{guardian.value}"))

    def intent(
        self,
        *,
        side: TradeSide = TradeSide.LONG,
        style: OrderStyle = OrderStyle.MARKET,
        reference: float = 1.1000,
        stop_limit: float | None = None,
        approved: bool = True,
    ) -> OrderIntent:
        stop = 1.0950 if side is TradeSide.LONG else 1.1050
        target = 1.1100 if side is TradeSide.LONG else 1.0900
        pending_expiry = None if style is OrderStyle.MARKET else NOW + timedelta(minutes=10)
        return OrderIntent(
            fp("strategy"), fp("demo-session"), "EURUSD", side, 0.10,
            reference, stop, target, 0.0025, 50.0,
            approved, 1.0, approved, approved,
            NOW, NOW + timedelta(minutes=2), 0,
            order_style=style,
            pending_expiry=pending_expiry,
            stop_limit_price=stop_limit,
        )

    def quote(self, seconds: float, *, bid: float, ask: float, source: str = "quotes") -> ShadowMarketQuote:
        return ShadowMarketQuote("EURUSD", NOW + timedelta(seconds=seconds), bid, ask, fp(source))

    def capture(self, registry, champion, intent, cognition=None, quote=None, *, at=None, max_age_ms=1000):
        capture_at = at or NOW + timedelta(seconds=1)
        capture_quote = quote or self.quote(0.5, bid=1.0999, ask=1.1001)
        return capture_shadow_intent(
            registry,
            champion,
            intent,
            cognition or self.cognition(side=intent.side),
            capture_quote,
            captured_at=capture_at,
            policy=ShadowCapturePolicy(max_age_ms),
        )

    def window(self, quotes, *, start=None, end=None, complete=True, source="window") -> ShadowQuoteWindow:
        return ShadowQuoteWindow(
            "EURUSD",
            start or NOW + timedelta(seconds=1),
            end or NOW + timedelta(minutes=11),
            complete,
            fp(source),
            tuple(quotes),
        )

    def test_market_shadow_executes_from_embedded_capture_quote_and_has_no_authority(self) -> None:
        registry, champion = self.champion_registry()
        try:
            shadow = self.capture(registry, champion, self.intent())
            result = assess_shadow_execution(
                shadow,
                self.window((), start=NOW, end=NOW + timedelta(seconds=2)),
                evaluated_at=NOW + timedelta(seconds=2),
            )
            self.assertEqual(result.status, ShadowAssessmentStatus.WOULD_EXECUTE)
            self.assertAlmostEqual(result.theoretical_execution_price or 0.0, 1.1001)
            self.assertEqual(result.time_to_executable_ms, 0.0)
            self.assertFalse(shadow.broker_write_authority)
            self.assertFalse(shadow.order_send_authority)
            self.assertFalse(shadow.retry_authority)
            self.assertFalse(shadow.position_mutation_authority)
            self.assertFalse(shadow.promotion_authority)
            self.assertFalse(shadow.risk_override_authority)
            self.assertFalse(shadow.guardian_override_authority)
            self.assertFalse(result.broker_write_authority)
            self.assertFalse(result.order_send_authority)
        finally:
            registry.close()

    def test_short_market_uses_bid_not_ask(self) -> None:
        registry, champion = self.champion_registry()
        try:
            intent = self.intent(side=TradeSide.SHORT)
            shadow = self.capture(registry, champion, intent, quote=self.quote(0.5, bid=1.0998, ask=1.1002))
            result = assess_shadow_execution(shadow, self.window((), start=NOW, end=NOW + timedelta(seconds=2)), evaluated_at=NOW + timedelta(seconds=2))
            self.assertEqual(result.status, ShadowAssessmentStatus.WOULD_EXECUTE)
            self.assertAlmostEqual(result.theoretical_execution_price or 0.0, 1.0998)
        finally:
            registry.close()

    def test_capture_requires_active_exact_champion_and_strategy(self) -> None:
        registry, champion = self.champion_registry()
        try:
            registry.append_lifecycle_event(
                ChampionLifecycleEvent(
                    champion.fingerprint,
                    ChampionLifecycleEventType.SUSPENDED,
                    fp("drift-watch"),
                    (fp("drift"),),
                    "suspended",
                    NOW,
                )
            )
            with self.assertRaisesRegex(ValueError, "ACTIVE"):
                self.capture(registry, champion, self.intent())
        finally:
            registry.close()

        registry, champion = self.champion_registry()
        try:
            wrong = self.intent()
            object.__setattr__(wrong, "strategy_hash", fp("other-strategy"))
            with self.assertRaisesRegex(ValueError, "does not match Frozen Champion"):
                self.capture(registry, champion, wrong)
        finally:
            registry.close()

    def test_stale_or_future_capture_quote_fails_closed(self) -> None:
        registry, champion = self.champion_registry()
        try:
            stale = ShadowMarketQuote("EURUSD", NOW - timedelta(seconds=5), 1.0999, 1.1001, fp("quotes"))
            with self.assertRaisesRegex(ValueError, "staleness policy"):
                self.capture(registry, champion, self.intent(), quote=stale, max_age_ms=1000)
            future = self.quote(2, bid=1.0999, ask=1.1001)
            with self.assertRaisesRegex(ValueError, "future quote"):
                self.capture(registry, champion, self.intent(), quote=future, at=NOW + timedelta(seconds=1))
        finally:
            registry.close()

    def test_governance_or_guardian_stop_cannot_be_frozen(self) -> None:
        registry, champion = self.champion_registry()
        try:
            with self.assertRaisesRegex(ValueError, "governance-approved"):
                self.capture(registry, champion, self.intent(approved=False))
            with self.assertRaisesRegex(ValueError, "Guardian STOP"):
                self.capture(registry, champion, self.intent(), cognition=self.cognition(guardian=GuardianState.STOP))
        finally:
            registry.close()

    def test_limit_long_and_short_use_executable_side_of_quote(self) -> None:
        registry, champion = self.champion_registry()
        try:
            long_shadow = self.capture(
                registry, champion,
                self.intent(style=OrderStyle.LIMIT, reference=1.0990),
                quote=self.quote(0.5, bid=1.0999, ask=1.1001),
            )
            long_quotes = (
                self.quote(2, bid=1.0992, ask=1.0994),
                self.quote(3, bid=1.0987, ask=1.0989),
            )
            long_result = assess_shadow_execution(long_shadow, self.window(long_quotes, start=NOW, end=NOW + timedelta(minutes=11)), evaluated_at=NOW + timedelta(minutes=11))
            self.assertEqual(long_result.status, ShadowAssessmentStatus.WOULD_EXECUTE)
            self.assertAlmostEqual(long_result.theoretical_execution_price or 0.0, 1.0989)

            short_shadow = self.capture(
                registry, champion,
                self.intent(side=TradeSide.SHORT, style=OrderStyle.LIMIT, reference=1.1010),
                quote=self.quote(0.5, bid=1.0999, ask=1.1001),
            )
            short_quotes = (self.quote(3, bid=1.1011, ask=1.1013),)
            short_result = assess_shadow_execution(short_shadow, self.window(short_quotes, start=NOW, end=NOW + timedelta(minutes=11)), evaluated_at=NOW + timedelta(minutes=11))
            self.assertEqual(short_result.status, ShadowAssessmentStatus.WOULD_EXECUTE)
            self.assertAlmostEqual(short_result.theoretical_execution_price or 0.0, 1.1011)
        finally:
            registry.close()

    def test_stop_and_stop_limit_geometry_are_distinct(self) -> None:
        registry, champion = self.champion_registry()
        try:
            stop_shadow = self.capture(
                registry, champion,
                self.intent(style=OrderStyle.STOP, reference=1.1010),
                quote=self.quote(0.5, bid=1.0999, ask=1.1001),
            )
            stop_result = assess_shadow_execution(
                stop_shadow,
                self.window((self.quote(3, bid=1.1010, ask=1.1012),), start=NOW, end=NOW + timedelta(minutes=11)),
                evaluated_at=NOW + timedelta(minutes=11),
            )
            self.assertEqual(stop_result.status, ShadowAssessmentStatus.WOULD_EXECUTE)
            self.assertGreater(stop_result.adverse_price_delta or 0.0, 0.0)

            stop_limit = self.capture(
                registry, champion,
                self.intent(style=OrderStyle.STOP_LIMIT, reference=1.1010, stop_limit=1.1005),
                quote=self.quote(0.5, bid=1.0999, ask=1.1001),
            )
            quotes = (
                self.quote(3, bid=1.1010, ask=1.1012),
                self.quote(4, bid=1.1003, ask=1.1005),
            )
            result = assess_shadow_execution(stop_limit, self.window(quotes, start=NOW, end=NOW + timedelta(minutes=11)), evaluated_at=NOW + timedelta(minutes=11))
            self.assertEqual(result.status, ShadowAssessmentStatus.WOULD_EXECUTE)
            self.assertNotEqual(result.trigger_quote_fingerprint, result.executable_quote_fingerprint)
            self.assertAlmostEqual(result.theoretical_execution_price or 0.0, 1.1005)
        finally:
            registry.close()

    def test_incomplete_quote_window_cannot_manufacture_expired_unfilled(self) -> None:
        registry, champion = self.champion_registry()
        try:
            shadow = self.capture(
                registry, champion,
                self.intent(style=OrderStyle.LIMIT, reference=1.0990),
                quote=self.quote(0.5, bid=1.0999, ask=1.1001),
            )
            incomplete = self.window(
                (self.quote(2, bid=1.0995, ask=1.0997),),
                start=NOW,
                end=NOW + timedelta(minutes=5),
                complete=False,
            )
            result = assess_shadow_execution(shadow, incomplete, evaluated_at=NOW + timedelta(minutes=11))
            self.assertEqual(result.status, ShadowAssessmentStatus.INSUFFICIENT_MARKET_EVIDENCE)
            self.assertIn("incomplete_quote_coverage_cannot_prove_unfilled_expiry", result.reasons)
        finally:
            registry.close()

    def test_complete_quote_window_can_prove_expired_unfilled_without_claiming_broker_fill(self) -> None:
        registry, champion = self.champion_registry()
        try:
            shadow = self.capture(
                registry, champion,
                self.intent(style=OrderStyle.LIMIT, reference=1.0990),
                quote=self.quote(0.5, bid=1.0999, ask=1.1001),
            )
            quotes = (
                self.quote(2, bid=1.0995, ask=1.0997),
                self.quote(590, bid=1.0994, ask=1.0996),
            )
            complete = self.window(quotes, start=NOW, end=NOW + timedelta(minutes=10), complete=True)
            result = assess_shadow_execution(shadow, complete, evaluated_at=NOW + timedelta(minutes=11))
            self.assertEqual(result.status, ShadowAssessmentStatus.EXPIRED_UNFILLED)
            self.assertIsNone(result.theoretical_execution_price)
        finally:
            registry.close()

    def test_duplicate_quotes_symbol_drift_and_out_of_coverage_fail_closed(self) -> None:
        q = self.quote(2, bid=1.0999, ask=1.1001)
        with self.assertRaisesRegex(ValueError, "duplicate quote"):
            self.window((q, q), start=NOW, end=NOW + timedelta(seconds=3))
        wrong = ShadowMarketQuote("GBPUSD", NOW + timedelta(seconds=2), 1.2, 1.2002, fp("quotes"))
        with self.assertRaisesRegex(ValueError, "symbol drift"):
            self.window((wrong,), start=NOW, end=NOW + timedelta(seconds=3))
        with self.assertRaisesRegex(ValueError, "outside declared"):
            self.window((q,), start=NOW + timedelta(seconds=3), end=NOW + timedelta(seconds=4))

    def test_pending_geometry_invalid_at_capture_fails_closed(self) -> None:
        registry, champion = self.champion_registry()
        try:
            with self.assertRaisesRegex(ValueError, "geometry is invalid"):
                self.capture(
                    registry, champion,
                    self.intent(style=OrderStyle.LIMIT, reference=1.1010),
                    quote=self.quote(0.5, bid=1.0999, ask=1.1001),
                )
        finally:
            registry.close()

    def test_vault_is_idempotent_and_preserves_quote_provenance(self) -> None:
        registry, champion = self.champion_registry()
        try:
            shadow = self.capture(registry, champion, self.intent())
            window = self.window((), start=NOW, end=NOW + timedelta(seconds=2))
            assessment = assess_shadow_execution(shadow, window, evaluated_at=NOW + timedelta(seconds=2))
            with tempfile.TemporaryDirectory() as folder:
                vault = ResearchArtifactVault(Path(folder) / "vault")
                try:
                    recorder = ShadowExecutionVault(vault, producer_fingerprint=fp("m186"))
                    first = recorder.record_intent(shadow)
                    second = recorder.record_intent(shadow)
                    self.assertEqual(first.record_fingerprint, second.record_fingerprint)
                    self.assertEqual(first.content_type, SHADOW_INTENT_CONTENT_TYPE)
                    self.assertIn(shadow.capture_quote.source_fingerprint, first.source_fingerprints)
                    assessment_record = recorder.record_assessment(assessment, window)
                    self.assertEqual(assessment_record.content_type, SHADOW_ASSESSMENT_CONTENT_TYPE)
                    self.assertIn(window.fingerprint, assessment_record.source_fingerprints)
                    self.assertTrue(vault.integrity_check()[0])
                    self.assertFalse(recorder.broker_write_authorized)
                    self.assertFalse(recorder.order_send_authorized)
                    self.assertFalse(hasattr(recorder, "order_send"))
                finally:
                    vault.close()
        finally:
            registry.close()


if __name__ == "__main__":
    unittest.main()
