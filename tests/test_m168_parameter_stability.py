from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.parameter_stability import (
    NeighborhoodPolicy,
    NeighborhoodStatus,
    ParameterPointResult,
    assess_parameter_neighborhood,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _point(label: str, distance: float, score: float, drawdown: float, passed: bool = True) -> ParameterPointResult:
    return ParameterPointResult(_fp(label), distance, score, drawdown, passed)


class M168ParameterNeighborhoodStabilityTests(unittest.TestCase):
    def test_broad_local_plateau_is_stable(self) -> None:
        center = _point("center", 0.0, 1.00, 0.10)
        neighbors = (
            _point("n1", 0.25, 0.95, 0.10),
            _point("n2", 0.50, 0.90, 0.11),
            _point("n3", 0.75, 0.86, 0.12),
            _point("n4", 1.00, 0.82, 0.13),
            _point("outside", 1.50, -10.0, 0.90, False),
        )
        assessment = assess_parameter_neighborhood(center, neighbors)
        self.assertEqual(assessment.status, NeighborhoodStatus.STABLE)
        self.assertEqual(assessment.neighbor_count, 4)
        self.assertEqual(assessment.stable_neighbor_count, 4)
        self.assertEqual(assessment.stable_fraction, 1.0)
        self.assertFalse(assessment.broker_write_authority)

    def test_isolated_spike_is_unstable_even_when_center_passes(self) -> None:
        center = _point("center", 0.0, 1.00, 0.10)
        neighbors = tuple(
            _point(f"n{i}", 0.2 * i, 0.35 + i * 0.02, 0.12, True)
            for i in range(1, 6)
        )
        assessment = assess_parameter_neighborhood(center, neighbors)
        self.assertEqual(assessment.status, NeighborhoodStatus.UNSTABLE)
        self.assertLess(assessment.stable_fraction, 0.60)
        self.assertGreater(assessment.maximum_relative_degradation, 0.25)

    def test_neighbor_drawdown_blowup_breaks_stability(self) -> None:
        center = _point("center", 0.0, 1.0, 0.10)
        neighbors = (
            _point("a", 0.2, 0.95, 0.40),
            _point("b", 0.4, 0.94, 0.35),
            _point("c", 0.6, 0.93, 0.30),
            _point("d", 0.8, 0.92, 0.25),
        )
        assessment = assess_parameter_neighborhood(center, neighbors)
        self.assertEqual(assessment.status, NeighborhoodStatus.UNSTABLE)
        self.assertEqual(assessment.stable_neighbor_count, 0)

    def test_insufficient_neighborhood_does_not_infer_stability(self) -> None:
        center = _point("center", 0.0, 1.0, 0.10)
        assessment = assess_parameter_neighborhood(center, (_point("a", 0.3, 0.99, 0.1),))
        self.assertEqual(assessment.status, NeighborhoodStatus.INSUFFICIENT)
        self.assertIsNone(assessment.neighbor_median_score)

    def test_duplicates_and_nonzero_center_distance_fail_closed(self) -> None:
        center = _point("center", 0.1, 1.0, 0.10)
        with self.assertRaises(ValueError):
            assess_parameter_neighborhood(center, ())
        center = _point("center", 0.0, 1.0, 0.10)
        duplicate = _point("dup", 0.3, 0.9, 0.1)
        with self.assertRaises(ValueError):
            assess_parameter_neighborhood(center, (duplicate, duplicate), policy=NeighborhoodPolicy(minimum_neighbors=1))


if __name__ == "__main__":
    unittest.main()
