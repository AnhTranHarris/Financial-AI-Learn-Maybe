from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.experience import TradeSide
from dusty.features import FeatureBar, FeatureConfig, completed_feature_bars_from_mt5, compute_standard_features
from dusty.investment_lab import LaboratoryConfig, run_laboratory_from_bars
from dusty.markets import InstrumentEconomics
from dusty.mt5worker import MT5Bar
from dusty.research import Clause, RuleOp
from dusty.runtime import RuntimeBar, compile_strategy, generate_runtime_trades
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2


UTC = timezone.utc


class ExecutionClockTests(unittest.TestCase):
    def economics(self) -> InstrumentEconomics:
        return InstrumentEconomics(
            contract_size=100_000,
            tick_size=0.0001,
            tick_value=10.0,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100.0,
            margin_rate=0.01,
        )

    def strategy(self, *, max_hold: int = 1, stop: str = "pct:0.10"):
        return compile_strategy(
            StrategySpecV2(
                strategy_id="execution-clock",
                direction=TradeSide.LONG,
                entry_groups=(RuleGroup((Clause("close", RuleOp.GT, 1.0),)),),
                exit_plan=ExitPlan(stop, target_rule="off", max_hold_steps=max_hold),
                decision_timeframe_minutes=15,
                intended_horizon_minutes=max(15, max_hold * 15),
            )
        )

    def test_mt5_completed_signal_enters_at_next_open_not_previous_close(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        raw = (
            MT5Bar(start, 1.10, 1.21, 1.09, 1.20, 100, 10, 0),
            MT5Bar(start + timedelta(minutes=15), 1.25, 1.27, 1.24, 1.26, 110, 10, 0),
            MT5Bar(start + timedelta(minutes=30), 1.30, 1.31, 1.29, 1.30, 120, 10, 0),
        )
        completed = completed_feature_bars_from_mt5(raw)
        self.assertEqual(completed[0].close, 1.20)
        self.assertEqual(completed[0].market_price_at_availability, 1.25)
        self.assertEqual(completed[0].at, raw[1].at)

        vectors = compute_standard_features(
            completed,
            FeatureConfig(ma_period=2, atr_period=2, rsi_period=2),
        )
        runtime = tuple(
            RuntimeBar.of(
                bar.at,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                features=vector.feature_map(),
                execution_price=bar.market_price_at_availability,
            )
            for bar, vector in zip(completed, vectors, strict=True)
        )
        trades = generate_runtime_trades(self.strategy(), runtime)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].entry_at, raw[1].at)
        self.assertEqual(trades[0].entry_price, raw[1].open)
        self.assertNotEqual(trades[0].entry_price, raw[0].close)
        self.assertEqual(trades[0].exit_price, raw[2].open)

    def test_gap_through_stop_uses_adverse_bar_open_not_fictitious_stop_fill(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        bars = (
            RuntimeBar.of(
                start,
                open=1.20,
                high=1.21,
                low=1.19,
                close=1.20,
                features={"close": 1.20},
                execution_price=1.20,
            ),
            RuntimeBar.of(
                start + timedelta(minutes=15),
                open=1.20,
                high=1.205,
                low=1.195,
                close=1.20,
                features={"close": 1.20},
                execution_price=1.18,
            ),
            RuntimeBar.of(
                start + timedelta(minutes=30),
                open=1.18,
                high=1.19,
                low=1.17,
                close=1.18,
                features={"close": 1.18},
                execution_price=1.18,
            ),
        )
        # 1% initial stop from 1.20 is 1.188. The next interval opens below it at 1.18.
        trades = generate_runtime_trades(self.strategy(max_hold=4, stop="pct:0.01"), bars)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(trades[0].stop_price, 1.188)
        self.assertEqual(trades[0].exit_reason, "stop")
        self.assertEqual(trades[0].exit_price, 1.18)
        self.assertLess(trades[0].exit_price, trades[0].stop_price)

    def test_reference_lab_marks_open_position_at_availability_price(self) -> None:
        start = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
        rows = []
        for index in range(12):
            completed_close = 1.10 + index * 0.001
            next_open = completed_close + 0.01
            rows.append(
                FeatureBar(
                    at=start + timedelta(minutes=15 * index),
                    open=completed_close - 0.0002,
                    high=completed_close + 0.0005,
                    low=completed_close - 0.0005,
                    close=completed_close,
                    spread_points=0.0,
                    tick_volume=100 + index,
                    source_open_at=start + timedelta(minutes=15 * (index - 1)),
                    execution_price=next_open,
                )
            )
        config = LaboratoryConfig(
            feature_config=FeatureConfig(ma_period=2, atr_period=2, rsi_period=2),
            strategy_test_equity=100_000,
            growth_starting_equity=10_000,
            growth_risk_fraction=0.0025,
        )
        run = run_laboratory_from_bars(
            self.strategy(),
            rows,
            symbol="EURUSD",
            economics=self.economics(),
            config=config,
        )
        self.assertTrue(run.potential_trades)
        first = run.potential_trades[0]
        point = next(
            item
            for item in run.minimum_lot_backtest.ledger
            if item.at == first.entry_at
        )
        # At entry, the ledger's market mark and runtime entry reference are the same availability price.
        self.assertAlmostEqual(point.unrealized_pnl, 0.0)
        self.assertNotEqual(first.entry_price, rows[0].close)


if __name__ == "__main__":
    unittest.main()
