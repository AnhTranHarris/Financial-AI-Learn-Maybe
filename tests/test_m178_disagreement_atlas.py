from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.disagreement_atlas import (
    DisagreementAtlasStatus,
    DisagreementOutcomeObservation,
    build_disagreement_cell,
)
from dusty.forecast_research import DisagreementState, ForecastDisagreement
from dusty.forecast_specialization import ForecastContextBucket


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def disagreement(state: DisagreementState) -> ForecastDisagreement:
    directions = {
        DisagreementState.UNANIMOUS_UP: (("chronos2", "up"), ("kronos-small", "up"), ("timesfm-2.5", "up")),
        DisagreementState.TWO_UP_ONE_DOWN: (("chronos2", "up"), ("kronos-small", "up"), ("timesfm-2.5", "down")),
        DisagreementState.MIXED_WITH_FLAT: (("chronos2", "up"), ("kronos-small", "down"), ("timesfm-2.5", "flat")),
    }[state]
    return ForecastDisagreement(state, directions, tuple(fp(f"e-{i}") for i in range(3)))


class M178DisagreementAtlasTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bucket = ForecastContextBucket("EURUSD", "M15", "london", "trend", 4)

    def test_majority_pattern_measures_realized_consensus_accuracy(self) -> None:
        d = disagreement(DisagreementState.TWO_UP_ONE_DOWN)
        rows = tuple(
            DisagreementOutcomeObservation(fp(f"case-{i}"), self.bucket, d, "up" if i < 3 else "down", 0.01 if i < 3 else -0.01)
            for i in range(4)
        )
        cell = build_disagreement_cell(self.bucket, d.state, rows, minimum_cases=4)
        self.assertEqual(cell.status, DisagreementAtlasStatus.MEASURED)
        self.assertEqual(cell.consensus_direction, "up")
        self.assertEqual(cell.consensus_accuracy, 0.75)
        self.assertFalse(cell.decision_authority)
        self.assertFalse(cell.broker_write_authority)

    def test_mixed_pattern_has_no_invented_consensus_vote(self) -> None:
        d = disagreement(DisagreementState.MIXED_WITH_FLAT)
        rows = tuple(DisagreementOutcomeObservation(fp(f"case-{i}"), self.bucket, d, "up", 0.01) for i in range(3))
        cell = build_disagreement_cell(self.bucket, d.state, rows, minimum_cases=3)
        self.assertEqual(cell.status, DisagreementAtlasStatus.MEASURED)
        self.assertIsNone(cell.consensus_direction)
        self.assertIsNone(cell.consensus_accuracy)
        self.assertAlmostEqual(cell.mean_realized_return, 0.01)

    def test_sparse_pattern_is_insufficient(self) -> None:
        d = disagreement(DisagreementState.UNANIMOUS_UP)
        row = DisagreementOutcomeObservation(fp("one"), self.bucket, d, "up", 0.01)
        cell = build_disagreement_cell(self.bucket, d.state, (row,), minimum_cases=5)
        self.assertEqual(cell.status, DisagreementAtlasStatus.INSUFFICIENT)
        self.assertIsNone(cell.consensus_accuracy)

    def test_other_context_or_state_does_not_leak_into_cell(self) -> None:
        other = ForecastContextBucket("GBPUSD", "M15", "london", "trend", 4)
        d = disagreement(DisagreementState.UNANIMOUS_UP)
        row = DisagreementOutcomeObservation(fp("other"), other, d, "up", 0.01)
        cell = build_disagreement_cell(self.bucket, d.state, (row,), minimum_cases=1)
        self.assertEqual(cell.case_count, 0)
        self.assertEqual(cell.status, DisagreementAtlasStatus.INSUFFICIENT)

    def test_duplicate_case_identity_fails_closed(self) -> None:
        d = disagreement(DisagreementState.UNANIMOUS_UP)
        row = DisagreementOutcomeObservation(fp("same"), self.bucket, d, "up", 0.01)
        with self.assertRaises(ValueError):
            build_disagreement_cell(self.bucket, d.state, (row, row), minimum_cases=1)


if __name__ == "__main__":
    unittest.main()
