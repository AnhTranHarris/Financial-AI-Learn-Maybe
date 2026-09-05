from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from dusty.capital_opportunity import CapitalOpportunityPolicy, CertifiedOpportunity, SettledCapitalState
from dusty.demo_capital_allocator import (
    DEFAULT_DEMO_DESK_CAPITAL,
    DemoCapitalReservation,
    DemoDeskCapitalState,
    DemoOpportunityEvidence,
    allocate_demo_capital,
)
from dusty.experience import TradeSide
from dusty.market_clock import MarketClockAssessment, MarketClockState


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def clock(opened: bool = True) -> MarketClockAssessment:
    return MarketClockAssessment(
        MarketClockState.OPEN if opened else MarketClockState.SCHEDULED_CLOSED,
        True,
        opened,
        opened,
        True,
        True,
        None if opened else NOW + timedelta(hours=12),
        ("open" if opened else "scheduled_close",),
    )


class M186DemoCapitalAllocatorTests(unittest.TestCase):
    def desk(self, name: str = "desk-1", *, session: str | None = None) -> DemoDeskCapitalState:
        return DemoDeskCapitalState.fresh(name, "generation-1", fp(session or f"session-{name}"))

    def opportunity(
        self,
        index: int,
        *,
        minimum: float = 1_000.0,
        risk: float = 0.0025,
        edge: float = 0.01,
        opened: bool = True,
        certified: bool = True,
    ) -> CertifiedOpportunity:
        return CertifiedOpportunity(
            f"op-{index}",
            "EURUSD",
            TradeSide.LONG,
            fp("strategy"),
            fp("forecast-model"),
            minimum,
            risk,
            edge,
            NOW + timedelta(hours=1),
            True,
            clock(opened),
            certified,
        )

    def evidence(self, index: int, *, shadow: str | None = None) -> DemoOpportunityEvidence:
        return DemoOpportunityEvidence(
            f"op-{index}",
            fp("strategy"),
            fp(f"evaluation-{index}"),
            fp("campaign"),
            fp(shadow or f"shadow-{index}"),
        )

    def reservation(
        self,
        desk: DemoDeskCapitalState,
        index: int,
        *,
        risk: float = 0.0025,
        minimum: float = 1_000.0,
        shadow: str | None = None,
        session_fingerprint: str | None = None,
    ) -> DemoCapitalReservation:
        evidence = self.evidence(index, shadow=shadow)
        return DemoCapitalReservation(
            desk.desk_id,
            desk.generation_id,
            session_fingerprint or desk.session_fingerprint,
            f"op-{index}",
            evidence.strategy_fingerprint,
            evidence.evaluation_fingerprint,
            evidence.campaign_fingerprint,
            evidence.shadow_fingerprint,
            risk,
            DEFAULT_DEMO_DESK_CAPITAL * risk,
            minimum,
            NOW - timedelta(minutes=1),
        )

    def test_fresh_demo_desk_defaults_to_independent_5000_account(self) -> None:
        first = self.desk("desk-1")
        second = self.desk("desk-2")
        self.assertEqual(first.capital.starting_balance, 5_000.0)
        self.assertEqual(first.capital.balance, 5_000.0)
        self.assertEqual(first.capital.equity, 5_000.0)
        self.assertEqual(second.capital.balance, 5_000.0)
        self.assertNotEqual(first.session_fingerprint, second.session_fingerprint)
        self.assertFalse(first.live_write_authority)
        self.assertFalse(first.cross_desk_transfer_authority)

    def test_one_desk_cannot_borrow_another_desks_capital(self) -> None:
        poor = self.desk("desk-poor")
        rich = DemoDeskCapitalState.fresh("desk-rich", "generation-1", fp("rich-session"), starting_capital=20_000)
        opportunity = self.opportunity(1, minimum=6_000)
        evidence = self.evidence(1)
        poor_result = allocate_demo_capital(poor, (opportunity,), (evidence,), at=NOW)
        rich_result = allocate_demo_capital(rich, (opportunity,), (evidence,), at=NOW)
        self.assertFalse(poor_result.reservations)
        self.assertTrue(any("insufficient_conservative_capital" in reason for reason in poor_result.reasons))
        self.assertEqual(len(rich_result.reservations), 1)

    def test_floating_profit_on_one_desk_does_not_unlock_slots_on_another(self) -> None:
        base = self.desk("desk-base")
        floating = DemoDeskCapitalState(
            "desk-floating",
            "generation-1",
            fp("floating-session"),
            SettledCapitalState(5_000, 0, 0, 0, 5_000, 7_000),
        )
        opportunities = (self.opportunity(1), self.opportunity(2))
        evidence = (self.evidence(1), self.evidence(2))
        base_result = allocate_demo_capital(base, opportunities, evidence, at=NOW)
        floating_result = allocate_demo_capital(floating, opportunities, evidence, at=NOW)
        self.assertEqual(len(base_result.reservations), 1)
        self.assertEqual(len(floating_result.reservations), 1)
        self.assertEqual(base_result.available_slots_before_new, 1)
        self.assertEqual(floating_result.available_slots_before_new, 1)

    def test_settled_realized_gain_unlocks_extra_slot_only_on_that_desk(self) -> None:
        grown = DemoDeskCapitalState(
            "desk-grown",
            "generation-1",
            fp("grown-session"),
            SettledCapitalState(5_000, 0, 0, 500, 5_500, 5_500),
        )
        unchanged = self.desk("desk-unchanged")
        opportunities = (self.opportunity(1), self.opportunity(2))
        evidence = (self.evidence(1), self.evidence(2))
        grown_result = allocate_demo_capital(grown, opportunities, evidence, at=NOW)
        unchanged_result = allocate_demo_capital(unchanged, opportunities, evidence, at=NOW)
        self.assertEqual(len(grown_result.reservations), 2)
        self.assertEqual(len(unchanged_result.reservations), 1)

    def test_active_same_desk_reservation_consumes_slot_risk_and_capital(self) -> None:
        desk = DemoDeskCapitalState(
            "desk-1",
            "generation-1",
            fp("session-desk-1"),
            SettledCapitalState(5_000, 0, 0, 500, 5_500, 5_500),
        )
        existing = self.reservation(desk, 1, minimum=2_000)
        result = allocate_demo_capital(
            desk,
            (self.opportunity(2, minimum=4_000),),
            (self.evidence(2),),
            active_firm_reservations=(existing,),
            at=NOW,
        )
        self.assertEqual(result.capital_already_reserved, 2_000)
        self.assertEqual(result.risk_fraction_already_reserved, 0.0025)
        self.assertEqual(result.available_slots_before_new, 1)
        self.assertFalse(result.reservations)
        self.assertTrue(any("insufficient_conservative_capital" in reason for reason in result.reasons))

    def test_session_restart_cannot_erase_active_risk(self) -> None:
        desk = self.desk("desk-1")
        stale_session = fp("previous-session")
        active = self.reservation(desk, 1, session_fingerprint=stale_session)
        with self.assertRaisesRegex(ValueError, "session drift"):
            allocate_demo_capital(
                desk,
                (self.opportunity(2),),
                (self.evidence(2),),
                active_firm_reservations=(active,),
                at=NOW,
            )

    def test_generation_drift_with_active_same_desk_risk_fails_closed(self) -> None:
        desk = self.desk("desk-1")
        active = replace(self.reservation(desk, 1), generation_id="generation-old")
        with self.assertRaisesRegex(ValueError, "generation drift"):
            allocate_demo_capital(
                desk,
                (self.opportunity(2),),
                (self.evidence(2),),
                active_firm_reservations=(active,),
                at=NOW,
            )

    def test_same_shadow_evidence_cannot_masquerade_as_independent_desk_session(self) -> None:
        desk_one = self.desk("desk-1")
        desk_two = self.desk("desk-2")
        shared_shadow = "shared-shadow"
        claimed = self.reservation(desk_one, 1, shadow=shared_shadow)
        result = allocate_demo_capital(
            desk_two,
            (self.opportunity(2),),
            (self.evidence(2, shadow=shared_shadow),),
            active_firm_reservations=(claimed,),
            at=NOW,
        )
        self.assertFalse(result.reservations)
        self.assertIn("op-2:shadow_evidence_already_claimed", result.reasons)

    def test_duplicate_batch_identity_and_evidence_identity_fail_closed(self) -> None:
        desk = self.desk()
        opportunity = self.opportunity(1)
        evidence = self.evidence(1)
        with self.assertRaisesRegex(ValueError, "duplicate opportunity"):
            allocate_demo_capital(desk, (opportunity, opportunity), (evidence,), at=NOW)
        with self.assertRaisesRegex(ValueError, "duplicate demo opportunity evidence"):
            allocate_demo_capital(desk, (opportunity,), (evidence, evidence), at=NOW)

    def test_strategy_identity_drift_fails_closed(self) -> None:
        desk = self.desk()
        evidence = replace(self.evidence(1), strategy_fingerprint=fp("different-strategy"))
        with self.assertRaisesRegex(ValueError, "strategy identity drift"):
            allocate_demo_capital(desk, (self.opportunity(1),), (evidence,), at=NOW)

    def test_closed_market_and_uncertified_opportunity_remain_wait_not_forced_trade(self) -> None:
        desk = self.desk()
        rows = (self.opportunity(1, opened=False), self.opportunity(2, certified=False))
        evidence = (self.evidence(1), self.evidence(2))
        result = allocate_demo_capital(desk, rows, evidence, at=NOW)
        self.assertFalse(result.reservations)
        self.assertTrue(any("market_clock_scheduled_closed" in reason for reason in result.reasons))
        self.assertTrue(any("not_certified" in reason for reason in result.reasons))

    def test_risk_constitution_is_not_relaxed_for_demo_allocator(self) -> None:
        desk = self.desk()
        row = self.opportunity(1, risk=0.006)
        result = allocate_demo_capital(desk, (row,), (self.evidence(1),), at=NOW)
        self.assertFalse(result.reservations)
        self.assertTrue(any("per_trade_risk_exceeded" in reason for reason in result.reasons))

    def test_decision_and_reservations_have_no_live_or_promotion_authority(self) -> None:
        desk = self.desk()
        first = allocate_demo_capital(desk, (self.opportunity(1),), (self.evidence(1),), at=NOW)
        second = allocate_demo_capital(desk, (self.opportunity(1),), (self.evidence(1),), at=NOW)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(first.reservations), 1)
        reservation = first.reservations[0]
        self.assertFalse(first.live_write_authority)
        self.assertFalse(first.promotion_authority)
        self.assertFalse(first.cross_desk_transfer_authority)
        self.assertFalse(reservation.live_write_authority)
        self.assertFalse(reservation.promotion_authority)

    def test_active_reservations_cannot_exceed_existing_portfolio_risk_budget(self) -> None:
        desk = DemoDeskCapitalState(
            "desk-risk",
            "generation-1",
            fp("risk-session"),
            SettledCapitalState(5_000, 0, 0, 2_500, 7_500, 7_500),
        )
        policy = CapitalOpportunityPolicy(maximum_risk_fraction_per_trade=0.005, maximum_total_risk_fraction=0.02, maximum_concurrent_opportunities=6, base_opportunity_slots=1, realized_gain_per_extra_slot=500)
        reservations = tuple(self.reservation(desk, index, risk=0.005, minimum=100) for index in range(1, 6))
        with self.assertRaisesRegex(ValueError, "portfolio risk constitution"):
            allocate_demo_capital(
                desk,
                (self.opportunity(9),),
                (self.evidence(9),),
                active_firm_reservations=reservations,
                at=NOW,
                policy=policy,
            )


if __name__ == "__main__":
    unittest.main()
