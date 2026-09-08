from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import unittest

from dusty.m165_calibration_execution import load_calibration_execution_binding


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 4, 0, tzinfo=UTC)
HEAD = "a" * 40


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")).hexdigest()


def plan() -> dict[str, object]:
    payload: dict[str, object] = {
        "protocol": "dusty-m165-calibration-roundtrip-plan-v1",
        "status": "planned",
        "source_commit": HEAD,
        "qualification_plan_fingerprint": fp("qualification-plan"),
        "qualification_plan_source_commit": "b" * 40,
        "qualification_manifest_fingerprint": fp("qualification-manifest"),
        "lane_id": "eurusd:m15:lane",
        "strategy_hash": fp("strategy"),
        "symbol": "EURUSD",
        "side": "long",
        "session_fingerprint": fp("session"),
        "native_preflight_assessment_fingerprint": fp("preflight"),
        "native_snapshot_fingerprint": fp("snapshot"),
        "symbol_spec_fingerprint": fp("symbol-spec"),
        "native_envelope": {"protocol": "dusty-m165-calibration-native-envelope-v1"},
        "selected": {
            "intent_hash": fp("intent"),
            "client_tag": "DD-test",
            "volume_lots": 0.01,
            "reference_price": 1.1,
            "stop_price": 1.09999,
            "stop_distance": 0.00001,
            "filling_mode": 0,
            "loss_at_stop": 0.01,
            "actual_risk_fraction": 0.0000001,
            "approved_risk_fraction": 0.0000001,
            "discovery_loss_ceiling_cash": 250.0,
            "execution_allowed_loss_cash": 0.01,
            "required_margin": 2.0,
            "checked_price": 1.1,
            "created_at": (NOW - timedelta(seconds=30)).isoformat(),
            "expires_at": (NOW + timedelta(seconds=90)).isoformat(),
        },
        "rejected_probe_count_before_selection": 0,
        "authority": {
            "broker_write": False,
            "live_write": False,
            "promotion": False,
            "execution_bridge_invoked": False,
        },
    }
    payload["plan_fingerprint"] = digest(payload)
    return payload


class M165CalibrationExecutionBindingTests(unittest.TestCase):
    def test_exact_current_plan_binds_without_authority(self) -> None:
        binding = load_calibration_execution_binding(plan(), expected_source_commit=HEAD, now=NOW)
        self.assertEqual(binding.execution_allowed_loss_cash, 0.01)
        self.assertFalse(binding.broker_write_authority)
        self.assertFalse(binding.live_write_authority)
        self.assertFalse(binding.promotion_authority)
        self.assertFalse(binding.retry_authority)

    def test_tampered_or_wrong_head_plan_fails_closed(self) -> None:
        payload = plan()
        payload["selected"]["volume_lots"] = 0.02
        with self.assertRaises(ValueError):
            load_calibration_execution_binding(payload, expected_source_commit=HEAD, now=NOW)
        with self.assertRaises(ValueError):
            load_calibration_execution_binding(plan(), expected_source_commit="c" * 40, now=NOW)

    def test_expired_plan_fails_closed(self) -> None:
        with self.assertRaises(PermissionError):
            load_calibration_execution_binding(plan(), expected_source_commit=HEAD, now=NOW + timedelta(minutes=3))

    def test_execution_budget_must_equal_measured_loss(self) -> None:
        payload = plan()
        payload["selected"]["execution_allowed_loss_cash"] = 1.0
        payload["plan_fingerprint"] = digest({key: value for key, value in payload.items() if key != "plan_fingerprint"})
        with self.assertRaises(ValueError):
            load_calibration_execution_binding(payload, expected_source_commit=HEAD, now=NOW)


if __name__ == "__main__":
    unittest.main()
