from __future__ import annotations

import unittest

from dusty.capital import (
    PositionSizingRequest,
    SizingMode,
    minimum_viable_capital,
    size_position,
)
from dusty.experience import TradeSide
from dusty.growth import (
    CapitalFeedbackClass,
    CapitalHealth,
    CapitalState,
    ResearchCycle,
    assess_research_cycle,
    capital_feedback,
    classify_capital_health,
    compression_ladder,
    deployment_multiplier,
    eligible_strategies_at_capital,
    next_cycle_capital,
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
    OutcomeQuality,
    RiskConstitution,
    RiskState,
    TradeRiskRequest,
    assess_trade_risk,
    classify_outcome,
    risk_state,
    stop_change_allowed,
)


FX = InstrumentEconomics(
    contract_size=100_000,
    tick_size=0.0001,
    tick_value=10.0,
    volume_min=0.01,
    volume_step=0.01,
    volume_max=100.0,
    margin_rate=0.01,
    commission_per_lot=7.0,
)


class MoneyManagerTests(unittest.TestCase):
    def test_micro_capital_refuses_minimum_lot_instead_of_raising_risk(self) -> None:
        request = PositionSizingRequest(
            equity=100.0,
            risk_fraction=0.0025,
            entry_price=1.1000,
            stop_price=1.0960,
            economics=FX,
        )
        result = size_position(request)
        self.assertFalse(result.feasible)
        self.assertEqual(result.approved_volume, 0.0)
        self.assertIn("broker_minimum_volume_exceeds_risk_budget", result.reasons)
        self.assertGreater(minimum_viable_capital(request), 100.0)

    def test_growth_size_rounds_down_and_never_exceeds_allowed_loss(self) -> None:
        request = PositionSizingRequest(
            equity=10_000.0,
            risk_fraction=0.0025,
            entry_price=1.1000,
            stop_price=1.0960,
            economics=FX,
            spread_price=0.0001,
            expected_slippage_price=0.0001,
        )
        result = size_position(request)
        self.assertTrue(result.feasible)
        self.assertLessEqual(result.expected_loss, result.allowed_loss)
        self.assertLessEqual(result.approved_volume, result.raw_volume)

    def test_strategy_validation_and_growth_sizing_are_explicitly_separate(self) -> None:
        request = PositionSizingRequest(
            equity=100_000.0,
            risk_fraction=0.0025,
            entry_price=1.1000,
            stop_price=1.0960,
            economics=FX,
        )
        strategy_test = size_position(request, mode=SizingMode.MINIMUM_LOT_STRATEGY_TEST)
        growth = size_position(request, mode=SizingMode.GROWTH_RISK)
        self.assertEqual(strategy_test.approved_volume, FX.volume_min)
        self.assertGreater(growth.approved_volume, strategy_test.approved_volume)


class RiskConstitutionTests(unittest.TestCase):
    def _snapshot(self, **changes: float) -> AccountRiskSnapshot:
        values = {
            "equity": 100.0,
            "balance": 100.0,
            "high_water_mark": 100.0,
            "day_start_equity": 100.0,
            "week_start_equity": 100.0,
            "margin_used": 0.0,
            "portfolio_heat": 0.0,
            "same_symbol_heat": 0.0,
        }
        values.update(changes)
        return AccountRiskSnapshot(**values)

    def test_drawdown_derisks_without_revenge_sizing(self) -> None:
        snapshot = self._snapshot(
            equity=97.0,
            balance=97.0,
            high_water_mark=100.0,
            day_start_equity=97.0,
            week_start_equity=97.0,
        )
        self.assertIs(risk_state(snapshot), RiskState.CAUTION)
        request = TradeRiskRequest(
            proposed_risk=0.0025,
            post_trade_portfolio_heat=0.0025,
            post_trade_same_symbol_heat=0.0025,
            post_trade_margin_used=5.0,
            has_initial_stop=True,
        )
        assessment = assess_trade_risk(snapshot, request)
        self.assertTrue(assessment.allowed)
        self.assertEqual(assessment.risk_multiplier, 0.75)

    def test_hard_rules_veto_profitable_or_confident_strategy(self) -> None:
        request = TradeRiskRequest(
            proposed_risk=0.011,
            post_trade_portfolio_heat=0.021,
            post_trade_same_symbol_heat=0.011,
            post_trade_margin_used=31.0,
            has_initial_stop=False,
            stop_widening=True,
            martingale=True,
        )
        assessment = assess_trade_risk(self._snapshot(), request)
        self.assertFalse(assessment.allowed)
        self.assertIn("trade_risk_ceiling", assessment.reasons)
        self.assertIn("initial_stop_required", assessment.reasons)
        self.assertIn("martingale_prohibited", assessment.reasons)

    def test_protective_stops_can_only_tighten(self) -> None:
        self.assertTrue(stop_change_allowed(TradeSide.LONG, 1.0950, 1.0970))
        self.assertFalse(stop_change_allowed(TradeSide.LONG, 1.0950, 1.0900))
        self.assertTrue(stop_change_allowed(TradeSide.SHORT, 1.1050, 1.1030))
        self.assertFalse(stop_change_allowed(TradeSide.SHORT, 1.1050, 1.1100))

    def test_good_losses_and_bad_wins_are_distinct(self) -> None:
        self.assertIs(classify_outcome(pnl=-10.0, rules_followed=True), OutcomeQuality.GOOD_LOSS)
        self.assertIs(classify_outcome(pnl=10.0, rules_followed=False), OutcomeQuality.BAD_WIN)


