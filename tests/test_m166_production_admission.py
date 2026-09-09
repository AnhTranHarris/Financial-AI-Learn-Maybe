from __future__ import annotations

from datetime import datetime, timezone
import unittest

from dusty.broker_calibration import BrokerEconomicsCalibration, CalibrationStatus
from dusty.m166_production_admission import admit_production_walk_forward
from dusty.walk_forward_lab import WalkForwardMode, build_walk_forward_plan


BROKER = "1" * 64
OBS = tuple(f"{index:064x}"[-64:] for index in range(1, 31))


def _calibration(status: CalibrationStatus, *, symbol: str = "EURUSD", count: int = 30, days: int = 3) -> BrokerEconomicsCalibration:
    metrics = (1.0, 2.0, 3.0, 0.0, 1.0, 2.0, 1.0, 1.0, 0.0) if status is CalibrationStatus.CALIBRATED else (None,) * 9
    return BrokerEconomicsCalibration(
        status,
        BROKER,
        symbol,
        count,
        days,
        OBS[:count],
        *metrics,
        "test calibration" if status is CalibrationStatus.CALIBRATED else "insufficient",
    )


def _plan():
    return build_walk_forward_plan(
        strategy_execution_fingerprint="2" * 64,
        parameter_fingerprint="3" * 64,
        dataset_fingerprint="4" * 64,
        start=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end=datetime(2020, 4, 1, tzinfo=timezone.utc),
        train_days=30,
        test_days=10,
        mode=WalkForwardMode.ANCHORED,
    )


class M166ProductionAdmissionTests(unittest.TestCase):
    def test_calibrated_m165_evidence_binds_exact_walk_forward_plan(self) -> None:
        calibration = _calibration(CalibrationStatus.CALIBRATED)
        plan = _plan()
        admission = admit_production_walk_forward(calibration=calibration, plan=plan, expected_symbol="EURUSD")
        self.assertEqual(admission.calibration_fingerprint, calibration.fingerprint)
        self.assertEqual(admission.walk_forward_plan_fingerprint, plan.fingerprint)
        self.assertEqual(admission.observation_count, 30)
        self.assertEqual(admission.distinct_days, 3)
        self.assertFalse(admission.broker_write_authority)
        self.assertFalse(admission.live_write_authority)
        self.assertFalse(admission.retry_authority)
        self.assertFalse(admission.promotion_authority)
        self.assertFalse(admission.risk_override_authority)

    def test_insufficient_m165_cannot_admit_production_m166(self) -> None:
        with self.assertRaises(PermissionError):
            admit_production_walk_forward(
                calibration=_calibration(CalibrationStatus.INSUFFICIENT, count=10, days=1),
                plan=_plan(),
                expected_symbol="EURUSD",
            )

    def test_symbol_drift_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            admit_production_walk_forward(
                calibration=_calibration(CalibrationStatus.CALIBRATED, symbol="EURUSD"),
                plan=_plan(),
                expected_symbol="XAUUSD",
            )

    def test_admission_payload_has_no_authority(self) -> None:
        admission = admit_production_walk_forward(
            calibration=_calibration(CalibrationStatus.CALIBRATED),
            plan=_plan(),
            expected_symbol="EURUSD",
        )
        self.assertEqual(
            admission.payload["authority"],
            {"broker_write": False, "live_write": False, "retry": False, "promotion": False, "risk_override": False},
        )


if __name__ == "__main__":
    unittest.main()
