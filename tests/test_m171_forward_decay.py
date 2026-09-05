from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import unittest

from dusty.forward_decay import (
    DecayStatus,
    PerformanceEvidence,
    measure_historical_forward_decay,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _evidence(label: str, start: str, end: str, value: float, trades: int, *, forward: bool) -> PerformanceEvidence:
    return PerformanceEvidence(
        _fp(label),
        _fp("strategy"),
        datetime.fromisoformat(start).replace(tzinfo=timezone.utc),
        datetime.fromisoformat(end).replace(tzinfo=timezone.utc),
        "expectancy",
        value,
        trades,
        forward,
    )


class M171HistoricalForwardDecayTests(unittest.TestCase):
    def test_missing_forward_evidence_stays_unmeasured(self) -> None:
        historical = _evidence("hist", "2025-01-01", "2025-06-01", 2.0, 200, forward=False)
        result = measure_historical_forward_decay(historical, None)
        self.assertEqual(result.status, DecayStatus.MISSING_FORWARD)
        self.assertIsNone(result.retention_ratio)
        self.assertIsNone(result.decay_fraction)
        self.assertFalse(result.broker_write_authority)

    def test_thin_real_forward_sample_is_insufficient_not_extrapolated(self) -> None:
        historical = _evidence("hist", "2025-01-01", "2025-06-01", 2.0, 200, forward=False)
        forward = _evidence("forward", "2025-06-02", "2025-07-01", 1.2, 12, forward=True)
        result = measure_historical_forward_decay(historical, forward, minimum_forward_trades=30)
        self.assertEqual(result.status, DecayStatus.INSUFFICIENT_FORWARD)
        self.assertEqual(result.forward_value, 1.2)
        self.assertIsNone(result.retention_ratio)

    def test_actual_later_forward_evidence_measures_retention_and_decay(self) -> None:
        historical = _evidence("hist", "2025-01-01", "2025-06-01", 2.0, 200, forward=False)
        forward = _evidence("forward", "2025-06-02", "2025-09-01", 1.5, 45, forward=True)
        result = measure_historical_forward_decay(historical, forward)
        self.assertEqual(result.status, DecayStatus.MEASURED)
        self.assertAlmostEqual(result.retention_ratio, 0.75)
        self.assertAlmostEqual(result.decay_fraction, 0.25)

    def test_overlapping_or_fake_forward_period_is_rejected(self) -> None:
        historical = _evidence("hist", "2025-01-01", "2025-06-01", 2.0, 200, forward=False)
        overlap = _evidence("forward", "2025-05-31", "2025-07-01", 1.5, 40, forward=True)
        with self.assertRaises(ValueError):
            measure_historical_forward_decay(historical, overlap)
        fake = _evidence("fake", "2025-06-02", "2025-07-01", 1.5, 40, forward=False)
        with self.assertRaises(ValueError):
            measure_historical_forward_decay(historical, fake)

    def test_nonpositive_historical_baseline_does_not_create_ratio_artifact(self) -> None:
        historical = _evidence("hist", "2025-01-01", "2025-06-01", 0.0, 200, forward=False)
        with self.assertRaises(ValueError):
            measure_historical_forward_decay(historical, None)


if __name__ == "__main__":
    unittest.main()
