from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.forecast_failure_memory import (
    FailurePatternStatus,
    ForecastFailureEvent,
    ForecastFailureKind,
    build_failure_pattern,
)
from dusty.forecast_specialization import ForecastContextBucket


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M181ForecastFailureMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bucket = ForecastContextBucket("EURUSD", "M15", "london", "trend", 4)

    def event(self, index: int, kind: ForecastFailureKind = ForecastFailureKind.WRONG_DIRECTION, providers: tuple[str, ...] = ("chronos2",)) -> ForecastFailureEvent:
        return ForecastFailureEvent(fp(f"case-{index}"), providers, self.bucket, kind, fp(f"source-{index}"))

    def test_repeated_failure_becomes_recurrent_pattern_without_disable_authority(self) -> None:
        events = tuple(self.event(i) for i in range(3))
        pattern = build_failure_pattern(("chronos2",), self.bucket, ForecastFailureKind.WRONG_DIRECTION, events, minimum_occurrences=3)
        self.assertEqual(pattern.status, FailurePatternStatus.RECURRENT)
        self.assertEqual(pattern.occurrence_count, 3)
        self.assertFalse(pattern.provider_disable_authority)
        self.assertFalse(pattern.strategy_mutation_authority)
        self.assertFalse(pattern.broker_write_authority)

    def test_one_failure_does_not_become_recurrent(self) -> None:
        pattern = build_failure_pattern(("kronos-small",), self.bucket, ForecastFailureKind.INTERVAL_MISS, (self.event(1, ForecastFailureKind.INTERVAL_MISS, ("kronos-small",)),), minimum_occurrences=3)
        self.assertEqual(pattern.status, FailurePatternStatus.INSUFFICIENT)

    def test_harmful_provider_combination_is_memory_distinct_from_single_provider(self) -> None:
        combo = ("chronos2", "timesfm-2.5")
        events = tuple(self.event(i, ForecastFailureKind.HARMFUL_ABLATION, combo) for i in range(3))
        combo_pattern = build_failure_pattern(combo, self.bucket, ForecastFailureKind.HARMFUL_ABLATION, events)
        single_pattern = build_failure_pattern(("chronos2",), self.bucket, ForecastFailureKind.HARMFUL_ABLATION, events)
        self.assertEqual(combo_pattern.status, FailurePatternStatus.RECURRENT)
        self.assertEqual(single_pattern.status, FailurePatternStatus.INSUFFICIENT)

    def test_context_drift_does_not_leak_into_pattern(self) -> None:
        other = ForecastContextBucket("GBPUSD", "M15", "london", "trend", 4)
        event = ForecastFailureEvent(fp("case"), ("chronos2",), other, ForecastFailureKind.UNAVAILABLE, fp("source"))
        pattern = build_failure_pattern(("chronos2",), self.bucket, ForecastFailureKind.UNAVAILABLE, (event,), minimum_occurrences=1)
        self.assertEqual(pattern.occurrence_count, 0)
        self.assertEqual(pattern.status, FailurePatternStatus.INSUFFICIENT)

    def test_duplicate_case_identity_fails_closed(self) -> None:
        row = self.event(1)
        with self.assertRaises(ValueError):
            build_failure_pattern(("chronos2",), self.bucket, ForecastFailureKind.WRONG_DIRECTION, (row, row), minimum_occurrences=1)

    def test_unknown_provider_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ForecastFailureEvent(fp("case"), ("unknown",), self.bucket, ForecastFailureKind.UNAVAILABLE, fp("source"))


if __name__ == "__main__":
    unittest.main()
