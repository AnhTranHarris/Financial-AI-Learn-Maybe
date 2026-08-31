from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.features import (
    FeatureBar,
    FeatureConfig,
    MT5IndicatorRow,
    compare_mt5_indicators,
    compute_standard_features,
    ema,
    rsi,
    sma,
)


class FeatureTests(unittest.TestCase):
    def bars(self, count: int = 80) -> tuple[FeatureBar, ...]:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        price = 1.10
        for index in range(count):
            price += (0.0007 if index % 5 else -0.0003)
            rows.append(FeatureBar(start + timedelta(minutes=15 * index), price - 0.0002, price + 0.0005, price - 0.0005, price, 12.0, 100 + index))
        return tuple(rows)

    def test_basic_moving_averages(self) -> None:
        values = (1.0, 2.0, 3.0, 4.0, 5.0)
        self.assertEqual(sma(values, 3), (None, None, 2.0, 3.0, 4.0))
        ema_values = ema(values, 3)
        self.assertEqual(ema_values[2], 2.0)
        self.assertEqual(ema_values[3], 3.0)
        self.assertEqual(ema_values[4], 4.0)

    def test_rsi_is_bounded(self) -> None:
        values = tuple(float(i) for i in range(1, 30))
        output = rsi(values, 14)
        observed = [value for value in output if value is not None]
        self.assertTrue(observed)
        self.assertTrue(all(0.0 <= value <= 100.0 for value in observed))
        self.assertEqual(observed[-1], 100.0)

    def test_feature_engine_is_prefix_invariant_and_therefore_no_lookahead(self) -> None:
        rows = self.bars()
        config = FeatureConfig(ma_period=10, atr_period=8, rsi_period=8)
        full = compute_standard_features(rows, config)
        prefix = compute_standard_features(rows[:50], config)
        self.assertEqual(full[:50], prefix)
        self.assertIn("atr", full[-1].feature_map())
        self.assertIn("rsi_8", full[-1].feature_map())

    def test_indicator_parity_comparator_passes_exact_reference(self) -> None:
        rows = self.bars()
        config = FeatureConfig(ma_period=10, atr_period=8, rsi_period=8)
        features = compute_standard_features(rows, config)
        mt5 = []
        for vector in features:
            values = vector.feature_map()
            if all(key in values for key in ("sma_10", "ema_10", "atr_8", "rsi_8")):
                mt5.append(MT5IndicatorRow(vector.at, float(values["sma_10"]), float(values["ema_10"]), float(values["atr_8"]), float(values["rsi_8"])))
        result = compare_mt5_indicators(features, mt5, config=config, min_rows=20)
        self.assertTrue(result.passed)
        self.assertGreaterEqual(result.matched_rows, 20)

    def test_indicator_parity_comparator_detects_drift(self) -> None:
        rows = self.bars()
        config = FeatureConfig(ma_period=10, atr_period=8, rsi_period=8)
        features = compute_standard_features(rows, config)
        last = features[-1]
        values = last.feature_map()
        mt5 = (MT5IndicatorRow(last.at, float(values["sma_10"]) + 0.1, float(values["ema_10"]), float(values["atr_8"]), float(values["rsi_8"])),)
        result = compare_mt5_indicators(features, mt5, config=config, min_rows=1)
        self.assertFalse(result.passed)
        self.assertTrue(any(reason.startswith("sma_parity_failed") for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
