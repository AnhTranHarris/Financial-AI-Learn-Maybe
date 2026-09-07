from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import unittest

from dusty.source_intake import (
    EvidenceClass,
    ProposalCompleteness,
    SourceAccess,
    SourceSnapshot,
    StrategyProposal,
)
from dusty.strategy_estate_builder import EstatePopulationResult, EstatePopulationRow, EstatePopulationStatus
from dusty.strategy_reconstruction_campaign import (
    AssignmentBasis,
    CONTEXT_TIMEFRAME_LADDER,
    DECISION_TIMEFRAME_LADDER,
    MAX_PROFILE_SYMBOLS,
    PlanStatus,
    RECONSTRUCTION_BATCH_SIZE,
    TimeframeMode,
    execute_reconstruction_campaign,
    plan_reconstruction_campaign,
)


NOW = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64
SYMBOLS = ("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "NASUSD")


def proposal(
    index: int,
    *,
    proposal_id: str | None = None,
    symbols: tuple[str, ...] = ("EURUSD",),
    timeframes: tuple[str, ...] = ("M15",),
    component: str | None = None,
    source_hash: str | None = None,
) -> StrategyProposal:
    return StrategyProposal(
        proposal_id or f"website:idea-{index}",
        SourceSnapshot(
            "test-source",
            f"https://example.test/strategy/{index}",
            NOW,
            source_hash or f"{index + 1:064x}"[-64:],
            SourceAccess.MANUAL_REVIEW,
            False,
        ),
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.PARTIAL,
        f"Strategy idea {index}",
        symbols=symbols,
        timeframes=timeframes,
        components=(component or f"component-{index}",),
        declared_rules=(("concept", component or f"concept-{index}"),),
        unresolved=("entry_logic",),
        tags=("research_only",),
    )


class RecordingBuilder:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[StrategyProposal, ...], dict[str, object]]] = []

    def populate(self, proposals, **kwargs):
        rows = tuple(proposals)
        self.calls.append((rows, kwargs))
        self.assert_single(rows)
        result = EstatePopulationRow(
            rows[0].proposal_id,
            EstatePopulationStatus.ADDED,
            reconstruction_fingerprint=H("a"),
        )
        return EstatePopulationResult((result,), None)

    @staticmethod
    def assert_single(rows) -> None:
        if len(rows) != 1:
            raise AssertionError("campaign issued concurrent/multi-proposal builder call")


