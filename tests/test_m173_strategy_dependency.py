from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.strategy_dependency import (
    DependencyPolicy,
    DependencyStatus,
    StrategyReturnSeries,
    build_strategy_dependency_matrix,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _series(label: str, returns: tuple[float, ...], *, offset_seconds: int = 0) -> StrategyReturnSeries:
    start = datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)
    times = tuple(start + timedelta(days=index) for index in range(len(returns)))
    return StrategyReturnSeries(_fp(label), times, returns)


class M173StrategyDependencyMatrixTests(unittest.TestCase):
    def test_diversified_series_pass_declared_dependency_limits(self) -> None:
        a = tuple(0.01 if index % 2 == 0 else -0.005 for index in range(40))
        b = tuple(0.004 if index % 3 == 0 else (-0.002 if index % 3 == 1 else 0.001) for index in range(40))
        matrix = build_strategy_dependency_matrix((_series("a", a), _series("b", b)))
        self.assertEqual(matrix.status, DependencyStatus.DIVERSIFIED)
        self.assertEqual(len(matrix.pairs), 1)
        self.assertFalse(matrix.broker_write_authority)

    def test_highly_identical_strategies_are_concentrated(self) -> None:
        returns = tuple(0.01 if index % 3 else -0.02 for index in range(40))
        matrix = build_strategy_dependency_matrix((_series("a", returns), _series("b", returns)))
        self.assertEqual(matrix.status, DependencyStatus.CONCENTRATED)
        self.assertAlmostEqual(matrix.maximum_absolute_correlation, 1.0)
        self.assertAlmostEqual(matrix.maximum_co_loss_fraction, 1.0)

    def test_downside_co_loss_can_trigger_concentration_even_with_looser_correlation_limit(self) -> None:
        a = tuple(-0.01 if index % 4 == 0 else (0.01 if index % 2 else 0.002) for index in range(40))
        b = tuple(-0.02 if index % 4 == 0 else (0.003 if index % 3 else 0.009) for index in range(40))
        policy = DependencyPolicy(maximum_absolute_correlation=1.0, maximum_co_loss_fraction=0.50)
        matrix = build_strategy_dependency_matrix((_series("a", a), _series("b", b)), policy=policy)
        self.assertEqual(matrix.status, DependencyStatus.CONCENTRATED)
        self.assertGreater(matrix.maximum_co_loss_fraction, 0.50)

    def test_unsynchronized_timestamp_grids_fail_closed(self) -> None:
        returns = tuple(0.01 for _ in range(40))
        with self.assertRaises(ValueError):
            build_strategy_dependency_matrix(
                (_series("a", returns), _series("b", returns, offset_seconds=1))
            )

    def test_thin_synchronized_history_is_insufficient(self) -> None:
        returns = tuple(0.01 if index % 2 else -0.01 for index in range(10))
        matrix = build_strategy_dependency_matrix((_series("a", returns), _series("b", returns)))
        self.assertEqual(matrix.status, DependencyStatus.INSUFFICIENT)
        self.assertEqual(matrix.pairs, ())
        self.assertIsNone(matrix.maximum_absolute_correlation)

    def test_duplicate_strategy_identity_is_rejected(self) -> None:
        returns = tuple(0.01 for _ in range(40))
        row = _series("a", returns)
        with self.assertRaises(ValueError):
            build_strategy_dependency_matrix((row, row))


if __name__ == "__main__":
    unittest.main()
