from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from dusty.capital_opportunity import (
    CertifiedOpportunity,
    SettledCapitalState,
    allocate_certified_opportunities,
)
from dusty.experience import TradeSide
from dusty.forecast_council import ForecastTradeAction
from dusty.forecast_demo import (
    DemoForecastObservation,
    DemoForecastOutcome,
    ForecastDeskEvidence,
    FrozenForecastChampion,
    SQLiteForecastDemoLedger,
    certify_forecast_demo_campaign,
)
from dusty.market_clock import MarketClockAssessment, MarketClockState


UTC = timezone.utc
NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def clock(opened: bool = True) -> MarketClockAssessment:
    return MarketClockAssessment(
        MarketClockState.OPEN if opened else MarketClockState.SCHEDULED_CLOSED,
        True,
        opened,
        opened,
        True,
        True,
        None,
        ("open" if opened else "scheduled_close",),
    )


class M93ForecastDemoTests(unittest.TestCase):
    def observation(self) -> DemoForecastObservation:
        return DemoForecastObservation("o1", "desk-1", NOW, "a" * 64, "b" * 64, "c" * 64, MarketClockState.OPEN, ForecastTradeAction.ENTER_LONG)

    def test_champion_is_content_addressed_and_frozen(self):
        champion = FrozenForecastChampion("c1", "a" * 64, "b" * 64, "c" * 64, NOW)
        self.assertEqual(len(champion.fingerprint), 64)

    def test_demo_ledger_requires_observation_before_outcome_and_is_append_only(self):
        ledger = SQLiteForecastDemoLedger()
        outcome = DemoForecastOutcome("o1", NOW + timedelta(hours=1), 0.01, 10.0, 1.0)
        with self.assertRaisesRegex(ValueError, "no prior observation"):
            ledger.append_outcome(outcome)
        ledger.append_observation(self.observation())
        ledger.append_outcome(outcome)
        self.assertEqual(ledger.counts(), (1, 1))
        before = ledger.evidence_hash
        with self.assertRaises(sqlite3.IntegrityError):
            ledger.append_observation(self.observation())
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append_only"):
            ledger._db.execute("UPDATE forecast_observations SET action='wait'")
        self.assertEqual(ledger.evidence_hash, before)
        self.assertTrue(ledger.integrity_ok())
        ledger.close()

    def test_six_independent_profitable_desks_certify_demo_not_live(self):
        rows = tuple(
            ForecastDeskEvidence(f"desk-{index}", "a" * 64, f"session-{index}", 40, 0.05, 100, 0.04, 0, 12)
            for index in range(6)
        )
        result = certify_forecast_demo_campaign(rows)
        self.assertTrue(result.certified)
        self.assertFalse(result.live_write_authorized)
        self.assertEqual(result.passing_desks, 6)

    def test_scheduled_closures_are_not_demo_failures(self):
        rows = tuple(
            ForecastDeskEvidence(f"desk-{index}", "a" * 64, f"session-{index}", 40, 0.05, 100, 0.04, 0, 1000)
            for index in range(6)
        )
        self.assertTrue(certify_forecast_demo_campaign(rows).certified)


class M94CapitalOpportunityTests(unittest.TestCase):
    def opportunity(self, index: int, *, opened: bool = True) -> CertifiedOpportunity:
        return CertifiedOpportunity(
            f"op-{index}", "EURUSD", TradeSide.LONG, "a" * 64, "b" * 64,
            300, 0.004, 0.01 - index / 10000, NOW + timedelta(hours=1), True, clock(opened),
        )

    def test_settled_realized_gains_unlock_more_opportunities_not_more_trade_risk(self):
        opportunities = tuple(self.opportunity(index) for index in range(4))
        initial = SettledCapitalState(1000, 0, 0, 0, 1000, 1000)
        grown = SettledCapitalState(1000, 0, 0, 1000, 2000, 2000)
        first = allocate_certified_opportunities(initial, opportunities, at=NOW)
        second = allocate_certified_opportunities(grown, opportunities, at=NOW)
        self.assertEqual(len(first.allocations), 1)
        self.assertEqual(len(second.allocations), 3)
        self.assertTrue(all(row.risk_fraction == 0.004 for row in second.allocations))

    def test_floating_profit_does_not_unlock_growth_slots(self):
        capital = SettledCapitalState(1000, 0, 0, 0, 1000, 1500)
        result = allocate_certified_opportunities(capital, tuple(self.opportunity(index) for index in range(3)), at=NOW)
        self.assertEqual(result.available_slots, 1)
        self.assertEqual(len(result.allocations), 1)

    def test_floating_loss_reduces_conservative_capital(self):
        capital = SettledCapitalState(1000, 0, 0, 0, 1000, 250)
        result = allocate_certified_opportunities(capital, (self.opportunity(1),), at=NOW)
        self.assertFalse(result.allocations)
        self.assertEqual(result.conservative_capital, 250)

    def test_goals_never_force_a_trade_while_market_is_closed(self):
        capital = SettledCapitalState(1000, 0, 0, 0, 1000, 1000)
        result = allocate_certified_opportunities(capital, (self.opportunity(1, opened=False),), at=NOW, daily_goal_fraction=0.05)
        self.assertFalse(result.allocations)
        self.assertFalse(result.daily_goal_forced_trade)
        self.assertTrue(any("scheduled_closed" in reason for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
