from __future__ import annotations

from types import SimpleNamespace
import unittest

from tools.extract_m165_native_observations import _field, _nearest_tick


class _AmbiguousTruthRows:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def __bool__(self) -> bool:
        raise ValueError("The truth value of an array with more than one element is ambiguous")


class _StructuredRow:
    """NumPy-void-like row: named fields use [] and attributes are absent."""

    def __init__(self, **fields: object) -> None:
        self._fields = fields

    def __getitem__(self, key: str) -> object:
        return self._fields[key]


class M165NativeObservationExtractorTests(unittest.TestCase):
    def test_nearest_tick_accepts_numpy_like_multirow_container(self) -> None:
        rows = _AmbiguousTruthRows([
            SimpleNamespace(time_msc=1000, bid=1.0, ask=1.1),
            SimpleNamespace(time_msc=1010, bid=2.0, ask=2.1),
        ])
        tick = _nearest_tick(rows, 1007)
        self.assertEqual(_field(tick, "time_msc"), 1010)

    def test_structured_named_fields_are_used_before_attribute_fallback(self) -> None:
        rows = _AmbiguousTruthRows([
            _StructuredRow(time_msc=1000, bid=1.1000, ask=1.1002),
            _StructuredRow(time_msc=1010, bid=1.2000, ask=1.2003),
        ])
        tick = _nearest_tick(rows, 1008)
        self.assertEqual(_field(tick, "time_msc"), 1010)
        self.assertAlmostEqual(float(_field(tick, "bid")), 1.2000)
        self.assertAlmostEqual(float(_field(tick, "ask")), 1.2003)

    def test_nearest_tick_skips_zero_or_crossed_quotes(self) -> None:
        rows = [
            _StructuredRow(time_msc=1009, bid=0.0, ask=0.0),
            _StructuredRow(time_msc=1010, bid=1.3, ask=1.2),
            _StructuredRow(time_msc=1011, bid=1.1, ask=1.2),
        ]
        tick = _nearest_tick(rows, 1010)
        self.assertEqual(_field(tick, "time_msc"), 1011)

    def test_nearest_tick_rejects_none_empty_or_no_valid_quote(self) -> None:
        with self.assertRaises(RuntimeError):
            _nearest_tick(None, 1000)
        with self.assertRaises(RuntimeError):
            _nearest_tick([], 1000)
        with self.assertRaises(RuntimeError):
            _nearest_tick([_StructuredRow(time_msc=1000, bid=0.0, ask=0.0)], 1000)


if __name__ == "__main__":
    unittest.main()
