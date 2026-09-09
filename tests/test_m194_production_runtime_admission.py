from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from dusty.champion_registry import ChampionLifecycleState
from dusty.m194_production_runtime_admission import start_production_native_demo_run

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src" / "dusty" / "m194_production_runtime_admission.py"


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class FakeRegistry:
    def __init__(self, champion, *, active=True):
        self.champion = champion
        self.active = active

    def integrity_check(self):
        return True, ()

    def state(self, fingerprint):
        return ChampionLifecycleState.ACTIVE if self.active and fingerprint == self.champion.fingerprint else ChampionLifecycleState.SUSPENDED

    def active_for_lane(self, lane_id):
        return self.champion if self.active and lane_id == self.champion.lane_id else None


class M194ProductionRuntimeAdmissionTests(unittest.TestCase):
    def fixtures(self):
        champion = SimpleNamespace(
            fingerprint=fp("champion"), lane_id="eurusd:m15:unit",
            deployment_fingerprint=fp("deployment"), strategy_fingerprint=fp("strategy"),
        )
        custody = SimpleNamespace(fingerprint=fp("custody"), champion_record=champion)
        identity = SimpleNamespace(
            fingerprint=fp("run"), champion_fingerprint=champion.fingerprint,
            lane_id=champion.lane_id, source_commit="1" * 40,
        )
        return champion, custody, identity

    def test_runtime_start_binds_production_custody(self):
        champion, custody, identity = self.fixtures()
        registry = FakeRegistry(champion)
        with patch("dusty.m194_production_runtime_admission.start_native_demo_run", return_value=identity) as core:
            admission, observed = start_production_native_demo_run(
                custody=custody, registry=registry, journal=SimpleNamespace(),
                run_id="run-1", source_commit="1" * 40,
                snapshot=SimpleNamespace(), assessment=SimpleNamespace(), started_at=SimpleNamespace(),
            )
        self.assertIs(observed, identity)
        self.assertEqual(admission.production_custody_fingerprint, custody.fingerprint)
        self.assertEqual(admission.native_run_identity_fingerprint, identity.fingerprint)
        self.assertFalse(admission.broker_write_authority)
        self.assertFalse(admission.live_write_authority)
        core.assert_called_once()

    def test_inactive_or_wrong_active_champion_blocks_before_core_start(self):
        champion, custody, identity = self.fixtures()
        with patch("dusty.m194_production_runtime_admission.start_native_demo_run", return_value=identity) as core:
            with self.assertRaises(PermissionError):
                start_production_native_demo_run(
                    custody=custody, registry=FakeRegistry(champion, active=False), journal=SimpleNamespace(),
                    run_id="run-1", source_commit="1" * 40,
                    snapshot=SimpleNamespace(), assessment=SimpleNamespace(), started_at=SimpleNamespace(),
                )
        core.assert_not_called()

    def test_module_has_no_direct_broker_send_surface(self):
        self.assertNotIn("order_send(", MODULE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
