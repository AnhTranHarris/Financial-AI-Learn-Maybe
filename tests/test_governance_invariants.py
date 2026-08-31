from __future__ import annotations

import math
import unittest

from dusty.capital import PositionSizingRequest, size_position
from dusty.growth import (
    CapitalHealth,
    CapitalState,
    ResearchCycle,
    assess_research_cycle,
    classify_capital_health,
    compression_ladder,
    deployment_multiplier,
    eligible_strategies_at_capital,
)
from dusty.markets import InstrumentEconomics
from dusty.portfolio import (
    AllocationMethod,
    PortfolioCandidate,
    QuantPortfolioPolicy,
    allocate_portfolio,
)
from dusty.risk import (
    AccountRiskSnapshot,
    RiskState,
    TradeRiskRequest,
    assess_trade_risk,
    risk_state,
)


FX = InstrumentEconomics(
    contract_size=100_000.0,
    tick_size=0.0001,
    tick_value=10.0,
    volume_min=0.01,
    volume_step=0.01,
    volume_max=100.0,
    margin_rate=0.01,
    commission_per_lot=7.0,
)


class ZeroCapitalFailClosedTests(unittest.TestCase):
    def test_zero_equity_is_a_governed_terminal_state_not_an_exception(self) -> None:
        snapshot = AccountRiskSnapshot(
            equity=0.0,
            balance=0.0,
            high_water_mark=100.0,
            day_start_equity=100.0,
            week_start_equity=100.0,
            margin_used=0.0,
            portfolio_heat=0.0,
        )
        self.assertIs(risk_state(snapshot), RiskState.FAILED)
        assessment = assess_trade_risk(
            snapshot,
            TradeRiskRequest(0.0025, 0.0025, 0.0025, 0.0, True),
        )
        self.assertFalse(assessment.allowed)
        self.assertEqual(assessment.risk_multiplier, 0.0)
        self.assertIn("account_state:failed", assessment.reasons)

        capital = CapitalState(100.0, 0.0, 100.0)
        health = classify_capital_health(capital)
        self.assertIs(health, CapitalHealth.CAPITAL_INSUFFICIENT)
        self.assertEqual(deployment_multiplier(health), 0.0)
        self.assertEqual(eligible_strategies_at_capital(0.0, {"a": 50.0}), ())

    def test_total_loss_research_cycle_is_recordable_and_fails(self) -> None:
        cycle = ResearchCycle(100.0, 0.0, 1.0, 10, True, True, True, 0.2)
        assessment = assess_research_cycle(cycle)
        self.assertFalse(assessment.passed)
        self.assertEqual(assessment.growth_fraction, -1.0)
        self.assertIn("growth_requirement_failed", assessment.reasons)
        self.assertIn("drawdown_requirement_failed", assessment.reasons)


class NonFiniteInputTests(unittest.TestCase):
    def test_financial_boundaries_reject_nan_and_infinity(self) -> None:
        with self.assertRaises(ValueError):
            InstrumentEconomics(math.nan, 0.0001, 10.0, 0.01, 0.01, 10.0)
        with self.assertRaises(ValueError):
            PositionSizingRequest(math.inf, 0.0025, 1.1, 1.09, FX)
        with self.assertRaises(ValueError):
            PortfolioCandidate("x", "EURUSD", math.nan, 0.1, 0.01)
        with self.assertRaises(ValueError):
            TradeRiskRequest(math.nan, 0.01, 0.01, 1.0, True)
        with self.assertRaises(ValueError):
            CapitalState(100.0, math.nan, 100.0)


class PositionSizingInvariantTests(unittest.TestCase):
    def test_grid_never_rounds_up_or_exceeds_growth_risk_budget(self) -> None:
        for equity in (150.0, 500.0, 1_000.0, 10_000.0, 100_000.0):
            for risk_fraction in (0.001, 0.0025, 0.005, 0.01):
                for stop_distance in (0.0005, 0.0010, 0.0025, 0.0100):
                    with self.subTest(
                        equity=equity,
                        risk_fraction=risk_fraction,
                        stop_distance=stop_distance,
                    ):
                        request = PositionSizingRequest(
                            equity=equity,
                            risk_fraction=risk_fraction,
                            entry_price=1.1000,
                            stop_price=1.1000 - stop_distance,
                            economics=FX,
                            spread_price=0.0001,
                            expected_slippage_price=0.0001,
                        )
                        result = size_position(request)
                        self.assertLessEqual(result.approved_volume, result.raw_volume + 1e-12)
                        self.assertLessEqual(result.expected_loss, result.allowed_loss + 1e-9)
                        self.assertLessEqual(result.effective_risk_fraction, risk_fraction + 1e-12)
                        if not result.feasible:
                            self.assertEqual(result.approved_volume, 0.0)
                            self.assertEqual(result.expected_loss, 0.0)


class PortfolioInvariantTests(unittest.TestCase):
    def test_all_methods_respect_budget_symbol_and_factor_heat(self) -> None:
        candidates = (
            PortfolioCandidate("a", "EURUSD", 2.0, 0.10, 0.008, (("USD", -1.0),)),
            PortfolioCandidate("b", "GBPUSD", 1.5, 0.12, 0.008, (("USD", -0.8),)),
            PortfolioCandidate("c", "USDJPY", 1.2, 0.15, 0.008, (("USD", 1.0),)),
            PortfolioCandidate("d", "WTI", 1.1, 0.25, 0.008, (("ENERGY", 1.0),)),
        )
        policy = QuantPortfolioPolicy(max_symbol_heat=0.008, max_factor_heat=0.01)
        correlations = {("a", "b"): 0.8, ("a", "c"): -0.5, ("b", "c"): -0.4}
        for method in AllocationMethod:
            for budget in (0.0, 0.005, 0.015, 0.02):
                with self.subTest(method=method, budget=budget):
                    allocation = allocate_portfolio(
                        candidates,
                        total_risk_budget=budget,
                        correlations=correlations,
                        policy=policy,
                        method=method,
                    )
                    self.assertLessEqual(allocation.portfolio_heat, budget + 1e-12)
                    self.assertAlmostEqual(
                        allocation.portfolio_heat + allocation.unallocated_risk,
                        budget,
                        places=12,
                    )
                    self.assertTrue(
                        all(value <= policy.max_symbol_heat + 1e-12 for _, value in allocation.symbol_heat)
                    )
                    self.assertTrue(
                        all(abs(value) <= policy.max_factor_heat + 1e-12 for _, value in allocation.factor_heat)
                    )


class CapitalCompressionInvariantTests(unittest.TestCase):
    def test_compression_is_monotone_bounded_and_never_below_floor(self) -> None:
        for start in (100.0, 250.0, 1_000.0, 10_000.0, 1_000_000.0):
            for ratio in (0.25, 0.5, 0.8, 0.95):
                with self.subTest(start=start, ratio=ratio):
                    ladder = compression_ladder(start, floor=100.0, ratio=ratio, max_levels=64)
                    self.assertEqual(ladder[0], start)
                    self.assertTrue(all(left >= right for left, right in zip(ladder, ladder[1:])))
                    self.assertTrue(all(value >= 100.0 for value in ladder))
                    self.assertLessEqual(len(ladder), 64)


if __name__ == "__main__":
    unittest.main()
