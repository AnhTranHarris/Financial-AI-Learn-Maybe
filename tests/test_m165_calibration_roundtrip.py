from __future__ import annotations

import unittest

from dusty.m165_calibration_roundtrip import (
    CalibrationPlanningPolicy,
    build_native_envelope,
    planning_policy_fingerprint,
    tighten_calibration_loss_budget,
)
from dusty.risk import RiskConstitution


class M165CalibrationRoundTripPlanningTests(unittest.TestCase):
    def test_envelope_uses_minimum_lot_and_existing_normal_risk_ceiling(self) -> None:
        policy = CalibrationPlanningPolicy()
        envelope = build_native_envelope(
            symbol="EURUSD",
            equity=1000.0,
            minimum_volume=0.01,
            volume_step=0.01,
            point=0.00001,
            trade_tick_size=0.00001,
            stops_level_points=10,
            freeze_level_points=0,
            policy=policy,
        )
        self.assertEqual(envelope.minimum_volume, 0.01)
        self.assertAlmostEqual(envelope.loss_ceiling_cash, 1000.0 * RiskConstitution().normal_trade_risk)
        self.assertFalse(envelope.broker_write_authority)
        self.assertFalse(envelope.live_write_authority)
        self.assertFalse(envelope.promotion_authority)
        self.assertEqual(envelope.policy_fingerprint, planning_policy_fingerprint(policy))

    def test_stop_candidates_are_native_aligned_strictly_increasing_and_bounded(self) -> None:
        policy = CalibrationPlanningPolicy(maximum_geometry_probes=8, geometry_growth_factor=1.5)
        envelope = build_native_envelope(
            symbol="EURUSD",
            equity=500.0,
            minimum_volume=0.01,
            volume_step=0.01,
            point=0.00001,
            trade_tick_size=0.00005,
            stops_level_points=12,
            freeze_level_points=4,
            policy=policy,
        )
        rows = envelope.stop_distance_candidates
        self.assertLessEqual(len(rows), 8)
        self.assertGreaterEqual(rows[0], 13 * 0.00001)
        self.assertTrue(all(abs((row / 0.00005) - round(row / 0.00005)) < 1e-8 for row in rows))
        self.assertTrue(all(rows[index] > rows[index - 1] for index in range(1, len(rows))))

    def test_calibration_policy_cannot_relax_dusty_normal_risk(self) -> None:
        with self.assertRaises(ValueError):
            CalibrationPlanningPolicy(normal_risk_fraction=RiskConstitution().normal_trade_risk + 0.0001)
        with self.assertRaises(ValueError):
            CalibrationPlanningPolicy(maximum_geometry_probes=0)
        with self.assertRaises(ValueError):
            CalibrationPlanningPolicy(geometry_growth_factor=1.0)

    def test_measured_loss_replaces_broad_discovery_ceiling(self) -> None:
        self.assertEqual(
            tighten_calibration_loss_budget(observed_loss=0.01, discovery_ceiling=250.0),
            0.01,
        )
        with self.assertRaises(ValueError):
            tighten_calibration_loss_budget(observed_loss=250.01, discovery_ceiling=250.0)
        with self.assertRaises(ValueError):
            tighten_calibration_loss_budget(observed_loss=0.0, discovery_ceiling=250.0)

    def test_invalid_native_volume_geometry_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            build_native_envelope(
                symbol="EURUSD",
                equity=1000.0,
                minimum_volume=0.015,
                volume_step=0.01,
                point=0.00001,
                trade_tick_size=0.00001,
                stops_level_points=0,
                freeze_level_points=0,
            )
        with self.assertRaises(ValueError):
            build_native_envelope(
                symbol="EURUSD",
                equity=0.0,
                minimum_volume=0.01,
                volume_step=0.01,
                point=0.00001,
                trade_tick_size=0.00001,
                stops_level_points=0,
                freeze_level_points=0,
            )


if __name__ == "__main__":
    unittest.main()
