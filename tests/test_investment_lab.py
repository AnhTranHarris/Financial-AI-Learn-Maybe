from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.experience import TradeSide
from dusty.features import FeatureBar, FeatureConfig
from dusty.investment_lab import LaboratoryConfig, run_laboratory_from_bars
from dusty.markets import InstrumentEconomics
from dusty.research import Clause, RuleOp
from dusty.runtime import RuntimeBar, compile_strategy, generate_runtime_trades
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2


class InvestmentLaboratoryTests(unittest.TestCase):
    def economics(self) -> InstrumentEconomics:
        return InstrumentEconomics(100_000, 0.0001, 10.0, 0.01, 0.01, 100.0, margin_rate=0.01, commission_per_lot=7.0)

    def bars(self, count: int = 100) -> tuple[FeatureBar, ...]:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        price = 1.10
        for index in range(count):
            price += 0.0004
            rows.append(FeatureBar(start + timedelta(minutes=15 * index), price - 0.0002, price + 0.0005, price - 0.0005, price, 10.0, 100 + index))
        return tuple(rows)

    def compiled(self, *, trailing: str = "off"):
        return compile_strategy(
            StrategySpecV2(
                strategy_id="lab-long",
                direction=TradeSide.LONG,
                entry_groups=(RuleGroup((Clause("close", RuleOp.GT, 1.0), Clause("sma_5", RuleOp.GT, 1.0))),),
                exit_plan=ExitPlan("pct:0.01", target_rule="off", trailing_rule=trailing, max_hold_steps=4),
                decision_timeframe_minutes=15,
                intended_horizon_minutes=60,
            )
        )

    def config(self, risk: float = 0.0025) -> LaboratoryConfig:
        return LaboratoryConfig(feature_config=FeatureConfig(ma_period=5, atr_period=5, rsi_period=5), strategy_test_equity=100_000, growth_starting_equity=10_000, growth_risk_fraction=risk, spread_price=0.0001, expected_slippage_price=0.00005)

    def test_end_to_end_chain_has_cognition_minimum_lot_and_growth_sizing(self) -> None:
        run = run_laboratory_from_bars(self.compiled(), self.bars(), symbol="EURUSD", economics=self.economics(), config=self.config())
        self.assertGreater(run.cognition_authorized_entries, 0)
        self.assertGreater(run.minimum_lot_backtest.trade_count, 0)
        self.assertGreater(run.growth_backtest.trade_count, 0)
        self.assertIn(",0.01,", run.minimum_lot_manifest)
        approved = [trace for trace in run.growth_sizing if trace.approved]
        self.assertTrue(approved)
        self.assertTrue(all(trace.sizing is not None and trace.sizing.expected_loss <= trace.sizing.allowed_loss + 1e-9 for trace in approved))
        self.assertNotEqual(run.minimum_lot_manifest, run.growth_manifest)

    def test_cognition_veto_is_inside_runtime_path(self) -> None:
        run = run_laboratory_from_bars(self.compiled(), self.bars(), symbol="EURUSD", economics=self.economics(), config=self.config(risk=0.02))
        self.assertEqual(run.cognition_authorized_entries, 0)
        self.assertEqual(run.potential_trades, ())
        self.assertEqual(run.minimum_lot_backtest.trade_count, 0)
        self.assertEqual(run.growth_backtest.trade_count, 0)

    def test_runtime_preserves_initial_stop_when_trailing_tightens(self) -> None:
        strategy = self.compiled(trailing="pct:0.001")
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        bars = []
        for index, close in enumerate((1.10, 1.11, 1.12, 1.13, 1.14, 1.15)):
            bars.append(RuntimeBar.of(start + timedelta(minutes=15 * index), open=close, high=close + 0.001, low=close - 0.001, close=close, features={"close": close, "sma_5": 1.05, "atr": 0.01}))
        trade = generate_runtime_trades(strategy, bars)[0]
        self.assertLess(trade.stop_price, trade.entry_price)
        self.assertIsNotNone(trade.exit_stop_price)
        self.assertGreater(trade.exit_stop_price, trade.stop_price)

    def test_low_capital_can_legitimately_produce_zero_growth_trades(self) -> None:
        config = LaboratoryConfig(feature_config=FeatureConfig(ma_period=5, atr_period=5, rsi_period=5), strategy_test_equity=100_000, growth_starting_equity=100, growth_risk_fraction=0.0025, spread_price=0.0001, expected_slippage_price=0.00005)
        run = run_laboratory_from_bars(self.compiled(), self.bars(), symbol="EURUSD", economics=self.economics(), config=config)
        self.assertGreater(run.minimum_lot_backtest.trade_count, 0)
        self.assertEqual(run.growth_backtest.trade_count, 0)
        self.assertTrue(any(trace.sizing is not None and not trace.sizing.feasible for trace in run.growth_sizing))


if __name__ == "__main__":
    unittest.main()
