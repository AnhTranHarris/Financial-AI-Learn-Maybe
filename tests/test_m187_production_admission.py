from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import unittest

from dusty.demo_execution_bridge import DemoBridgePermit
from dusty.m185_production_custody import ProductionChampionCustodyEnvelope
from dusty.m186_production_admission import M186ProductionShadowAdmission
from dusty.m187_production_admission import execute_production_demo

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "src" / "dusty" / "m187_production_admission.py"


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class FakeBridge:
    def __init__(self) -> None:
        self.calls = 0

    def execute(self, **kwargs):
        self.calls += 1
        champion = kwargs["champion"]
        shadow = kwargs["shadow"]
        preflight = kwargs["preflight"]
        admission = SimpleNamespace(
            champion_fingerprint=champion.fingerprint,
            shadow_fingerprint=shadow.fingerprint,
            intent_hash=preflight.intent.intent_hash,
        )
        return SimpleNamespace(admission=admission)


class M187ProductionAdmissionTests(unittest.TestCase):
    def fixtures(self):
        champion = SimpleNamespace(
            fingerprint=fp("champion"),
            deployment_fingerprint=fp("deployment"),
            lane_id="eurusd:m15:unit",
            strategy_fingerprint=fp("strategy"),
            selection_evidence_fingerprint=fp("selection"),
            robustness_fingerprint=fp("robustness"),
            forecast_integration_fingerprint=None,
            source_commit="1" * 40,
        )
        custody = ProductionChampionCustodyEnvelope(
            champion_record=champion,
            qualification_manifest_fingerprint=fp("manifest"),
            production_chain_fingerprint=fp("chain"),
            selection_evidence_fingerprint=fp("selection"),
        )
        shadow = SimpleNamespace(
            fingerprint=fp("shadow"),
            champion_fingerprint=champion.fingerprint,
            intent_hash=fp("intent"),
        )
        shadow_admission = M186ProductionShadowAdmission(
            production_custody_fingerprint=custody.fingerprint,
            champion_fingerprint=champion.fingerprint,
            deployment_fingerprint=champion.deployment_fingerprint,
            lane_id=champion.lane_id,
            strategy_fingerprint=champion.strategy_fingerprint,
            shadow_intent_fingerprint=shadow.fingerprint,
        )
        preflight = SimpleNamespace(intent=SimpleNamespace(intent_hash=shadow.intent_hash))
        permit = DemoBridgePermit(
            champion_fingerprint=champion.fingerprint,
            lane_id=champion.lane_id,
            session_fingerprint=fp("session"),
            issuer_fingerprint=fp("issuer"),
            authorization_evidence_fingerprints=(custody.fingerprint, shadow_admission.fingerprint),
            valid_from=NOW - timedelta(seconds=1),
            valid_until=NOW + timedelta(seconds=30),
        )
        return champion, custody, shadow, shadow_admission, preflight, permit

    def test_production_execution_requires_custody_shadow_and_permit_evidence(self):
        champion, custody, shadow, shadow_admission, preflight, permit = self.fixtures()
        bridge = FakeBridge()
        admission, receipt = execute_production_demo(
            bridge=bridge,
            custody=custody,
            shadow_admission=shadow_admission,
            shadow=shadow,
            shadow_artifact=SimpleNamespace(),
            preflight=preflight,
            permit=permit,
            at=NOW,
        )
        self.assertEqual(bridge.calls, 1)
        self.assertEqual(admission.production_custody_fingerprint, custody.fingerprint)
        self.assertEqual(admission.m186_production_admission_fingerprint, shadow_admission.fingerprint)
        self.assertEqual(receipt.admission.intent_hash, shadow.intent_hash)
        self.assertFalse(admission.live_write_authority)
        self.assertFalse(admission.retry_authority)
        self.assertFalse(admission.raw_order_send_authority)

    def test_missing_production_evidence_blocks_before_bridge_call(self):
        champion, custody, shadow, shadow_admission, preflight, _ = self.fixtures()
        permit = DemoBridgePermit(
            champion_fingerprint=champion.fingerprint,
            lane_id=champion.lane_id,
            session_fingerprint=fp("session"),
            issuer_fingerprint=fp("issuer"),
            authorization_evidence_fingerprints=(fp("unrelated"),),
            valid_from=NOW - timedelta(seconds=1),
            valid_until=NOW + timedelta(seconds=30),
        )
        bridge = FakeBridge()
        with self.assertRaisesRegex(PermissionError, "lacks M185/M186"):
            execute_production_demo(
                bridge=bridge, custody=custody, shadow_admission=shadow_admission,
                shadow=shadow, shadow_artifact=SimpleNamespace(), preflight=preflight,
                permit=permit, at=NOW,
            )
        self.assertEqual(bridge.calls, 0)

    def test_wrong_m186_custody_blocks_before_bridge_call(self):
        champion, custody, shadow, shadow_admission, preflight, permit = self.fixtures()
        wrong = M186ProductionShadowAdmission(
            production_custody_fingerprint=fp("wrong-custody"),
            champion_fingerprint=champion.fingerprint,
            deployment_fingerprint=champion.deployment_fingerprint,
            lane_id=champion.lane_id,
            strategy_fingerprint=champion.strategy_fingerprint,
            shadow_intent_fingerprint=shadow.fingerprint,
        )
        bridge = FakeBridge()
        with self.assertRaisesRegex(PermissionError, "does not match M185 custody"):
            execute_production_demo(
                bridge=bridge, custody=custody, shadow_admission=wrong,
                shadow=shadow, shadow_artifact=SimpleNamespace(), preflight=preflight,
                permit=permit, at=NOW,
            )
        self.assertEqual(bridge.calls, 0)

    def test_wrapper_has_no_raw_order_send_surface(self):
        self.assertNotIn("order_send(", TOOL.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
