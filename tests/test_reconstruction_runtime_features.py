from __future__ import annotations

import unittest
from datetime import datetime, timezone

from dusty.reconstruction_feature_contract import (
    ReconstructionUnit,
    reconstruction_feature,
    validate_reconstruction_spec,
)
from dusty.reconstruction_runtime_features import (
    ATR_14_FRACTION,
    CLOSE_EMA_20_DISTANCE_FRAC,
    CLOSE_SMA_20_DISTANCE_FRAC,
    augment_runtime_bar,
    normalized_reconstruction_values,
)
from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp
from dusty.runtime import RuntimeBar
from dusty.strategy_ir import ExecutionSensitivity, ExitPlan, GroupMode, RuleGroup, StrategySpecV2


def spec(*clauses: Clause) -> StrategySpecV2:
    return StrategySpecV2(
        strategy_id="normalized-semantic-test",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup(tuple(clauses), GroupMode.ALL),),
        exit_plan=ExitPlan("pct:0.01", "pct:0.02", "off", "off", 4),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
        execution_sensitivity=ExecutionSensitivity.NORMAL,
    )


class ReconstructionRuntimeFeatureTests(unittest.TestCase):
    def test_normalized_values_are_dimensionless_and_period_explicit(self) -> None:
        values = normalized_reconstruction_values({
            "close": 100.0,
            "sma_20": 98.0,
            "ema_20": 99.0,
            "atr_14": 2.0,
        })
        self.assertAlmostEqual(values[CLOSE_SMA_20_DISTANCE_FRAC], 100.0 / 98.0 - 1.0)
        self.assertAlmostEqual(values[CLOSE_EMA_20_DISTANCE_FRAC], 100.0 / 99.0 - 1.0)
        self.assertAlmostEqual(values[ATR_14_FRACTION], 0.02)

    def test_warmup_missing_inputs_do_not_invent_features(self) -> None:
        self.assertEqual(normalized_reconstruction_values({"close": 1.1}), {})

    def test_runtime_augmentation_preserves_identity_and_execution_reference(self) -> None:
        row = RuntimeBar.of(
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            open=1.1,
            high=1.2,
            low=1.0,
            close=1.15,
            execution_price=1.16,
            features={"close": 1.15, "sma_20": 1.10, "ema_20": 1.12, "atr_14": 0.01},
        )
        augmented = augment_runtime_bar(row)
        self.assertEqual(augmented.at, row.at)
        self.assertEqual(augmented.execution_price, row.execution_price)
        self.assertEqual(augmented.close, row.close)
        self.assertIn(CLOSE_SMA_20_DISTANCE_FRAC, augmented.feature_map())
        self.assertIn(CLOSE_EMA_20_DISTANCE_FRAC, augmented.feature_map())
        self.assertIn(ATR_14_FRACTION, augmented.feature_map())

    def test_contract_exposes_normalized_units_and_rejects_absurd_thresholds(self) -> None:
        sma = reconstruction_feature(CLOSE_SMA_20_DISTANCE_FRAC)
        atr = reconstruction_feature(ATR_14_FRACTION)
        self.assertEqual(sma.unit, ReconstructionUnit.SIGNED_PRICE_DISTANCE_FRACTION)
        self.assertEqual(atr.unit, ReconstructionUnit.NONNEGATIVE_PRICE_FRACTION)
        validate_reconstruction_spec(spec(
            Clause(CLOSE_SMA_20_DISTANCE_FRAC, RuleOp.GT, 0.002),
            Clause(ATR_14_FRACTION, RuleOp.LT, 0.01),
        ))
        with self.assertRaisesRegex(ValueError, "signed_price_distance_fraction domain"):
            validate_reconstruction_spec(spec(Clause(CLOSE_SMA_20_DISTANCE_FRAC, RuleOp.GT, 200.0)))
        with self.assertRaisesRegex(ValueError, "nonnegative_price_fraction domain"):
            validate_reconstruction_spec(spec(Clause(ATR_14_FRACTION, RuleOp.GT, 2.0)))


if __name__ == "__main__":
    unittest.main()
