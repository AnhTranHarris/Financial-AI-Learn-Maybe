from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from dusty.features import FeatureBar
from dusty.forecast_research import (
    DisagreementState,
    ProviderOutcomeCase,
    build_pit_context,
    classify_disagreement,
    future_mutation_invariant,
    score_provider_cases,
)
from dusty.provider_forecast_adapter import ForecastEvidence, PROTOCOL as FORECAST_PROTOCOL
from dusty.quant_reviewer import (
    PROMPT_VERSION,
    PROTOCOL as QUANT_PROTOCOL,
    QuantReviewRequest,
    QuantReviewState,
    build_quant_prompt_payload,
    parse_quant_review,
)
from dusty.research_brain import (
    ChallengerPlan,
    Mutation,
    MutationAxis,
    ResearchMandate,
    ResearchMetrics,
    ResearchSchool,
    evaluate_school,
    human_durable_priors,
)
from dusty.research_runtime import (
    BlackboardItem,
    BlackboardKind,
    ResearchBlackboard,
    ResearchStage,
    SQLiteResearchCycleStore,
    graveyard_research_allowed,
    heartbeat,
)
from dusty.research_scheduler import (
    FidelityTier,
    JobKind,
    ResearchJob,
    next_fidelity,
    schedule_jobs,
)
from dusty.resource import JobPriority, ResourceBudget, ResourceSnapshot
from dusty.source_intake import (
    EvidenceClass,
    ProposalCompleteness,
    SourceAccess,
    StrategyProposal,
    deduplicate_proposals,
    default_source_policies,
    make_snapshot,
    proposal_priority_key,
    proposals_from_vibe,
)
from dusty.vibe_research_contract import (
    EXPECTED_VIBE_VERSION,
    PROTOCOL as VIBE_PROTOCOL,
    PROVIDER_ID as VIBE_PROVIDER_ID,
    VibeResearchEvidence,
)


UTC = timezone.utc
T0 = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)


