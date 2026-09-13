from __future__ import annotations

import unittest

from dusty.experience import TradeSide
from dusty.reconstruction_feature_contract import (
    ReconstructionUnit,
    reconstruction_feature,
    validate_model_feature_universe,
    validate_reconstruction_spec,
)
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExecutionSensitivity, ExitPlan, GroupMode, RuleGroup, StrategySpecV2


def spec(*clauses: Clause) -> StrategySpecV2:
    return StrategySpecV2(
        strategy_id="semantic-contract-test",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup(tuple(clauses), GroupMode.ALL),),
        exit_plan=ExitPlan("pct:0.01", "pct:0.02", "off", "off", 4),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
        execution_sensitivity=ExecutionSensitivity.NORMAL,
    )


class ReconstructionFeatureContractTests(unittest.TestCase):
    def test_m156_registered_fraction_and_rsi_are_model_safe_with_explicit_units(self) -> None:
        return_feature = reconstruction_feature("return_1")
        rsi_feature = reconstruction_feature("rsi")
        self.assertEqual(return_feature.unit, ReconstructionUnit.FRACTION)
        self.assertEqual((return_feature.minimum, return_feature.maximum), (-0.02, 0.02))
        self.assertEqual(rsi_feature.unit, ReconstructionUnit.OSCILLATOR_0_100)
        self.assertEqual((rsi_feature.minimum, rsi_feature.maximum), (0.0, 100.0))

    def test_raw_price_indicators_are_not_model_authored_scalar_features(self) -> None:
        for name in ("sma", "sma_20", "ema", "ema_20", "atr", "atr_14", "close", "spread_points", "tick_volume"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "not safe"):
                reconstruction_feature(name)

    def test_return_threshold_unit_confusion_fails_closed(self) -> None:
        for threshold in (-0.05, 0.05, 1.0, 5.0):
            with self.subTest(threshold=threshold), self.assertRaisesRegex(ValueError, "fraction domain"):
                validate_reconstruction_spec(spec(Clause("return_1", RuleOp.GT, threshold)))

    def test_rsi_threshold_outside_oscillator_domain_fails_closed(self) -> None:
        for threshold in (-1.0, 101.0, 200.0):
            with self.subTest(threshold=threshold), self.assertRaisesRegex(ValueError, "oscillator_0_100 domain"):
                validate_reconstruction_spec(spec(Clause("rsi", RuleOp.GT, threshold)))

    def test_dimensionally_valid_candidate_passes_without_claiming_activation(self) -> None:
        candidate = spec(
            Clause("return_1", RuleOp.GT, 0.002),
            Clause("rsi", RuleOp.LT, 30.0),
        )
        validate_reconstruction_spec(candidate)

    def test_unsafe_universe_fails_before_model_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "not safe"):
            validate_model_feature_universe(("return_1", "sma"))


if __name__ == "__main__":
    unittest.main()
