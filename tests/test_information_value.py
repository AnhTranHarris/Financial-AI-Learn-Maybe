from __future__ import annotations

import unittest

from dusty.adaptive import CurriculumCycleMetrics
from dusty.event_certification import EventIntelligenceGateInput, qualify_event_intelligence
from dusty.information_value import (
    SourceValueObservation,
    SourceValueState,
    decide_news_acquisition,
    evaluate_source_value,
)
from dusty.markets import AssetClass, InstrumentType, MarketIdentity, SymbolResearchProfile
from dusty.news import NewsAccess, NewsSource, SourceRole


class InformationValueTests(unittest.TestCase):
    def setUp(self) -> None:
        market = MarketIdentity.of(raw_symbol="GBPUSD", economic_underlier="GBPUSD", asset_class=AssetClass.FX, instrument_type=InstrumentType.CFD)
        self.profile = SymbolResearchProfile.of(market, currencies=("GBP", "USD"))
        self.source = NewsSource("boe", "Bank of England", NewsAccess.FREE_PRIMARY, SourceRole.PRIMARY_FACT, "boe", currencies=("GBP",))

    def test_useful_source_can_earn_another_bounded_batch(self) -> None:
        assessment = evaluate_source_value(
            (
                SourceValueObservation("boe", "GBPUSD", "rates", 0.50, 0.60),
                SourceValueObservation("boe", "GBPUSD", "rates", 0.40, 0.55),
                SourceValueObservation("boe", "GBPUSD", "rates", 0.45, 0.50),
            )
        )
        self.assertEqual(assessment.state, SourceValueState.USEFUL)
        decision = decide_news_acquisition(
            self.source,
            self.profile,
            CurriculumCycleMetrics(acquired=20, classified=20, hypotheses_tested=20, useful_lessons=4),
            knowledge_gap="BoE reaction under London liquidity",
            requested_count=100,
            value=assessment,
        )
        self.assertEqual(decision.approved_count, 20)

    def test_free_source_with_no_incremental_value_is_paused(self) -> None:
        assessment = evaluate_source_value(
            (
                SourceValueObservation("boe", "GBPUSD", "rates", 0.60, 0.55),
                SourceValueObservation("boe", "GBPUSD", "rates", 0.50, 0.50),
                SourceValueObservation("boe", "GBPUSD", "rates", 0.70, 0.65),
            )
        )
        self.assertEqual(assessment.state, SourceValueState.PAUSE)
        decision = decide_news_acquisition(
            self.source,
            self.profile,
            CurriculumCycleMetrics(acquired=20, classified=20, hypotheses_tested=20, useful_lessons=1),
            knowledge_gap="more rates context",
            requested_count=20,
            value=assessment,
        )
        self.assertEqual(decision.approved_count, 0)
        self.assertEqual(decision.reason, "source_incremental_value_failed")

    def test_paid_source_cannot_enter_automatic_pipeline(self) -> None:
        paid = NewsSource("paid", "Paid", NewsAccess.PAID, SourceRole.SECONDARY_CONTEXT, "paid", currencies=("GBP",))
        decision = decide_news_acquisition(
            paid,
            self.profile,
            CurriculumCycleMetrics(acquired=0, classified=0, hypotheses_tested=0, useful_lessons=0),
            knowledge_gap="test",
            requested_count=20,
        )
        self.assertEqual(decision.reason, "source_not_free_for_automatic_acquisition")

    def test_m55_certification_never_authorizes_broker(self) -> None:
        inputs = EventIntelligenceGateInput(
            m45_ready=True,
            market_identity_certified=True,
            free_source_policy_certified=True,
            scheduled_event_pit_clean=True,
            unscheduled_clustering_certified=True,
            source_independence_certified=True,
            scenario_falsification_ready=True,
            reaction_research_certified=True,
            strategy_event_research_certified=True,
            event_reasoning_bridge_certified=True,
            source_value_gate_certified=True,
        )
        result = qualify_event_intelligence(inputs)
        self.assertTrue(result.ready_for_demo_execution_development)
        self.assertFalse(result.broker_write_authorized)


if __name__ == "__main__":
    unittest.main()