class QuantPortfolioManagerTests(unittest.TestCase):
    def test_quant_pm_cannot_create_risk_and_caps_crowded_factor(self) -> None:
        candidates = (
            PortfolioCandidate("a", "EURUSD", 2.0, 0.10, 0.01, (("USD_SHORT", 1.0),)),
            PortfolioCandidate("b", "GBPUSD", 2.0, 0.10, 0.01, (("USD_SHORT", 1.0),)),
            PortfolioCandidate("c", "WTI", 1.0, 0.20, 0.01, (("ENERGY", 1.0),)),
        )
        allocation = allocate_portfolio(
            candidates,
            total_risk_budget=0.02,
            policy=QuantPortfolioPolicy(max_symbol_heat=0.01, max_factor_heat=0.0075),
            correlations={("a", "b"): 0.95},
        )
        self.assertLessEqual(allocation.portfolio_heat, 0.02)
        self.assertAlmostEqual(allocation.portfolio_heat + allocation.unallocated_risk, 0.02)
        factor = dict(allocation.factor_heat)
        self.assertLessEqual(abs(factor.get("USD_SHORT", 0.0)), 0.0075 + 1e-12)

    def test_inverse_volatility_prefers_lower_volatility_under_equal_caps(self) -> None:
        candidates = (
            PortfolioCandidate("low", "A", 1.0, 0.10, 0.02),
            PortfolioCandidate("high", "B", 1.0, 0.20, 0.02),
        )
        allocation = allocate_portfolio(
            candidates,
            total_risk_budget=0.015,
            method=AllocationMethod.INVERSE_VOLATILITY,
            policy=QuantPortfolioPolicy(max_symbol_heat=0.02, max_factor_heat=0.02),
        )
        risks = {item.strategy_hash: item.risk for item in allocation.allocations}
        self.assertGreater(risks["low"], risks["high"])


class GrowthManagerTests(unittest.TestCase):
    def test_external_contributions_are_not_mislabeled_as_trading_profit(self) -> None:
        state = CapitalState(100.0, 150.0, 150.0, net_external_flows=50.0)
        self.assertEqual(state.trading_pnl, 0.0)
        self.assertEqual(state.growth_fraction, 0.0)

    def test_growth_never_expands_percentage_risk_envelope(self) -> None:
        thriving = CapitalState(100.0, 105.0, 105.0)
        health = classify_capital_health(thriving)
        self.assertIs(health, CapitalHealth.THRIVING)
        self.assertLessEqual(deployment_multiplier(health), 1.0)

    def test_loss_increases_research_priority_not_position_size(self) -> None:
        feedback = capital_feedback(pnl=-1.0, rules_followed=True, drawdown_fraction=0.01)
        self.assertIs(feedback.classification, CapitalFeedbackClass.VALID_LOSS)
        self.assertTrue(feedback.research_priority_increased)
        bad_win = capital_feedback(pnl=10.0, rules_followed=False, drawdown_fraction=0.0)
        self.assertIs(bad_win.classification, CapitalFeedbackClass.GOVERNANCE_FAILURE)

    def test_capital_compression_is_earned_and_reaches_100_floor(self) -> None:
        self.assertEqual(next_cycle_capital(1000.0, 800.0, cycle_passed=False), 1000.0)
        self.assertEqual(next_cycle_capital(1000.0, 800.0, cycle_passed=True), 800.0)
        ladder = compression_ladder(10_000.0, floor=100.0, ratio=0.5)
        self.assertEqual(ladder[0], 10_000.0)
        self.assertEqual(ladder[-1], 100.0)
        self.assertEqual(
            eligible_strategies_at_capital(100.0, {"a": 80.0, "b": 120.0}),
            ("a",),
        )

    def test_research_cycle_requires_growth_rules_statistics_execution_and_diversity(self) -> None:
        good = ResearchCycle(1000.0, 1030.0, 0.02, 8, True, True, True, 0.30)
        self.assertTrue(assess_research_cycle(good).passed)
        lucky = ResearchCycle(1000.0, 1100.0, 0.02, 8, True, True, True, 0.90)
        assessment = assess_research_cycle(lucky)
        self.assertFalse(assessment.passed)
        self.assertIn("profit_concentration_failed", assessment.reasons)


if __name__ == "__main__":
    unittest.main()
