from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.forecast_calibration_memory import (
    CalibrationMemoryPolicy,
    CalibrationMemoryStatus,
    ForecastCalibrationObservation,
    build_calibration_memory,
    make_calibration_observation,
)
from dusty.forecast_campaign import PITForecastAttempt, PITForecastCase, PITForecastOutcome
from dusty.forecast_specialization import ForecastContextBucket
from dusty.provider_forecast_adapter import ForecastEvidence, PROTOCOL


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def case() -> PITForecastCase:
    as_of = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return PITForecastCase(fp("case"), "EURUSD", "M15", as_of, as_of + timedelta(hours=1), 4, 100.0, fp("ctx"))


def evidence(row: PITForecastCase, *, provider: str = "chronos2", p50: float = 102.0, p10: float = 98.0, p90: float = 104.0) -> ForecastEvidence:
    return ForecastEvidence(PROTOCOL, provider, "model", "rev", "runtime", "license", row.symbol, row.timeframe, row.as_of, row.as_of, row.horizon_steps, row.origin_value, p10, p50, p90, row.context_sha256, fp("req" + provider), fp("resp" + provider))


class M177ForecastCalibrationMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bucket = ForecastContextBucket("EURUSD", "M15", "london", "trend", 4)

    def test_observation_is_derived_only_from_realized_pit_outcome(self) -> None:
        row = case()
        attempt = PITForecastAttempt(row.case_fingerprint, "chronos2", evidence(row, p50=102.0))
        outcome = PITForecastOutcome(row.case_fingerprint, row.target_at, 101.0)
        obs = make_calibration_observation(row, attempt, outcome, bucket=self.bucket)
        self.assertAlmostEqual(obs.signed_error_fraction, 0.01)
        self.assertEqual(obs.absolute_error, 1.0)
        self.assertTrue(obs.direction_hit)
        self.assertTrue(obs.interval_80_hit)

    def test_unavailable_forecast_cannot_create_calibration_observation(self) -> None:
        row = case()
        attempt = PITForecastAttempt(row.case_fingerprint, "chronos2", None, "timeout")
        outcome = PITForecastOutcome(row.case_fingerprint, row.target_at, 101.0)
        with self.assertRaises(ValueError):
            make_calibration_observation(row, attempt, outcome, bucket=self.bucket)

    def test_measured_memory_tracks_bias_skill_direction_and_coverage(self) -> None:
        rows = tuple(
            ForecastCalibrationObservation(
                fp(f"case-{index}"),
                "chronos2",
                self.bucket,
                0.01 if index < 3 else -0.01,
                0.5,
                1.0,
                index != 3,
                index != 2,
            )
            for index in range(4)
        )
        memory = build_calibration_memory("chronos2", self.bucket, rows, policy=CalibrationMemoryPolicy(minimum_cases=4))
        self.assertEqual(memory.status, CalibrationMemoryStatus.MEASURED)
        self.assertAlmostEqual(memory.mean_signed_error_fraction, 0.005)
        self.assertAlmostEqual(memory.skill, 0.5)
        self.assertAlmostEqual(memory.direction_accuracy, 0.75)
        self.assertAlmostEqual(memory.observed_interval_80_coverage, 0.75)
        self.assertAlmostEqual(memory.interval_coverage_error, 0.05)
        self.assertFalse(memory.forecast_correction_authority)
        self.assertFalse(memory.voting_authority)
        self.assertFalse(memory.broker_write_authority)

    def test_sparse_memory_is_explicitly_insufficient(self) -> None:
        row = ForecastCalibrationObservation(fp("one"), "kronos-small", self.bucket, 0.0, 1.0, 1.0, True, True)
        memory = build_calibration_memory("kronos-small", self.bucket, (row,), policy=CalibrationMemoryPolicy(minimum_cases=5))
        self.assertEqual(memory.status, CalibrationMemoryStatus.INSUFFICIENT)
        self.assertIsNone(memory.skill)
        self.assertIsNone(memory.interval_coverage_error)

    def test_duplicate_case_identity_fails_closed(self) -> None:
        row = ForecastCalibrationObservation(fp("same"), "timesfm-2.5", self.bucket, 0.0, 1.0, 1.0, True, True)
        with self.assertRaises(ValueError):
            build_calibration_memory("timesfm-2.5", self.bucket, (row, row), policy=CalibrationMemoryPolicy(minimum_cases=1))

    def test_bucket_drift_does_not_leak_into_target_memory(self) -> None:
        other = ForecastContextBucket("GBPUSD", "M15", "london", "trend", 4)
        row = ForecastCalibrationObservation(fp("other"), "chronos2", other, 0.0, 0.1, 1.0, True, True)
        memory = build_calibration_memory("chronos2", self.bucket, (row,), policy=CalibrationMemoryPolicy(minimum_cases=1))
        self.assertEqual(memory.status, CalibrationMemoryStatus.INSUFFICIENT)
        self.assertEqual(memory.case_count, 0)


if __name__ == "__main__":
    unittest.main()
