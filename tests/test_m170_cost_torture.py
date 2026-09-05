from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.broker_calibration import BrokerEconomicsCalibration, CalibrationStatus
from dusty.cost_torture import (
    CostStressPolicy,
    CostStressResult,
    build_cost_stress_scenarios,
    assess_cost_torture,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _calibration(status: CalibrationStatus = CalibrationStatus.CALIBRATED) -> BrokerEconomicsCalibration:
    metrics = (10.0, 18.0, 25.0, 0.5, 2.0, 4.0, 3.5, 4.0, 0.8)
    if status is not CalibrationStatus.CALIBRATED:
        metrics = (None,) * 9
    return BrokerEconomicsCalibration(
        status,
        _fp("broker"),
        "EURUSD",
        40 if status is CalibrationStatus.CALIBRATED else 0,
        5 if status is CalibrationStatus.CALIBRATED else 0,
        tuple(_fp(f"obs-{i}") for i in range(40)) if status is CalibrationStatus.CALIBRATED else (),
        *metrics,
        "calibrated" if status is CalibrationStatus.CALIBRATED else "no observations",
    )


class M170CostSlippageTortureTests(unittest.TestCase):
    def test_uncalibrated_profile_cannot_generate_realistic_cost_scenarios(self) -> None:
        with self.assertRaises(ValueError):
            build_cost_stress_scenarios(_calibration(CalibrationStatus.UNCALIBRATED))

    def test_scenarios_are_bound_to_calibration_and_monotonic_in_stress(self) -> None:
        calibration = _calibration()
        scenarios = build_cost_stress_scenarios(calibration, policy=CostStressPolicy(extreme_multiplier=1.5))
        self.assertEqual(len(scenarios), 4)
        self.assertTrue(all(row.calibration_fingerprint == calibration.fingerprint for row in scenarios))
        spreads = [row.spread_points for row in scenarios]
        slippage = [row.adverse_slippage_points for row in scenarios]
        self.assertEqual(spreads, sorted(spreads))
        self.assertEqual(slippage, sorted(slippage))
        self.assertGreater(scenarios[-1].spread_points, scenarios[-2].spread_points)

    def test_assessment_requires_every_exact_scenario_and_keeps_worst_case(self) -> None:
        calibration = _calibration()
        scenarios = build_cost_stress_scenarios(calibration)
        returns = (0.05, 0.03, 0.01, -0.02)
        results = tuple(
            CostStressResult(row.fingerprint, returns[index], 0.04 + index * 0.02, returns[index] >= 0)
            for index, row in enumerate(scenarios)
        )
        assessment = assess_cost_torture(calibration, scenarios, results)
        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.worst_net_return, -0.02)
        self.assertEqual(assessment.worst_max_drawdown, 0.10)
        self.assertFalse(assessment.broker_write_authority)
        with self.assertRaises(ValueError):
            assess_cost_torture(calibration, scenarios, results[:-1])

    def test_explicit_policy_can_allow_less_than_all_scenarios_but_never_hide_failures(self) -> None:
        calibration = _calibration()
        policy = CostStressPolicy(minimum_pass_fraction=0.75)
        scenarios = build_cost_stress_scenarios(calibration, policy=policy)
        results = tuple(
            CostStressResult(row.fingerprint, 0.01 if index < 3 else -0.01, 0.05, index < 3)
            for index, row in enumerate(scenarios)
        )
        assessment = assess_cost_torture(calibration, scenarios, results, policy=policy)
        self.assertTrue(assessment.passed)
        self.assertEqual(assessment.pass_fraction, 0.75)
        self.assertEqual(assessment.worst_net_return, -0.01)

    def test_scenario_identity_drift_is_rejected(self) -> None:
        calibration = _calibration()
        other = BrokerEconomicsCalibration(
            CalibrationStatus.CALIBRATED,
            _fp("other-broker"), "EURUSD", 40, 5,
            tuple(_fp(f"other-{i}") for i in range(40)),
            10, 18, 25, 0.5, 2, 4, 3.5, 4, 0.8, "calibrated",
        )
        scenarios = build_cost_stress_scenarios(other)
        with self.assertRaises(ValueError):
            assess_cost_torture(calibration, scenarios, ())


if __name__ == "__main__":
    unittest.main()
