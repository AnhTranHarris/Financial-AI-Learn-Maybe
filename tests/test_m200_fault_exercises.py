from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dusty.long_running_soak import (
    SoakDisturbanceKind,
    SoakEvidenceMode,
    SoakRecoveryStatus,
)
from dusty.m200_fault_exercises import (
    exercise_abnormal_broker_condition,
    exercise_data_gap,
    exercise_market_closure,
    exercise_provider_failure,
)


NOW = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)


class M200FaultExerciseTests(unittest.TestCase):
    def test_provider_failure_exercises_m191_without_external_mutation(self):
        result = exercise_provider_failure(
            at=NOW,
            provider_id="ollama-qwen",
            model_identity_fingerprint="a" * 64,
        )
        self.assertIs(result.kind, SoakDisturbanceKind.PROVIDER_OR_MODEL_FAILURE)
        self.assertIs(result.recovery_status, SoakRecoveryStatus.RECOVERED)
        self.assertIs(result.mode, SoakEvidenceMode.CONTROLLED_EXERCISE)

    def test_data_gap_exercises_m1967_fail_closed_path(self):
        result = exercise_data_gap(at=NOW)
        self.assertIs(result.kind, SoakDisturbanceKind.DATA_GAP)
        self.assertIs(result.recovery_status, SoakRecoveryStatus.SAFE_HALT)
        self.assertIs(result.mode, SoakEvidenceMode.CONTROLLED_EXERCISE)

    def test_market_closure_exercises_entry_suppression(self):
        result = exercise_market_closure(at=NOW)
        self.assertIs(result.kind, SoakDisturbanceKind.MARKET_CLOSURE)
        self.assertIs(result.recovery_status, SoakRecoveryStatus.SAFE_HALT)
        self.assertIs(result.mode, SoakEvidenceMode.CONTROLLED_EXERCISE)

    def test_abnormal_broker_condition_exercises_halt(self):
        result = exercise_abnormal_broker_condition(at=NOW)
        self.assertIs(result.kind, SoakDisturbanceKind.ABNORMAL_BROKER_CONDITION)
        self.assertIs(result.recovery_status, SoakRecoveryStatus.SAFE_HALT)
        self.assertIs(result.mode, SoakEvidenceMode.CONTROLLED_EXERCISE)

    def test_fault_exercises_expose_no_authority(self):
        rows = (
            exercise_provider_failure(
                at=NOW,
                provider_id="ollama-qwen",
                model_identity_fingerprint="a" * 64,
            ),
            exercise_data_gap(at=NOW),
            exercise_market_closure(at=NOW),
            exercise_abnormal_broker_condition(at=NOW),
        )
        for row in rows:
            self.assertFalse(hasattr(row, "broker_write_authority"))
            self.assertFalse(hasattr(row, "live_write_authority"))


if __name__ == "__main__":
    unittest.main()