def h(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def bar(i: int, close: float | None = None) -> FeatureBar:
    price = close if close is not None else 1.10 + i * 0.001
    return FeatureBar(
        at=T0 + timedelta(minutes=15 * i),
        open=price,
        high=price + 0.002,
        low=price - 0.002,
        close=price,
        spread_points=10,
        tick_volume=100 + i,
    )


def forecast(
    provider: str,
    *,
    p50: float,
    p10: float = 1.09,
    p90: float = 1.12,
    context_sha: str = h("context"),
    as_of: datetime = T0,
) -> ForecastEvidence:
    return ForecastEvidence(
        protocol=FORECAST_PROTOCOL,
        provider_id=provider,
        model_id=f"{provider}-model",
        model_revision=h(provider)[:40],
        provider_version="test",
        license_id="test-license",
        symbol="EURUSD",
        timeframe="M15",
        as_of=as_of,
        origin_at=as_of,
        horizon_steps=4,
        origin_value=1.10,
        p10=p10,
        p50=p50,
        p90=p90,
        context_sha256=context_sha,
        request_sha256=h(provider + "request"),
        response_sha256=h(provider + "response"),
    )


def source_snapshot(policy_id: str = "myfxbook"):
    policy = next(p for p in default_source_policies() if p.source_id == policy_id)
    return make_snapshot(
        policy,
        url="https://www.myfxbook.com/strategies/example/1",
        captured_at=T0,
        content="example",
        automated=False,
    )


class SourceIntakeTests(unittest.TestCase):
    def test_source_firewall_blocks_myfxbook_automation(self) -> None:
        policy = next(p for p in default_source_policies() if p.source_id == "myfxbook")
        with self.assertRaises(PermissionError):
            make_snapshot(
                policy,
                url="https://www.myfxbook.com/strategies/example/1",
                captured_at=T0,
                content="claimed strategy",
                automated=True,
            )

    def test_forexfactory_calendar_structured_automation_allowed(self) -> None:
        policy = next(p for p in default_source_policies() if p.source_id == "forexfactory-calendar")
        snapshot = make_snapshot(
            policy,
            url="https://www.forexfactory.com/calendar",
            captured_at=T0,
            content='{"event":"CPI"}',
            automated=True,
        )
        self.assertTrue(snapshot.automated)
        self.assertEqual(snapshot.access, SourceAccess.STRUCTURED_PUBLIC)

    def test_claimed_return_does_not_define_family_or_priority(self) -> None:
        snapshot = source_snapshot()
        common = dict(
            snapshot=snapshot,
            evidence_class=EvidenceClass.STRATEGY_HYPOTHESIS,
            completeness=ProposalCompleteness.PARTIAL,
            title="same genetics",
            symbols=("EURUSD",),
            timeframes=("M15",),
            components=("EMA", "RSI"),
            unresolved=("exit_logic",),
        )
        high = StrategyProposal("high", claimed_performance=(("return", "1000000%"),), **common)
        low = StrategyProposal("low", claimed_performance=(("return", "2%"),), **common)
        self.assertEqual(high.family_fingerprint, low.family_fingerprint)
        self.assertEqual(proposal_priority_key(high)[:-1], proposal_priority_key(low)[:-1])
        self.assertEqual(len(deduplicate_proposals((high, low))), 1)

    def test_vibe_alpha_zoo_becomes_concept_only(self) -> None:
        payload = {
            "status": "ok",
            "result": {
                "items": [{
                    "id": "alpha101_001",
                    "nickname": "Alpha 1",
                    "theme": ["reversal"],
                    "formula_latex": "rank(close)",
                    "columns_required": ["close"],
                    "frequency": ["1D"],
                }]
            },
        }
        text = json.dumps(payload)
        evidence = VibeResearchEvidence(
            protocol=VIBE_PROTOCOL,
            provider_id=VIBE_PROVIDER_ID,
            tool="alpha_zoo",
            vibe_version=EXPECTED_VIBE_VERSION,
            surface_sha256=h("surface"),
            request_sha256=h("request"),
            response_sha256=h(text),
            result_text=text,
        )
        proposal = proposals_from_vibe(evidence)[0]
        self.assertEqual(proposal.completeness, ProposalCompleteness.CONCEPT_ONLY)
        self.assertEqual(proposal.unresolved, ("entry_logic", "exit_logic", "risk_logic"))

    def test_source_policies_are_conservative(self) -> None:
        policies = {p.source_id: p for p in default_source_policies()}
        self.assertFalse(policies["tradingview"].automated_acquisition_allowed)
        self.assertFalse(policies["myfxbook"].automated_acquisition_allowed)
        self.assertTrue(policies["vibe-trading"].automated_acquisition_allowed)


class ForecastResearchTests(unittest.TestCase):
    def test_future_mutation_cannot_change_point_in_time_context(self) -> None:
        original = [bar(i) for i in range(10)]
        mutated = original[:6] + [bar(i, 2.0 + i) for i in range(6, 10)]
        as_of = original[5].at
        self.assertTrue(
            future_mutation_invariant(
                original,
                mutated,
                symbol="EURUSD",
                timeframe="M15",
                as_of=as_of,
            )
        )

    def test_history_mutation_changes_context(self) -> None:
        original = [bar(i) for i in range(6)]
        changed = original.copy()
        changed[4] = bar(4, 1.30)
        left = build_pit_context(original, symbol="EURUSD", timeframe="M15", as_of=original[-1].at)
        right = build_pit_context(changed, symbol="EURUSD", timeframe="M15", as_of=original[-1].at)
        self.assertNotEqual(left.context_hash, right.context_hash)

    def test_disagreement_is_classified_without_authority(self) -> None:
        context = h("same-context")
        rows = (
            forecast("chronos2", p50=1.11, context_sha=context),
            forecast("kronos-small", p50=1.09, context_sha=context),
            forecast("timesfm-2.5", p50=1.115, context_sha=context),
        )
        result = classify_disagreement(rows)
        self.assertEqual(result.state, DisagreementState.TWO_UP_ONE_DOWN)
        self.assertFalse(result.decision_authority)

    def test_provider_scorecard_keeps_regime_and_session(self) -> None:
        ev = forecast("chronos2", p50=1.11)
        cases = (
            ProviderOutcomeCase(ev, 1.105, T0 + timedelta(hours=1), "trend", "london"),
            ProviderOutcomeCase(ev, 1.115, T0 + timedelta(hours=1), "trend", "london"),
        )
        score = score_provider_cases(cases)[0]
        self.assertEqual(score.regime, "trend")
        self.assertEqual(score.session, "london")
        self.assertEqual(score.count, 2)


class ResearchBrainTests(unittest.TestCase):
    def metrics(self, **overrides) -> ResearchMetrics:
        values = dict(
            sample_count=100,
            oos_expectancy=0.001,
            cost_stress_expectancy=0.0007,
            max_drawdown_fraction=0.10,
            walk_forward_efficiency=0.70,
            parameter_stable=True,
            constitution_compliant=True,
            forward_sample_count=30,
            forward_expectancy=0.0005,
            entries_per_hour=0.5,
            resource_seconds=100.0,
        )
        values.update(overrides)
        return ResearchMetrics(**values)

    def test_exactly_twenty_human_priors(self) -> None:
        priors = human_durable_priors()
        self.assertEqual(len(priors), 20)
        self.assertEqual(len({p.prior_id for p in priors}), 20)

    def test_a1_a2_a3_sequential_proof(self) -> None:
        metrics = self.metrics()
        self.assertTrue(evaluate_school(ResearchSchool.A1_EDGE, metrics).passed)
        self.assertTrue(evaluate_school(ResearchSchool.A2_PROFITABILITY, metrics).passed)
        self.assertTrue(evaluate_school(ResearchSchool.A3_VELOCITY, metrics).passed)

    def test_a3_cannot_bypass_a1_or_a2(self) -> None:
        decision = evaluate_school(
            ResearchSchool.A3_VELOCITY,
            self.metrics(oos_expectancy=-0.001, cost_stress_expectancy=-0.001),
        )
        self.assertFalse(decision.passed)
        self.assertIn("a2_not_proven", decision.reasons)

    def test_entry_rate_constitution_blocks_velocity(self) -> None:
        decision = evaluate_school(
            ResearchSchool.A3_VELOCITY,
            self.metrics(entries_per_hour=4.0),
            ResearchMandate(max_entries_per_hour=3.0),
        )
        self.assertFalse(decision.passed)
        self.assertIn("entry_rate_constitution_failed", decision.reasons)

    def test_challenger_is_bounded_and_champion_immutable(self) -> None:
        plan = ChallengerPlan(
            parent_hash=h("champion"),
            hypothesis="Test later entry only",
            mutations=(Mutation(MutationAxis.ENTRY, "delay one completed bar"),),
        )
        self.assertFalse(plan.champion_modified)
        with self.assertRaises(ValueError):
            ChallengerPlan(
                parent_hash=h("champion"),
                hypothesis="too many changes",
                mutations=(
                    Mutation(MutationAxis.ENTRY, "x"),
                    Mutation(MutationAxis.EXIT, "y"),
                    Mutation(MutationAxis.REGIME, "z"),
                ),
            )


class SchedulerTests(unittest.TestCase):
    def snapshot(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            total_ram_bytes=16 * 1024**3,
            available_ram_bytes=8 * 1024**3,
            disk_free_bytes=100 * 1024**3,
            cpu_percent=20,
        )

    def budget(self) -> ResourceBudget:
        return ResourceBudget(min_free_disk_bytes=2 * 1024**3)

    def test_model_inference_is_sequential(self) -> None:
        jobs = (
            ResearchJob("c", JobKind.FORECAST, JobPriority.FORECAST, FidelityTier.STANDARD, 1, 10, 10, True),
            ResearchJob("k", JobKind.FORECAST, JobPriority.FORECAST, FidelityTier.STANDARD, 1, 10, 9, True),
            ResearchJob("t", JobKind.FORECAST, JobPriority.FORECAST, FidelityTier.STANDARD, 1, 10, 8, True),
        )
        result = schedule_jobs(jobs, self.snapshot(), self.budget())
        self.assertEqual(sum(job.model_inference for job in result.admitted), 1)

    def test_cheap_screen_precedes_native(self) -> None:
        jobs = (
            ResearchJob("native", JobKind.NATIVE_MT5, JobPriority.BACKTEST, FidelityTier.NATIVE, 1, 10, 100),
            ResearchJob("cheap", JobKind.STRATEGY_SCREEN, JobPriority.RESEARCH, FidelityTier.CHEAP, 1, 10, 1),
        )
        result = schedule_jobs(jobs, self.snapshot(), self.budget())
        self.assertEqual(result.admitted[0].job_id, "cheap")

    def test_fidelity_only_escalates_after_pass(self) -> None:
        self.assertIsNone(next_fidelity(FidelityTier.CHEAP, passed=False))
        self.assertEqual(next_fidelity(FidelityTier.CHEAP, passed=True), FidelityTier.STANDARD)
        self.assertEqual(next_fidelity(FidelityTier.STANDARD, passed=True), FidelityTier.NATIVE)


class QuantReviewerTests(unittest.TestCase):
    def request(self) -> QuantReviewRequest:
        return QuantReviewRequest(
            request_id="case-1",
            model_tag="qwen3:1.7b",
            model_digest=h("model"),
            forecast_fingerprints=(h("forecast"),),
            strategy_fingerprints=(h("strategy"),),
            evidence_fingerprints=(h("evidence"),),
            scorecard_text="Chronos direction=55%; no authority",
            question="How should this disagreement be researched?",
        )

    def test_prompt_contains_no_operational_authority(self) -> None:
        payload = build_quant_prompt_payload(self.request())
        text = json.dumps(payload)
        self.assertNotIn("order_send", text)
        self.assertNotIn("broker_password", text)
        self.assertIn("no_trade_authority", text)

    def test_strict_research_required_parser(self) -> None:
        request = self.request()
        response = json.dumps(
            {
                "state": QuantReviewState.RESEARCH_REQUIRED.value,
                "rationale_codes": ["forecast_disagreement"],
                "cited_fingerprints": [h("forecast")],
                "proposed_research": ["Run regime-stratified historical comparison."],
            }
        )
        result = parse_quant_review(request, response)
        self.assertEqual(result.protocol, QUANT_PROTOCOL)
        self.assertEqual(result.prompt_version, PROMPT_VERSION)
        self.assertFalse(result.broker_write_authority)

    def test_parser_rejects_extra_authority_field(self) -> None:
        request = self.request()
        response = json.dumps(
            {
                "state": "resolved",
                "rationale_codes": ["x"],
                "cited_fingerprints": [],
                "proposed_research": [],
                "place_order": True,
            }
        )
        with self.assertRaises(ValueError):
            parse_quant_review(request, response)


class ResearchRuntimeTests(unittest.TestCase):
    def board(self) -> ResearchBlackboard:
        item = BlackboardItem(BlackboardKind.SOURCE, "source-1", h("payload"))
        return ResearchBlackboard("cycle-1", T0, (item,))

    def test_blackboard_is_content_addressed_and_has_no_live_authority(self) -> None:
        board = self.board()
        self.assertEqual(board.fingerprint, self.board().fingerprint)
        self.assertFalse(board.live_write_authorized)
        with self.assertRaises(ValueError):
            ResearchBlackboard("bad", T0, (), live_write_authorized=True)

    def test_checkpoint_store_is_append_only_and_restartable(self) -> None:
        store = SQLiteResearchCycleStore()
        try:
            board = self.board()
            first = heartbeat(store, board, now=T0)
            second = heartbeat(store, board, now=T0 + timedelta(seconds=1))
            history = tuple(store.iter_history("cycle-1"))
            self.assertEqual(first.checkpoint.stage, ResearchStage.ACQUIRE)
            self.assertTrue(second.resumed)
            self.assertEqual(second.checkpoint.stage, ResearchStage.FORECAST)
            self.assertEqual(len(history), 2)
            self.assertTrue(store.integrity_ok())
        finally:
            store.close()

    def test_graveyard_requires_changed_context_and_reason(self) -> None:
        self.assertTrue(
            graveyard_research_allowed(
                previously_rejected=False,
                context_changed=False,
                explicit_reason="",
            )
        )
        self.assertFalse(
            graveyard_research_allowed(
                previously_rejected=True,
                context_changed=True,
                explicit_reason="",
            )
        )
        self.assertTrue(
            graveyard_research_allowed(
                previously_rejected=True,
                context_changed=True,
                explicit_reason="new regime",
            )
        )


if __name__ == "__main__":
    unittest.main()
