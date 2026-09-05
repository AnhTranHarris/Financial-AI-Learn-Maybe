from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from dusty.artifact_vault import ArtifactKind, ResearchArtifactVault
from dusty.champion_registry import (
    ChampionLifecycleEvent,
    ChampionLifecycleEventType,
    FrozenChampionRegistry,
    freeze_champion_record,
)
from dusty.cognition import CognitionAssessment, RoleJustification
from dusty.core import AnalystState, Cognition, GuardianState, PatienceState, SkepticState
from dusty.demo_execution import DemoExecutionResult, DemoMT5ExecutionAdapter
from dusty.demo_execution_bridge import (
    DEMO_BRIDGE_ADMISSION_CONTENT_TYPE,
    DemoBridgePermit,
    DemoExecutionBridge,
)
from dusty.demo_session import AccountMode, DemoSession, SessionIdentity
from dusty.execution_lifecycle import ExecutionState, SQLiteExecutionLedger
from dusty.experience import TradeSide
from dusty.order_intent import BrokerPreflight, OrderIntent
from dusty.robustness_gate import RobustnessCertification, RobustnessGateStatus
from dusty.shadow_execution import ShadowCapturePolicy, ShadowExecutionVault, ShadowMarketQuote, capture_shadow_intent
from dusty.strategy_v3 import FrozenStrategyDeployment, OrderStyle


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 23, 0, tzinfo=UTC)
SOURCE_COMMIT = "570f7bd364b6cefea85698a9d845ef6223e91968"


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class SpyAdapter:
    def __init__(self, result: DemoExecutionResult | None = None, before_send=None) -> None:
        self.calls = 0
        self.last_preflight = None
        self.last_at = None
        self.result = result
        self.before_send = before_send

    @property
    def live_write_authorized(self) -> bool:
        return False

    def send(self, preflight: BrokerPreflight, *, at: datetime) -> DemoExecutionResult:
        if self.before_send is not None:
            self.before_send(preflight, at)
        self.calls += 1
        self.last_preflight = preflight
        self.last_at = at
        return self.result or DemoExecutionResult(
            preflight.intent.intent_hash,
            ExecutionState.FILLED,
            10009,
            700,
            800,
            "done",
        )


class FakeMT5:
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_PLACED = 10008
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(self) -> None:
        self.initialize_calls = 0
        self.shutdown_calls = 0
        self.order_send_calls = 0

    def initialize(self, _terminal_path: str) -> bool:
        self.initialize_calls += 1
        return True

    def shutdown(self) -> None:
        self.shutdown_calls += 1

    def order_send(self, _request: dict[str, object]):
        self.order_send_calls += 1
        return SimpleNamespace(retcode=10009, order=700, deal=800, comment="done")