class M1966StrategyReconstructionCampaignTests(unittest.TestCase):
    def test_six_is_batch_window_not_parallel_model_calls(self) -> None:
        rows = tuple(proposal(i) for i in range(14))
        campaign = plan_reconstruction_campaign(rows, allowed_symbols=SYMBOLS)
        self.assertEqual(RECONSTRUCTION_BATCH_SIZE, 6)
        self.assertEqual(tuple(len(batch) for batch in campaign.batches), (6, 6, 2))

        builder = RecordingBuilder()
        result = execute_reconstruction_campaign(
            campaign,
            builder=builder,  # type: ignore[arg-type]
            model_tag="qwen3:1.7b",
            model_digest=H("d"),
            allowed_features=("rsi", "atr"),
            allowed_sessions=("ASIA", "LONDON", "NEW_YORK"),
            estate_path=Path("unused-estate.json"),
        )
        self.assertEqual(len(builder.calls), 14)
        self.assertTrue(all(len(call[0]) == 1 for call in builder.calls))
        self.assertEqual(result.batches_completed, 3)
        self.assertEqual(result.model_calls_scheduled, 14)
        self.assertEqual(result.added_to_estate, 14)

    def test_family_duplicates_are_removed_before_batches(self) -> None:
        original = proposal(1, component="same-family")
        duplicate = StrategyProposal(
            "other-source:same-idea",
            SourceSnapshot(
                "other-source",
                "https://other.example/strategy",
                NOW,
                H("f"),
                SourceAccess.MANUAL_REVIEW,
                False,
            ),
            EvidenceClass.STRATEGY_HYPOTHESIS,
            ProposalCompleteness.CONCEPT_ONLY,
            "Different marketing title",
            symbols=original.symbols,
            timeframes=original.timeframes,
            components=original.components,
            declared_rules=original.declared_rules,
            unresolved=("entry_logic", "exit_logic"),
            tags=original.tags,
        )
        unique = tuple(proposal(i + 10) for i in range(7))
        campaign = plan_reconstruction_campaign((original, duplicate, *unique), allowed_symbols=SYMBOLS)
        self.assertEqual(campaign.proposals_seen, 9)
        self.assertEqual(campaign.proposals_after_dedupe, 8)
        self.assertEqual(campaign.duplicates_removed, 1)
        self.assertEqual(sum(len(batch) for batch in campaign.batches), 8)

    def test_declared_single_timeframe_remains_single(self) -> None:
        campaign = plan_reconstruction_campaign((proposal(1, timeframes=("H4",)),), allowed_symbols=SYMBOLS)
        plan = campaign.batches[0][0]
        assert plan.profile is not None
        self.assertEqual(plan.profile.primary, "H4")
        self.assertEqual(plan.profile.context, ())
        self.assertEqual(plan.profile.mode, TimeframeMode.SINGLE)
        self.assertEqual(plan.profile.basis, AssignmentBasis.SOURCE_DECLARED)

    def test_declared_multitimeframe_preserves_bounded_human_context(self) -> None:
        campaign = plan_reconstruction_campaign(
            (proposal(1, timeframes=("H1", "D1", "M15", "M5")),),
            allowed_symbols=SYMBOLS,
        )
        plan = campaign.batches[0][0]
        assert plan.profile is not None
        self.assertEqual(plan.profile.primary, "H1")
        self.assertEqual(plan.profile.context, ("D1", "M15"))
        self.assertEqual(plan.profile.mode, TimeframeMode.MULTI)
        self.assertLessEqual(len(plan.profile.context), 2)

    def test_m1_can_be_context_but_not_current_primary_decision_timeframe(self) -> None:
        campaign = plan_reconstruction_campaign(
            (proposal(1, timeframes=("M5", "M1")),),
            allowed_symbols=SYMBOLS,
        )
        plan = campaign.batches[0][0]
        assert plan.profile is not None
        self.assertEqual(plan.profile.primary, "M5")
        self.assertEqual(plan.profile.context, ("M1",))
        self.assertNotIn("M1", DECISION_TIMEFRAME_LADDER)
        self.assertIn("M1", CONTEXT_TIMEFRAME_LADDER)

    def test_source_with_only_m1_is_deferred_not_silently_rewritten(self) -> None:
        campaign = plan_reconstruction_campaign((proposal(1, timeframes=("M1",)),), allowed_symbols=SYMBOLS)
        self.assertEqual(campaign.batches, ())
        self.assertEqual(campaign.plans[0].status, PlanStatus.DEFERRED)
        self.assertIn("m5_plus", campaign.plans[0].reason)

    def test_unspecified_timeframe_assignment_is_deterministic_and_not_all_m15(self) -> None:
        rows = tuple(proposal(i, timeframes=()) for i in range(30))
        first = plan_reconstruction_campaign(rows, allowed_symbols=SYMBOLS)
        second = plan_reconstruction_campaign(tuple(reversed(rows)), allowed_symbols=SYMBOLS)
        first_map = {plan.proposal.family_fingerprint: plan.profile for plan in first.plans}
        second_map = {plan.proposal.family_fingerprint: plan.profile for plan in second.plans}
        self.assertEqual(first_map, second_map)
        primaries = {plan.profile.primary for plan in first.plans if plan.profile is not None}
        self.assertGreater(len(primaries), 1)
        self.assertTrue(primaries.issubset(set(DECISION_TIMEFRAME_LADDER)))
        self.assertTrue(any(plan.profile and plan.profile.mode is TimeframeMode.MULTI for plan in first.plans))
        self.assertTrue(any(plan.profile and plan.profile.mode is TimeframeMode.SINGLE for plan in first.plans))

    def test_cross_symbol_options_are_bounded_without_cartesian_expansion(self) -> None:
        campaign = plan_reconstruction_campaign(
            (proposal(1, symbols=("EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "NASUSD")),),
            allowed_symbols=SYMBOLS,
        )
        plan = campaign.batches[0][0]
        self.assertEqual(len(plan.target_symbols), MAX_PROFILE_SYMBOLS)
        self.assertEqual(plan.target_symbols, ("EURUSD", "GBPUSD", "USDJPY"))
        self.assertEqual(plan.deferred_symbols, ("XAUUSD", "NASUSD"))
        self.assertEqual(len(campaign.batches), 1)
        self.assertEqual(len(campaign.batches[0]), 1)

    def test_executor_passes_only_primary_timeframe_until_pit_context_binding_exists(self) -> None:
        campaign = plan_reconstruction_campaign(
            (proposal(1, timeframes=("H1", "D1", "M15")),),
            allowed_symbols=SYMBOLS,
        )
        builder = RecordingBuilder()
        execute_reconstruction_campaign(
            campaign,
            builder=builder,  # type: ignore[arg-type]
            model_tag="qwen3:1.7b",
            model_digest=H("d"),
            allowed_features=("rsi",),
            allowed_sessions=("LONDON",),
            estate_path=Path("unused-estate.json"),
        )
        kwargs = builder.calls[0][1]
        self.assertEqual(kwargs["allowed_timeframes"], ("H1",))
        self.assertNotIn("D1", kwargs["allowed_timeframes"])

    def test_campaign_has_no_trading_authority(self) -> None:
        campaign = plan_reconstruction_campaign((proposal(1),), allowed_symbols=SYMBOLS)
        result = execute_reconstruction_campaign(
            campaign,
            builder=RecordingBuilder(),  # type: ignore[arg-type]
            model_tag="qwen3:1.7b",
            model_digest=H("d"),
            allowed_features=("rsi",),
            allowed_sessions=(),
            estate_path=Path("unused-estate.json"),
        )
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
