from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.experience import TradeSide
from dusty.features import (
    FeatureConfig,
    atr,
    completed_feature_bars_from_mt5,
    parse_mt5_indicator_csv,
)
from dusty.investment_lab import LaboratoryConfig, run_laboratory_from_bars, run_laboratory_from_mt5
from dusty.markets import InstrumentEconomics
from dusty.mt5worker import MT5Bar, MT5BarRequest
from dusty.research import Clause, RuleOp
from dusty.runtime import RuntimeBar, compile_strategy, generate_runtime_trades
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2


UTC = timezone.utc


class FakeWorker:
    def __init__(self, bars: tuple[MT5Bar, ...]) -> None:
        self.bars = bars

    def stream_bars(self, request: MT5BarRequest):
        yield from self.bars


class InvestmentReviewProofTests(unittest.TestCase):
    def economics(self) -> InstrumentEconomics:
        return InstrumentEconomics(
            contract_size=100_000,
            tick_size=0.0001,
            tick_value=10.0,
            volume_min=0.01,
            volume_step=0.01,
            volume_max=100.0,
            margin_rate=0.01,
            commission_per_lot=7.0,
        )

    def spec(self, *, cooldown: int = 0, trailing: str = "off", scale_in: int = 0, scale_out: tuple[float, ...] = ()) -> StrategySpecV2:
        return StrategySpecV2(
            strategy_id="proof",
            direction=TradeSide.LONG,
            entry_groups=(RuleGroup((Clause("close", RuleOp.GT, 1.0), Clause("sma_5", RuleOp.GT, 1.0))),),
            exit_plan=ExitPlan("pct:0.01", target_rule="off", trailing_rule=trailing, max_hold_steps=1),
            decision_timeframe_minutes=15,
            intended_horizon_minutes=30,
            cooldown_steps=cooldown,
            scale_in_limit=scale_in,
            scale_out_fractions=scale_out,
        )

    def feature_bars(self, count: int = 60):
        from dusty.features import FeatureBar

        start = datetime(2026, 1, 1, 0, 15, tzinfo=UTC)
        rows = []
        price = 1.10
        for index in range(count):
            price += 0.0004
            rows.append(
                FeatureBar(
                    start + timedelta(minutes=15 * index),
                    price - 0.0002,
                    price + 0.0005,
                    price - 0.0005,
                    price,
                    10.0,
                    100 + index,
                    source_open_at=start + timedelta(minutes=15 * (index - 1)) if index else start - timedelta(minutes=15),
                )
            )
        return tuple(rows)

    def raw_mt5_bars(self, count: int = 65) -> tuple[MT5Bar, ...]:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        rows = []
        price = 1.10
        for index in range(count):
            price += 0.0004
            rows.append(
                MT5Bar(
                    at=start + timedelta(minutes=15 * index),
                    open=price - 0.0002,
                    high=price + 0.0005,
                    low=price - 0.0005,
                    close=price,
                    tick_volume=100 + index,
                    spread=10,
                    real_volume=0,
                )
            )
        return tuple(rows)

    def config(self) -> LaboratoryConfig:
        return LaboratoryConfig(
            feature_config=FeatureConfig(ma_period=5, atr_period=5, rsi_period=5),
            strategy_test_equity=100_000,
            growth_starting_equity=10_000,
            growth_risk_fraction=0.0025,
            spread_price=0.0001,
            expected_slippage_price=0.00005,
        )

    def test_mt5_bar_open_time_is_not_used_as_completed_bar_availability(self) -> None:
        raw = self.raw_mt5_bars(4)
        completed = completed_feature_bars_from_mt5(raw)
        self.assertEqual(len(completed), 3)
        self.assertEqual(completed[0].source_open_at, raw[0].at)
        self.assertEqual(completed[0].at, raw[1].at)
        self.assertGreater(completed[0].at, completed[0].source_open_at)
        self.assertNotIn(raw[-1].at, {bar.source_open_at for bar in completed})

    def test_builtin_atr_target_is_simple_average_of_true_range(self) -> None:
        from dusty.features import FeatureBar

        start = datetime(2026, 1, 1, tzinfo=UTC)
        bars = (
            FeatureBar(start + timedelta(minutes=15), 10, 12, 9, 11, source_open_at=start),
            FeatureBar(start + timedelta(minutes=30), 11, 14, 10, 13, source_open_at=start + timedelta(minutes=15)),
            FeatureBar(start + timedelta(minutes=45), 13, 15, 12, 14, source_open_at=start + timedelta(minutes=30)),
        )
        # True ranges are 3, 4, 3; the built-in iATR target uses their simple moving average.
        values = atr(bars, 2)
        self.assertIsNone(values[0])
        self.assertAlmostEqual(values[1], 3.5)
        self.assertAlmostEqual(values[2], 3.5)

    def test_indicator_parity_csv_uses_availability_time_not_closed_bar_open_time(self) -> None:
        source_open = int(datetime(2026, 1, 1, 0, 0, tzinfo=UTC).timestamp())
        available = int(datetime(2026, 1, 1, 0, 15, tzinfo=UTC).timestamp())
        text = f"source_open_time,available_time,sma,ema,atr,rsi\n{source_open},{available},1.1,1.1,0.01,55\n"
        row = parse_mt5_indicator_csv(text)[0]
        self.assertEqual(row.source_open_at, datetime.fromtimestamp(source_open, tz=UTC))
        self.assertEqual(row.at, datetime.fromtimestamp(available, tz=UTC))
        self.assertGreater(row.at, row.source_open_at)

    def test_runtime_refuses_represented_but_unimplemented_scaling(self) -> None:
        with self.assertRaisesRegex(ValueError, "runtime_scaling_not_supported"):
            compile_strategy(self.spec(scale_in=1))
        with self.assertRaisesRegex(ValueError, "runtime_scaling_not_supported"):
            compile_strategy(self.spec(scale_out=(0.5,)))

    def test_runtime_honors_cooldown_after_exit(self) -> None:
        strategy = compile_strategy(self.spec(cooldown=2))
        start = datetime(2026, 1, 1, tzinfo=UTC)
        bars = []
        for index in range(12):
            close = 1.10 + index * 0.001
            bars.append(
                RuntimeBar.of(
                    start + timedelta(minutes=15 * index),
                    open=close,
                    high=close + 0.0004,
                    low=close - 0.0004,
                    close=close,
                    features={"close": close, "sma_5": 1.05, "atr": 0.01},
                )
            )
        trades = generate_runtime_trades(strategy, bars)
        self.assertGreaterEqual(len(trades), 2)
        gap_steps = int((trades[1].entry_at - trades[0].exit_at).total_seconds() // (15 * 60))
        self.assertGreaterEqual(gap_steps, 3)

    def test_dynamic_protection_research_is_not_falsely_claimed_as_mt5_manifest_parity(self) -> None:
        strategy = compile_strategy(self.spec(trailing="pct:0.001"))
        run = run_laboratory_from_bars(
            strategy,
            self.feature_bars(),
            symbol="EURUSD",
            economics=self.economics(),
            config=self.config(),
        )
        self.assertFalse(run.mt5_manifest_supported)
        self.assertEqual(run.minimum_lot_manifest, "")
        self.assertEqual(run.growth_manifest, "")
        self.assertIn("dynamic_trailing_manifest_not_supported", run.mt5_manifest_reasons)

    def test_mt5_reference_lab_drops_unproven_last_bar_and_matches_strategy_timeframe(self) -> None:
        raw = self.raw_mt5_bars()
        strategy = compile_strategy(self.spec())
        request = MT5BarRequest(
            terminal_path="terminal.exe",
            symbol="EURUSD",
            timeframe="M15",
            start=raw[0].at,
            end=raw[-1].at + timedelta(minutes=15),
        )
        run = run_laboratory_from_mt5(
            FakeWorker(raw),
            request,
            strategy,
            economics=self.economics(),
            config=self.config(),
        )
        self.assertEqual(run.bar_count, len(raw) - 1)
        self.assertTrue(run.mt5_manifest_supported)
        self.assertTrue(run.minimum_lot_manifest)
        self.assertTrue(run.growth_manifest)

        bad_request = MT5BarRequest(
            terminal_path="terminal.exe",
            symbol="EURUSD",
            timeframe="H1",
            start=raw[0].at,
            end=raw[-1].at + timedelta(hours=1),
        )
        with self.assertRaisesRegex(ValueError, "decision timeframe"):
            run_laboratory_from_mt5(
                FakeWorker(raw),
                bad_request,
                strategy,
                economics=self.economics(),
                config=self.config(),
            )


if __name__ == "__main__":
    unittest.main()
