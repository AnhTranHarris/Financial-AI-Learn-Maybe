from __future__ import annotations

from types import SimpleNamespace
import unittest

from tools.extract_m165_native_observations import _nearest_tick


class _AmbiguousTruthRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def __bool__(self) -> bool:
        raise ValueError("The truth value of an array with more than one element is ambiguous")


class M165NativeObservationExtractorTests(unittest.TestCase):
    def test_nearest_tick_accepts_numpy_like_multirow_container(self) -> None:
        rows = _AmbiguousTruthRows([
            SimpleNamespace(time_msc=1000, bid=1.0, ask=1.1),
            SimpleNamespace(time_msc=1010, bid=2.0, ask=2.1),
        ])
        tick = _nearest_tick(rows, 1007)
        self.assertEqual(tick.time_msc, 1010)

    def test_nearest_tick_rejects_none_or_empty(self) -> None:
        with self.assertRaises(RuntimeError):
            _nearest_tick(None, 1000)
        with self.assertRaises(RuntimeError):
            _nearest_tick([], 1000)


if __name__ == "__main__":
    unittest.main()
