from datetime import datetime, timedelta, timezone
import unittest

from dusty.analysis_runtime import AnalysisReplay, AnalysisReplayTrade
from dusty.backtest import BacktestMode, PriceMark, trade_gross_pnl, trade_net_pnl
from dusty.experience import TradeSide
from dusty.markets import InstrumentEconomics
from dusty.multitimeframe_financial_backtest import (
    ResearchFriction,
    run_minimum_lot_financial_replay,
    summarize_minimum_lot_financial_replay,
)


UTC = timezone.utc
T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def economics() -> InstrumentEconomics:
    return InstrumentEconomics(
        contract_size=100_000.0,
        tick_size=0.00001,
        tick_value=1.0,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
        margin_rate=0.01,
        point_size=0.00001,
    )


def replay(*, open_position=None) -> AnalysisReplay:
    trade = AnalysisReplayTrade(
        TradeSide.LONG,
        T0,
        T0 + timedelta(minutes=30),
        1.1000,
        1.1010,
        7.0,
        "exit_graph_true",
    )
    return AnalysisReplay(
        "a" * 64,
        "b" * 64,
        (),
        (trade,),
        open_position,
    )


class M19610MultiTimeframeFinancialBacktestTests(unittest.TestCase):
    def test_bridge_forces_broker_minimum_lot_not_semantic_replay_volume(self):
        result = run_minimum_lot_financial_replay(
            replay(),
            (
                PriceMark(T0, "EURUSD", 1.1000),
                PriceMark(T0 + timedelta(minutes=15), "EURUSD", 1.1005),
                PriceMark(T0 + timedelta(minutes=30), "EURUSD", 1.1010),
            ),
            symbol="EURUSD",
            economics=economics(),
            starting_equity=100_000.0,
        )
        self.assertEqual(result.volume, 0.01)
        self.assertEqual(result.simulated_trades[0].volume, 0.01)
        self.assertNotEqual(result.simulated_trades[0].volume, 7.0)
        self.assertEqual(result.backtest.mode, BacktestMode.REALISTIC_LEDGER)
        self.assertEqual(result.backtest.trade_count, 1)

    def test_bridge_reuses_existing_ledger_cash_math(self):
        result = run_minimum_lot_financial_replay(
            replay(),
            (
                PriceMark(T0, "EURUSD", 1.1000),
                PriceMark(T0 + timedelta(minutes=15), "EURUSD", 1.1005),
                PriceMark(T0 + timedelta(minutes=30), "EURUSD", 1.1010),
            ),
            symbol="EURUSD",
            economics=economics(),
            starting_equity=100_000.0,
        )
        trade = result.simulated_trades[0]
        expected = trade_net_pnl(trade, economics())
        self.assertAlmostEqual(result.backtest.net_pnl, expected)
        self.assertAlmostEqual(result.backtest.ending_equity, 100_000.0 + expected)
        self.assertGreaterEqual(result.backtest.max_drawdown_fraction, 0.0)

    def test_summary_exposes_derived_metrics_without_backtest_schema_assumptions(self):
        friction = ResearchFriction(entry_cost_per_lot=8.0, exit_cost_per_lot=3.0, basis="test")
        result = run_minimum_lot_financial_replay(
            replay(),
            (
                PriceMark(T0, "EURUSD", 1.1000),
                PriceMark(T0 + timedelta(minutes=15), "EURUSD", 1.1005),
                PriceMark(T0 + timedelta(minutes=30), "EURUSD", 1.1010),
            ),
            symbol="EURUSD",
            economics=economics(),
            starting_equity=100_000.0,
            friction=friction,
        )
        summary = summarize_minimum_lot_financial_replay(result, economics())
        gross = sum(trade_gross_pnl(trade, economics()) for trade in result.simulated_trades)
        net = sum(trade_net_pnl(trade, economics()) for trade in result.simulated_trades)
        self.assertAlmostEqual(summary.gross_pnl, gross)
        self.assertAlmostEqual(summary.net_pnl, net)
        self.assertAlmostEqual(summary.total_costs, gross - net)
        self.assertAlmostEqual(summary.ending_equity, result.backtest.ending_equity)
        self.assertAlmostEqual(summary.max_drawdown_fraction, result.backtest.max_drawdown_fraction)
        self.assertAlmostEqual(
            summary.max_margin_used,
            max(point.margin_used for point in result.backtest.ledger),
        )
        self.assertEqual(summary.trade_count, result.backtest.trade_count)
        self.assertFalse(hasattr(result.backtest, "gross_pnl"))
        self.assertFalse(hasattr(result.backtest, "max_margin_used"))

    def test_explicit_friction_is_charged_and_never_silently_invented(self):
        no_cost = run_minimum_lot_financial_replay(
            replay(),
            (),
            symbol="EURUSD",
            economics=economics(),
            starting_equity=100_000.0,
        )
        friction = ResearchFriction(
            entry_cost_per_lot=8.0,
            exit_cost_per_lot=3.0,
            swap_cost_per_lot=2.0,
            basis="test-observed-costs",
        )
        stressed = run_minimum_lot_financial_replay(
            replay(),
            (),
            symbol="EURUSD",
            economics=economics(),
            starting_equity=100_000.0,
            friction=friction,
        )
        expected_cost = (8.0 + 3.0 + 2.0) * economics().volume_min
        self.assertAlmostEqual(no_cost.backtest.net_pnl - stressed.backtest.net_pnl, expected_cost)
        stressed_trade = stressed.simulated_trades[0]
        self.assertAlmostEqual(
            trade_gross_pnl(stressed_trade, economics()) - trade_net_pnl(stressed_trade, economics()),
            expected_cost,
        )
        self.assertEqual(stressed.friction.basis, "test-observed-costs")

    def test_open_semantic_replay_fails_closed(self):
        from dusty.strategy_v3 import PositionView

        with self.assertRaisesRegex(ValueError, "flat completed semantic replay"):
            run_minimum_lot_financial_replay(
                replay(open_position=PositionView(TradeSide.LONG, 1.1, 1.09, 1.0, 1)),
                (),
                symbol="EURUSD",
                economics=economics(),
                starting_equity=100_000.0,
            )

    def test_mixed_symbol_marks_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "cannot mix symbols"):
            run_minimum_lot_financial_replay(
                replay(),
                (PriceMark(T0, "GBPUSD", 1.3),),
                symbol="EURUSD",
                economics=economics(),
                starting_equity=100_000.0,
            )

    def test_invalid_friction_and_equity_fail_closed(self):
        with self.assertRaises(ValueError):
            ResearchFriction(entry_cost_per_lot=-1.0)
        with self.assertRaises(ValueError):
            run_minimum_lot_financial_replay(
                replay(), (), symbol="EURUSD", economics=economics(), starting_equity=0.0
            )

    def test_adapter_has_no_operational_authority(self):
        result = run_minimum_lot_financial_replay(
            replay(), (), symbol="EURUSD", economics=economics(), starting_equity=100_000.0
        )
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_empty_flat_replay_is_a_valid_zero_trade_financial_result(self):
        empty = AnalysisReplay("a" * 64, "b" * 64, (), (), None)
        result = run_minimum_lot_financial_replay(
            empty,
            (PriceMark(T0, "EURUSD", 1.1),),
            symbol="EURUSD",
            economics=economics(),
            starting_equity=100_000.0,
        )
        summary = summarize_minimum_lot_financial_replay(result, economics())
        self.assertEqual(result.backtest.trade_count, 0)
        self.assertEqual(result.backtest.net_pnl, 0.0)
        self.assertEqual(result.backtest.ending_equity, 100_000.0)
        self.assertEqual(summary.gross_pnl, 0.0)
        self.assertEqual(summary.net_pnl, 0.0)
        self.assertEqual(summary.total_costs, 0.0)
        self.assertEqual(summary.max_margin_used, 0.0)


if __name__ == "__main__":
    unittest.main()