class M187DemoExecutionBridgeTests(unittest.TestCase):
    def robustness(self) -> RobustnessCertification:
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
            tuple(fp(f"robust-{index}") for index in range(8)),
        )

    def session_identity(self, *, mode: AccountMode = AccountMode.DEMO, login: int = 1001) -> SessionIdentity:
        return SessionIdentity(
            "C:\\Program Files\\Coinexx MT5 Terminal\\terminal64.exe",
            "6180",
            "Coinexx-Demo",
            login,
            mode,
            "USD",
            100.0,
            True,
            True,
            2,
            fp("symbol-spec"),
            NOW - timedelta(minutes=5),
        )

    def champion_registry(self):
        robust = self.robustness()
        deployment = FrozenStrategyDeployment(fp("strategy"), fp("graph"), (fp("tool-a"),), "g1")
        champion = freeze_champion_record(
            lane_id="eurusd:m15:breakout",
            strategy_family="breakout",
            deployment=deployment,
            source_commit=SOURCE_COMMIT,
            selection_evidence_fingerprint=fp("selection"),
            robustness=robust,
            forecast_integration=None,
            parent_champion_fingerprint=None,
            created_at=NOW - timedelta(minutes=4),
        )
        registry = FrozenChampionRegistry()
        registry.register(
            champion,
            robustness=robust,
            forecast_integration=None,
            actor_fingerprint=fp("governance"),
            evidence_fingerprints=(champion.selection_evidence_fingerprint, champion.robustness_fingerprint),
            reason="M187 fixture Champion",
        )
        return registry, champion

    def cognition(self) -> CognitionAssessment:
        cognition = Cognition(AnalystState.LONG, SkepticState.CLEAR, PatienceState.READY, GuardianState.NORMAL)
        return CognitionAssessment(
            cognition,
            (
                RoleJustification("analyst", "long", ("entry_rules_met",)),
                RoleJustification("skeptic", "clear", ("no_material_counterevidence",)),
                RoleJustification("patience", "ready", ("setup_ready",)),
                RoleJustification("guardian", "normal", ("risk_normal",)),
            ),
            fp("cognition"),
        )

    def intent(self, session: DemoSession) -> OrderIntent:
        return OrderIntent(
            fp("strategy"),
            session.identity.fingerprint,
            "EURUSD",
            TradeSide.LONG,
            0.10,
            1.1000,
            1.0950,
            1.1100,
            0.0025,
            50.0,
            True,
            1.0,
            True,
            True,
            NOW,
            NOW + timedelta(minutes=3),
            0,
            order_style=OrderStyle.MARKET,
        )

    def preflight(self, intent: OrderIntent, *, passed: bool = True) -> BrokerPreflight:
        request = (
            ("action", 1),
            ("comment", intent.client_tag),
            ("price", 1.1001),
            ("sl", intent.stop_price),
            ("symbol", intent.symbol),
            ("type", 0),
            ("type_filling", intent.filling_mode),
            ("type_time", 0),
            ("volume", intent.volume),
        )
        return BrokerPreflight(
            intent,
            passed,
            49.0,
            100.0,
            1.1001,
            request if passed else (),
            () if passed else ("failed",),
        )

    def permit(
        self,
        champion,
        session: DemoSession,
        *,
        start=None,
        end=None,
        lane=None,
        champion_fp=None,
        session_fp=None,
    ) -> DemoBridgePermit:
        return DemoBridgePermit(
            champion_fp or champion.fingerprint,
            lane or champion.lane_id,
            session_fp or session.identity.fingerprint,
            fp("permit-issuer"),
            (fp("demo-authorization"), fp("risk-policy")),
            start or NOW,
            end or NOW + timedelta(minutes=2),
        )

    def fixture(self, adapter=None):
        registry, champion = self.champion_registry()
        session = DemoSession(self.session_identity())
        intent = self.intent(session)
        quote = ShadowMarketQuote("EURUSD", NOW + timedelta(milliseconds=500), 1.0999, 1.1001, fp("quote-source"))
        shadow = capture_shadow_intent(
            registry,
            champion,
            intent,
            self.cognition(),
            quote,
            captured_at=NOW + timedelta(seconds=1),
            policy=ShadowCapturePolicy(1000),
        )
        temp = tempfile.TemporaryDirectory()
        vault = ResearchArtifactVault(Path(temp.name) / "vault")
        recorder = ShadowExecutionVault(vault, producer_fingerprint=fp("m186-producer"))
        record = recorder.record_intent(shadow)
        bridge = DemoExecutionBridge(
            registry=registry,
            vault=vault,
            session=session,
            adapter=adapter or SpyAdapter(),
            producer_fingerprint=fp("m187-producer"),
        )
        return temp, vault, registry, champion, session, intent, shadow, record, bridge

    def close_fixture(self, temp, vault, registry):
        vault.close()
        registry.close()
        temp.cleanup()

    def test_permit_is_demo_only_time_bounded_and_non_escalating(self) -> None:
        registry, champion = self.champion_registry()
        session = DemoSession(self.session_identity())
        try:
            permit = self.permit(champion, session)
            self.assertTrue(permit.demo_write_authority)
            self.assertFalse(permit.live_write_authority)
            self.assertFalse(permit.risk_override_authority)
            self.assertFalse(permit.guardian_override_authority)
            self.assertFalse(permit.strategy_mutation_authority)
            self.assertTrue(permit.active_at(NOW + timedelta(seconds=10)))
            self.assertFalse(permit.active_at(NOW + timedelta(minutes=3)))
            with self.assertRaises(ValueError):
                replace(permit, purpose="live_order_send")
            with self.assertRaises(ValueError):
                DemoBridgePermit(
                    champion.fingerprint,
                    champion.lane_id,
                    session.identity.fingerprint,
                    fp("issuer"),
                    (fp("evidence"),),
                    NOW,
                    NOW,
                )
        finally:
            registry.close()

    def test_bridge_has_no_ambient_write_authority_and_names_existing_send_owner(self) -> None:
        values = self.fixture()
        temp, vault, registry, _champion, _session, _intent, _shadow, _record, bridge = values
        try:
            self.assertFalse(bridge.demo_write_authorized)
            self.assertFalse(bridge.live_write_authorized)
            self.assertEqual(bridge.order_send_owner, "DemoMT5ExecutionAdapter")
            self.assertFalse(hasattr(bridge, "order_send"))
        finally:
            self.close_fixture(temp, vault, registry)

    def test_exact_admission_persists_before_send_and_delegates_once(self) -> None:
        holder = {}

        def assert_persisted(preflight, _at):
            vault = holder["vault"]
            rows = tuple(
                row
                for row in vault.list_subject(preflight.intent.intent_hash)
                if row.content_type == DEMO_BRIDGE_ADMISSION_CONTENT_TYPE
            )
            if len(rows) != 1:
                raise AssertionError("M187 admission artifact was not durable before adapter.send")

        spy = SpyAdapter(before_send=assert_persisted)
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, record, bridge = values
        holder["vault"] = vault
        try:
            permit = self.permit(champion, session)
            receipt = bridge.execute(
                champion=champion,
                shadow=shadow,
                shadow_artifact=record,
                preflight=self.preflight(intent),
                permit=permit,
                at=NOW + timedelta(seconds=2),
            )
            self.assertEqual(spy.calls, 1)
            self.assertEqual(receipt.admission.intent_hash, intent.intent_hash)
            self.assertEqual(receipt.execution.intent_hash, intent.intent_hash)
            admission_record = vault.get_record(receipt.admission_artifact_record_fingerprint)
            self.assertIsNotNone(admission_record)
            assert admission_record is not None
            self.assertEqual(admission_record.content_type, DEMO_BRIDGE_ADMISSION_CONTENT_TYPE)
            body = json.loads(vault.read_bytes(admission_record.record_fingerprint))
            self.assertEqual(body["permit"]["champion_fingerprint"], champion.fingerprint)
            self.assertEqual(body["admission"]["shadow_fingerprint"], shadow.fingerprint)
            self.assertFalse(body["permit"]["live_write_authority"])
            self.assertFalse(receipt.live_write_authority)
            self.assertFalse(receipt.retry_authority)
            self.assertFalse(receipt.promotion_authority)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_inactive_permit_never_reaches_adapter(self) -> None:
        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, record, bridge = values
        try:
            permit = self.permit(
                champion,
                session,
                start=NOW - timedelta(minutes=2),
                end=NOW - timedelta(minutes=1),
            )
            with self.assertRaisesRegex(PermissionError, "permit is not active"):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=record,
                    preflight=self.preflight(intent),
                    permit=permit,
                    at=NOW + timedelta(seconds=2),
                )
            self.assertEqual(spy.calls, 0)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_suspended_champion_never_reaches_adapter(self) -> None:
        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, record, bridge = values
        try:
            registry.append_lifecycle_event(
                ChampionLifecycleEvent(
                    champion.fingerprint,
                    ChampionLifecycleEventType.SUSPENDED,
                    fp("drift-watch"),
                    (fp("drift-evidence"),),
                    "suspended before send",
                    NOW + timedelta(milliseconds=1500),
                )
            )
            with self.assertRaisesRegex(PermissionError, "ACTIVE"):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=record,
                    preflight=self.preflight(intent),
                    permit=self.permit(champion, session),
                    at=NOW + timedelta(seconds=2),
                )
            self.assertEqual(spy.calls, 0)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_latched_demo_session_never_reaches_adapter(self) -> None:
        from dusty.demo_session import SessionFault

        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, record, bridge = values
        try:
            session.latch(SessionFault.PERMISSION_LOSS)
            with self.assertRaisesRegex(PermissionError, "latched DemoSession"):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=record,
                    preflight=self.preflight(intent),
                    permit=self.permit(champion, session),
                    at=NOW + timedelta(seconds=2),
                )
            self.assertEqual(spy.calls, 0)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_permit_champion_lane_or_session_drift_never_reaches_adapter(self) -> None:
        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, record, bridge = values
        try:
            permits = (
                self.permit(champion, session, champion_fp=fp("other-champion")),
                self.permit(champion, session, lane="other-lane"),
                self.permit(champion, session, session_fp=fp("other-session")),
            )
            for permit in permits:
                with self.subTest(permit=permit.fingerprint):
                    with self.assertRaises(PermissionError):
                        bridge.execute(
                            champion=champion,
                            shadow=shadow,
                            shadow_artifact=record,
                            preflight=self.preflight(intent),
                            permit=permit,
                            at=NOW + timedelta(seconds=2),
                        )
            self.assertEqual(spy.calls, 0)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_failed_preflight_or_expired_intent_never_reaches_adapter(self) -> None:
        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, record, bridge = values
        try:
            with self.assertRaisesRegex(PermissionError, "preflight did not pass"):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=record,
                    preflight=self.preflight(intent, passed=False),
                    permit=self.permit(champion, session),
                    at=NOW + timedelta(seconds=2),
                )
            with self.assertRaisesRegex(PermissionError, "expired"):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=record,
                    preflight=self.preflight(intent),
                    permit=self.permit(champion, session, end=NOW + timedelta(minutes=10)),
                    at=NOW + timedelta(minutes=4),
                )
            self.assertEqual(spy.calls, 0)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_shadow_economics_drift_is_rejected_even_if_object_hash_field_is_reused(self) -> None:
        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, record, bridge = values
        try:
            forged = replace(shadow, volume=0.20)

            # M186 itself must never accept two different immutable shadow
            # records for the same OrderIntent. This was the original fixture
            # bug: the adversarial test attempted to create an invalid M186
            # state through the guarded M186 persistence API.
            with self.assertRaisesRegex(ValueError, "already has different M186 shadow evidence"):
                ShadowExecutionVault(
                    vault,
                    producer_fingerprint=fp("forged-producer"),
                ).record_intent(forged)

            # Defense in depth: emulate corrupt/bypassed upstream storage by
            # writing the forged bytes through the generic M164 vault. M187
            # must still reject the economic drift before broker delegation.
            forged_record = vault.store_bytes(
                json.dumps(
                    forged.payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                    default=str,
                ).encode("utf-8"),
                kind=ArtifactKind.OTHER,
                content_type=record.content_type,
                producer_fingerprint=fp("forged-producer"),
                subject_fingerprint=intent.intent_hash,
                source_fingerprints=record.source_fingerprints,
                now=forged.captured_at,
            )
            with self.assertRaisesRegex(PermissionError, "shadow/volume binding drift"):
                bridge.execute(
                    champion=champion,
                    shadow=forged,
                    shadow_artifact=forged_record,
                    preflight=self.preflight(intent),
                    permit=self.permit(champion, session),
                    at=NOW + timedelta(seconds=2),
                )
            self.assertEqual(spy.calls, 0)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_wrong_m186_artifact_provenance_fails_closed(self) -> None:
        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, _record, bridge = values
        try:
            wrong = vault.store_bytes(
                b"{}",
                kind=ArtifactKind.OTHER,
                content_type="application/json",
                producer_fingerprint=fp("other"),
                subject_fingerprint=intent.intent_hash,
                source_fingerprints=(fp("other-source"),),
                now=shadow.captured_at,
            )
            with self.assertRaisesRegex(ValueError, "M186 shadow-intent artifact"):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=wrong,
                    preflight=self.preflight(intent),
                    permit=self.permit(champion, session),
                    at=NOW + timedelta(seconds=2),
                )
            self.assertEqual(spy.calls, 0)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_shadow_artifact_timestamp_drift_fails_closed(self) -> None:
        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, _record, bridge = values
        try:
            late = vault.store_bytes(
                json.dumps(
                    shadow.payload,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                    default=str,
                ).encode("utf-8"),
                kind=ArtifactKind.OTHER,
                content_type="application/vnd.dusty.m186-shadow-intent+json",
                producer_fingerprint=fp("late-producer"),
                subject_fingerprint=intent.intent_hash,
                source_fingerprints=(
                    shadow.champion_fingerprint,
                    shadow.champion_deployment_fingerprint,
                    shadow.cognition_fingerprint,
                    shadow.capture_quote.fingerprint,
                    shadow.capture_quote.source_fingerprint,
                    shadow.capture_policy_fingerprint,
                ),
                now=NOW + timedelta(seconds=3),
            )
            with self.assertRaisesRegex(ValueError, "timestamp does not match shadow capture"):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=late,
                    preflight=self.preflight(intent),
                    permit=self.permit(champion, session),
                    at=NOW + timedelta(seconds=4),
                )
            self.assertEqual(spy.calls, 0)
        finally:
            self.close_fixture(temp, vault, registry)

    def test_admission_storage_failure_blocks_adapter_send(self) -> None:
        spy = SpyAdapter()
        values = self.fixture(spy)
        temp, vault, registry, champion, session, intent, shadow, record, bridge = values
        try:
            vault.close()
            with self.assertRaises(sqlite3.ProgrammingError):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=record,
                    preflight=self.preflight(intent),
                    permit=self.permit(champion, session),
                    at=NOW + timedelta(seconds=2),
                )
            self.assertEqual(spy.calls, 0)
            vault = None
        finally:
            if vault is not None:
                vault.close()
            registry.close()
            temp.cleanup()

    def test_real_existing_adapter_sends_once_and_ledger_blocks_duplicate_resend(self) -> None:
        temp, vault, registry, champion, session, intent, shadow, record, _bridge = self.fixture()
        ledger = SQLiteExecutionLedger()
        mt5 = FakeMT5()
        adapter = DemoMT5ExecutionAdapter(mt5, session, lambda: session.identity, ledger)
        bridge = DemoExecutionBridge(
            registry=registry,
            vault=vault,
            session=session,
            adapter=adapter,
            producer_fingerprint=fp("m187-producer"),
        )
        try:
            preflight = self.preflight(intent)
            permit = self.permit(champion, session)
            receipt = bridge.execute(
                champion=champion,
                shadow=shadow,
                shadow_artifact=record,
                preflight=preflight,
                permit=permit,
                at=NOW + timedelta(seconds=2),
            )
            self.assertEqual(receipt.execution.state, ExecutionState.FILLED)
            self.assertEqual(mt5.order_send_calls, 1)
            self.assertEqual(ledger.get(intent.intent_hash).state, ExecutionState.FILLED)
            with self.assertRaises(sqlite3.IntegrityError):
                bridge.execute(
                    champion=champion,
                    shadow=shadow,
                    shadow_artifact=record,
                    preflight=preflight,
                    permit=permit,
                    at=NOW + timedelta(seconds=3),
                )
            self.assertEqual(mt5.order_send_calls, 1)
        finally:
            ledger.close()
            self.close_fixture(temp, vault, registry)

    def test_bridge_constructor_rejects_non_demo_identity_even_if_session_object_is_tampered(self) -> None:
        values = self.fixture()
        temp, vault, registry, _champion, session, _intent, _shadow, _record, _bridge = values
        try:
            session.identity = self.session_identity(mode=AccountMode.REAL)
            with self.assertRaisesRegex(ValueError, "DEMO sessions only"):
                DemoExecutionBridge(
                    registry=registry,
                    vault=vault,
                    session=session,
                    adapter=SpyAdapter(),
                    producer_fingerprint=fp("m187-producer"),
                )
        finally:
            self.close_fixture(temp, vault, registry)


if __name__ == "__main__":
    unittest.main()
