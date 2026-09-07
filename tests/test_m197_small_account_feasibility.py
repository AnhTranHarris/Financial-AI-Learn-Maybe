from __future__ import annotations

import unittest

from dusty.experience import TradeSide
from dusty.markets import InstrumentEconomics
from dusty.risk import RiskConstitution
from dusty.small_account_feasibility import (
    CapitalBacktestEvidence,
    CapitalCompressionStatus,
    CapitalState,
    FeasibilityDecision,
    SmallAccountFeasibilityRequest,
    assess_capital_ladder,
    assess_small_account_feasibility,
    capital_compression_equities,
    run_capital_compression_campaign,
)


def economics(**overrides) -> InstrumentEconomics:
    values = dict(
        contract_size=100000.0,
        tick_size=0.00001,
        tick_value=1.0,
        volume_min=0.01,
        volume_step=0.01,
        volume_max=100.0,
        commission_per_lot=0.0,
        stop_level_points=0.0,
        point_size=0.00001,
    )
    values.update(overrides)
    return InstrumentEconomics(**values)


class M197SmallAccountFeasibilityTests(unittest.TestCase):
    def test_100_account_returns_no_trade_when_minimum_lot_exceeds_normal_risk(self):
        result = assess_small_account_feasibility(SmallAccountFeasibilityRequest(
            equity=100.0,
            side=TradeSide.LONG,
            entry_price=1.10000,
            strategy_stop_price=1.09900,
            economics=economics(),
        ))
        self.assertIs(result.decision, FeasibilityDecision.NO_TRADE)
        self.assertIs(result.capital_state, CapitalState.CAPITAL_INSUFFICIENT)
        self.assertEqual(result.approved_volume, 0.0)
        self.assertIn("broker_minimum_volume_exceeds_risk_budget", result.reasons)
        self.assertGreater(result.minimum_compliant_capital, 100.0)

    def test_100_account_can_be_tradeable_when_broker_economics_fit_constitution(self):
        result = assess_small_account_feasibility(SmallAccountFeasibilityRequest(
            equity=100.0,
            side=TradeSide.LONG,
            entry_price=1.10000,
            strategy_stop_price=1.09990,
            economics=economics(tick_value=0.1, volume_min=0.001, volume_step=0.001),
        ))
        self.assertIs(result.decision, FeasibilityDecision.TRADEABLE)
        self.assertIs(result.capital_state, CapitalState.MICRO_CAPITAL)
        self.assertLessEqual(result.effective_risk_fraction, RiskConstitution().normal_trade_risk)

    def test_broker_stop_floor_is_applied_before_sizing(self):
        result = assess_small_account_feasibility(SmallAccountFeasibilityRequest(
            equity=10000.0,
            side=TradeSide.LONG,
            entry_price=1.10000,
            strategy_stop_price=1.09995,
            economics=economics(stop_level_points=20.0),
        ))
        self.assertTrue(result.broker_stop_floor_applied)
        self.assertAlmostEqual(result.required_stop_distance, 0.00020)
        self.assertAlmostEqual(result.required_stop_price, 1.09980)
        self.assertIn("broker_minimum_stop_floor_applied", result.reasons)

    def test_short_stop_floor_moves_outward_not_toward_entry(self):
        result = assess_small_account_feasibility(SmallAccountFeasibilityRequest(
            equity=10000.0,
            side=TradeSide.SHORT,
            entry_price=1.10000,
            strategy_stop_price=1.10005,
            economics=economics(stop_level_points=20.0),
        ))
        self.assertAlmostEqual(result.required_stop_price, 1.10020)

    def test_margin_can_make_risk_feasible_minimum_lot_untradeable(self):
        result = assess_small_account_feasibility(SmallAccountFeasibilityRequest(
            equity=100.0,
            side=TradeSide.LONG,
            entry_price=1.10000,
            strategy_stop_price=1.09999,
            economics=economics(tick_value=0.1, volume_min=0.001, volume_step=0.001),
            margin_for_minimum_lot=40.0,
        ))
        self.assertIs(result.decision, FeasibilityDecision.NO_TRADE)
        self.assertIn("broker_minimum_volume_exceeds_margin_budget", result.reasons)
        self.assertAlmostEqual(result.margin_minimum_capital, 40.0 / RiskConstitution().margin_hard)

    def test_custom_risk_cannot_exceed_hard_constitutional_ceiling(self):
        with self.assertRaises(ValueError):
            SmallAccountFeasibilityRequest(
                equity=100.0,
                side=TradeSide.LONG,
                entry_price=1.1,
                strategy_stop_price=1.09,
                economics=economics(),
                risk_fraction=0.02,
            )

    def test_small_capital_never_rounds_volume_up_to_manufacture_trade(self):
        result = assess_small_account_feasibility(SmallAccountFeasibilityRequest(
            equity=399.0,
            side=TradeSide.LONG,
            entry_price=1.10000,
            strategy_stop_price=1.09900,
            economics=economics(),
        ))
        self.assertIs(result.decision, FeasibilityDecision.NO_TRADE)
        self.assertEqual(result.approved_volume, 0.0)

    def test_capital_ladder_preserves_same_broker_strategy_economics(self):
        request = SmallAccountFeasibilityRequest(
            equity=100.0,
            side=TradeSide.LONG,
            entry_price=1.10000,
            strategy_stop_price=1.09900,
            economics=economics(),
        )
        rows = assess_capital_ladder(request, (1000.0, 750.0, 562.5))
        self.assertEqual(tuple(row.equity for row in rows), (1000.0, 750.0, 562.5))
        self.assertEqual(len({row.minimum_compliant_capital for row in rows}), 1)

    def test_default_compression_starts_at_100k_and_reduces_exactly_25_percent(self):
        equities = capital_compression_equities()
        self.assertEqual(equities[0], 100000.0)
        self.assertAlmostEqual(equities[1], 75000.0)
        self.assertTrue(all(abs(right - left * 0.75) <= 1e-9 for left, right in zip(equities, equities[1:])))
        self.assertGreaterEqual(equities[-2], 100.0)
        self.assertLess(equities[-1], 100.0)

    def test_compression_stops_at_first_failed_rerun_and_records_bound(self):
        calls = []

        def evaluate(equity: float) -> CapitalBacktestEvidence:
            calls.append(equity)
            passed = equity >= 1000.0
            return CapitalBacktestEvidence(equity, passed, f"evidence-{len(calls)}")

        result = run_capital_compression_campaign(evaluate)
        self.assertIs(result.status, CapitalCompressionStatus.MINIMUM_BOUND_FOUND)
        self.assertTrue(result.lowest_passing_equity >= 1000.0)
        self.assertLess(result.first_failing_equity, 1000.0)
        self.assertEqual(tuple(row.equity for row in result.evidence), tuple(calls))
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_compression_can_prove_success_below_100(self):
        sequence = []

        def evaluate(equity: float) -> CapitalBacktestEvidence:
            sequence.append(equity)
            return CapitalBacktestEvidence(equity, True, f"below-{len(sequence)}")

        result = run_capital_compression_campaign(evaluate)
        self.assertIs(result.status, CapitalCompressionStatus.BELOW_TARGET_PROVEN)
        self.assertLess(result.lowest_passing_equity, 100.0)
        self.assertIsNone(result.first_failing_equity)

    def test_reference_backtest_must_pass_before_compression_can_continue(self):
        result = run_capital_compression_campaign(
            lambda equity: CapitalBacktestEvidence(equity, False, "reference-failed")
        )
        self.assertIs(result.status, CapitalCompressionStatus.REFERENCE_FAILED)
        self.assertEqual(len(result.evidence), 1)
        self.assertIsNone(result.lowest_passing_equity)
        self.assertEqual(result.first_failing_equity, 100000.0)

    def test_compression_rejects_wrong_equity_or_reused_evidence_identity(self):
        with self.assertRaises(ValueError):
            run_capital_compression_campaign(
                lambda equity: CapitalBacktestEvidence(equity - 1.0, True, "wrong-equity")
            )

        calls = 0

        def duplicate(equity: float) -> CapitalBacktestEvidence:
            nonlocal calls
            calls += 1
            return CapitalBacktestEvidence(equity, True, "same-evidence")

        with self.assertRaises(ValueError):
            run_capital_compression_campaign(duplicate)

    def test_result_has_no_operational_authority(self):
        result = assess_small_account_feasibility(SmallAccountFeasibilityRequest(
            equity=1000.0,
            side=TradeSide.LONG,
            entry_price=1.10000,
            strategy_stop_price=1.09900,
            economics=economics(),
        ))
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_fingerprint_is_deterministic(self):
        request = SmallAccountFeasibilityRequest(
            equity=1000.0,
            side=TradeSide.LONG,
            entry_price=1.10000,
            strategy_stop_price=1.09900,
            economics=economics(),
        )
        self.assertEqual(
            assess_small_account_feasibility(request).fingerprint,
            assess_small_account_feasibility(request).fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
