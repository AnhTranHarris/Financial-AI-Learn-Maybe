from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import unittest

from dusty.features import FeatureBar
from dusty.forecast_research import DisagreementState, ForecastDisagreement
from dusty.market_clock import BrokerMarketSchedule, SessionKind, WeeklySession
from dusty.mt5worker import MT5Bar, MT5BarRequest
from dusty.ollama_quant_reviewer import OllamaQuantReviewer, QuantReviewerAvailability
from dusty.provider_forecast_adapter import PROTOCOL as FORECAST_PROTOCOL, ForecastEvidence
from dusty.quant_reviewer import QuantReviewRequest, QuantReviewState
from dusty.research_brain import ResearchMetrics
from dusty.research_organism import (
    BrokerSymbolBinding,
    DeterministicQuantScorecard,
    DisagreementOutcomeCase,
    MT5ResearchDataService,
    ProfitVelocityObservation,
    ResearchOrganism,
    SQLiteProviderSkillStore,
    SQLiteResearchOrganismStore,
    StageWork,
    build_pit_campaign,
    build_quant_scorecard,
    build_session_forecast_horizon,
    realize_campaign_forecast,
    run_research_funnel,
    score_disagreement_cases,
    summarize_profit_velocity,
)
from dusty.research_runtime import BlackboardItem, BlackboardKind, ResearchBlackboard, ResearchStage
from dusty.source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal
from dusty.strategy_lab import (
    ConstraintMode,
    FailureDiagnosis,
    FailureMechanism,
    StrategyConstraint,
    StrategyOrigin,
    UserStrategyIntent,
    compile_user_strategy_intent,
    compose_in_house_strategy,
    external_strategy_genomes,
    redesign_from_failure,
    resolve_strategy_experiments,
    vibe_strategy_factory,
)
from dusty.vibe_research_contract import EXPECTED_VIBE_VERSION, PROTOCOL as VIBE_PROTOCOL, VibeResearchEvidence


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _raw_bars(count: int = 90) -> tuple[MT5Bar, ...]:
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        price = 1.10 + index * 0.0001
        rows.append(
            MT5Bar(
                at=start + timedelta(minutes=15 * index),
                open=price,
                high=price + 0.0003,
                low=price - 0.0003,
                close=price + 0.0001,
                tick_volume=100 + index,
                spread=10,
                real_volume=0,
            )
        )
    return tuple(rows)


def _feature_bars(count: int = 90) -> tuple[FeatureBar, ...]:
    raw = _raw_bars(count + 1)
    return tuple(
        FeatureBar.from_mt5(
            current,
            available_at=following.at,
            execution_price=following.open,
            decision_spread_proxy_points=float(following.spread),
        )
        for current, following in zip(raw, raw[1:])
    )


def _forecast(context, provider: str = "chronos2", *, p50_scale: float = 1.001) -> ForecastEvidence:
    origin = context.rows[-1].close
    return ForecastEvidence(
        protocol=FORECAST_PROTOCOL,
        provider_id=provider,
        model_id=f"model:{provider}",
        model_revision="a" * 40,
        provider_version="test",
        license_id="test",
        symbol=context.symbol,
        timeframe=context.timeframe,
        as_of=context.as_of,
        origin_at=context.as_of,
        horizon_steps=4,
        origin_value=origin,
        p10=origin * 0.997,
        p50=origin * p50_scale,
        p90=origin * 1.003,
        context_sha256=context.context_hash,
        request_sha256=_digest(f"{provider}:request"),
        response_sha256=_digest(f"{provider}:response"),
    )


class _FakeWorker:
    broker_write_authorized = False

    def __init__(self, bars):
        self.bars = bars
        self.requests = []

    def stream_bars(self, request):
        self.requests.append(request)
        yield from self.bars


class M136M154ResearchOrganismTests(unittest.TestCase):
    def test_m136_read_only_mt5_service_uses_explicit_symbol_binding(self):
        worker = _FakeWorker(_raw_bars(8))
        service = MT5ResearchDataService(worker)
        request = MT5BarRequest(
            "C:/MT5/terminal64.exe", "NAS", "M15",
            datetime(2026, 1, 5, tzinfo=timezone.utc), datetime(2026, 1, 6, tzinfo=timezone.utc),
        )
        batch = service.load(request, binding=BrokerSymbolBinding("NAS", "USTEC.cash"))
        self.assertFalse(service.broker_write_authorized)
        self.assertFalse(batch.broker_write_authority)
        self.assertEqual(worker.requests[0].symbol, "USTEC.cash")
        self.assertEqual(len(batch.completed_bars), len(batch.raw_bars) - 1)
        self.assertEqual(len(batch.terminal_path_sha256), 64)

    def test_m137_session_horizon_skips_weekend_without_inventing_elapsed_bars(self):
        captured = datetime(2026, 1, 2, 12, tzinfo=timezone.utc)
        sessions = tuple(WeeklySession(SessionKind.TRADE, weekday, 0, 0, 0) for weekday in range(5))
        schedule = BrokerMarketSchedule("broker", "server", "EURUSD", captured, 0, sessions)
        as_of = datetime(2026, 1, 2, 23, 45, tzinfo=timezone.utc)
        horizon = build_session_forecast_horizon(schedule, as_of=as_of, timeframe="M15", horizon_steps=2)
        self.assertEqual(horizon.future_times[0], datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(horizon.future_times[1], datetime(2026, 1, 5, 0, 15, tzinfo=timezone.utc))
        self.assertTrue(horizon.skill_certification_eligible)

    def test_m137_future_schedule_capture_is_rejected(self):
        sessions = (WeeklySession(SessionKind.TRADE, 0, 0, 0, 0),)
        schedule = BrokerMarketSchedule("broker", "server", "EURUSD", datetime(2026, 1, 6, tzinfo=timezone.utc), 0, sessions)
        with self.assertRaisesRegex(ValueError, "future broker schedule"):
            build_session_forecast_horizon(
                schedule, as_of=datetime(2026, 1, 5, tzinfo=timezone.utc), timeframe="M15", horizon_steps=1
            )

    def test_m138_campaign_uses_actual_completed_observation_times(self):
        bars = _feature_bars(90)
        points = build_pit_campaign(
            bars, symbol="EURUSD", timeframe="M15", horizon_steps=4, min_context_observations=64
        )
        self.assertGreater(len(points), 10)
        self.assertEqual(points[0].context.as_of, bars[63].at)
        self.assertEqual(points[0].target_at, bars[67].at)

    def test_m139_realization_binds_exact_forecast_context_and_target(self):
        bars = _feature_bars(90)
        point = build_pit_campaign(
            bars, symbol="EURUSD", timeframe="M15", horizon_steps=4, min_context_observations=64
        )[0]
        evidence = _forecast(point.context)
        outcome = realize_campaign_forecast(evidence, point, bars, regime="trend", session="london")
        self.assertEqual(outcome.realized_at, point.target_at)
        self.assertEqual(outcome.regime, "trend")
        bad = ForecastEvidence(
            protocol=evidence.protocol, provider_id=evidence.provider_id, model_id=evidence.model_id,
            model_revision=evidence.model_revision, provider_version=evidence.provider_version,
            license_id=evidence.license_id, symbol=evidence.symbol, timeframe=evidence.timeframe,
            as_of=evidence.as_of, origin_at=evidence.origin_at, horizon_steps=evidence.horizon_steps,
            origin_value=evidence.origin_value, p10=evidence.p10, p50=evidence.p50, p90=evidence.p90,
            context_sha256=_digest("wrong-context"), request_sha256=evidence.request_sha256,
            response_sha256=evidence.response_sha256,
        )
        with self.assertRaisesRegex(ValueError, "does not bind"):
            realize_campaign_forecast(bad, point, bars)

    def test_m140_provider_skill_memory_is_append_only_and_persistent(self):
        bars = _feature_bars(90)
        point = build_pit_campaign(
            bars, symbol="EURUSD", timeframe="M15", horizon_steps=4, min_context_observations=64
        )[0]
        case = realize_campaign_forecast(_forecast(point.context), point, bars, regime="trend", session="london")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "skill.sqlite3"
            store = SQLiteProviderSkillStore(path)
            try:
                scored = store.append_cases((case,), captured_at=point.target_at + timedelta(minutes=1))
                self.assertEqual(scored[0].count, 1)
                self.assertEqual(store.history()[0].provider_id, "chronos2")
                self.assertTrue(store.integrity_ok())
            finally:
                store.close()
            reopened = SQLiteProviderSkillStore(path)
            try:
                self.assertEqual(len(reopened.history()), 1)
            finally:
                reopened.close()

    def test_m141_disagreement_is_research_feature_not_vote(self):
        disagreement = ForecastDisagreement(
            DisagreementState.TWO_UP_ONE_DOWN,
            (("chronos2", "up"), ("kronos-small", "down"), ("timesfm-2.5", "up")),
            (_digest("a"), _digest("b"), _digest("c")),
        )
        rows = (
            DisagreementOutcomeCase(disagreement, "EURUSD", "M15", 4, "trend", "london", 0.002),
            DisagreementOutcomeCase(disagreement, "EURUSD", "M15", 4, "trend", "london", -0.001),
        )
        score = score_disagreement_cases(rows)[0]
        self.assertEqual(score.count, 2)
        self.assertEqual(score.up_rate, 0.5)
        self.assertFalse(disagreement.decision_authority)

    def test_m142_scorecard_precedes_llm_and_has_no_decision_authority(self):
        bars = _feature_bars(90)
        point = build_pit_campaign(
            bars, symbol="EURUSD", timeframe="M15", horizon_steps=4, min_context_observations=64
        )[0]
        provider_case = realize_campaign_forecast(_forecast(point.context), point, bars)
        disagreement = ForecastDisagreement(
            DisagreementState.UNANIMOUS_UP,
            (("chronos2", "up"), ("kronos-small", "up"), ("timesfm-2.5", "up")),
            (_digest("a"), _digest("b"), _digest("c")),
        )
        disagreement_case = DisagreementOutcomeCase(disagreement, "EURUSD", "M15", 4, "unclassified", "unclassified", 0.001)
        card = build_quant_scorecard(
            (provider_case,), (disagreement_case,), as_of=point.target_at,
            source_fingerprints=(_digest("dataset"),),
        )
        self.assertIsInstance(card, DeterministicQuantScorecard)
        self.assertFalse(card.decision_authority)
        self.assertIn('"authority":"research_only"', card.render())
        self.assertEqual(len(card.fingerprint), 64)

    def test_m143_vibe_factory_turns_research_surface_into_genomes(self):
        result_text = json.dumps({
            "status": "ok",
            "result": {"items": [{
                "id": "alpha-1", "nickname": "Momentum Seed", "columns_required": ["close", "volume"],
                "theme": ["momentum"], "frequency": ["15m"], "formula": "close/lag(close)-1",
            }]},
        })
        evidence = VibeResearchEvidence(
            VIBE_PROTOCOL, "vibe-trading", "alpha_zoo", EXPECTED_VIBE_VERSION,
            _digest("surface"), _digest("request"), _digest("response"), result_text,
        )
        genomes = vibe_strategy_factory((evidence,))
        self.assertEqual(len(genomes), 1)
        self.assertEqual(genomes[0].origin, StrategyOrigin.VIBE)
        self.assertIn("entry_logic", genomes[0].unresolved)
        self.assertFalse(genomes[0].promotion_authority)

    def test_m144_external_genome_preserves_provenance_not_claimed_performance_as_fitness(self):
        snapshot = SourceSnapshot(
            "myfxbook", "https://www.myfxbook.com/strategies/example/1",
            datetime(2026, 1, 5, tzinfo=timezone.utc), _digest("page"), SourceAccess.MANUAL_REVIEW, False,
        )
        proposal = StrategyProposal(
            "external:1", snapshot, EvidenceClass.STRATEGY_HYPOTHESIS, ProposalCompleteness.PARTIAL,
            "Example", symbols=("EURUSD",), components=("ema",), unresolved=("exit_logic",),
            claimed_performance=(("return", "10000%"),),
        )
        genome = external_strategy_genomes((proposal,))[0]
        self.assertEqual(genome.origin, StrategyOrigin.EXTERNAL)
        self.assertEqual(genome.source_fingerprint, proposal.fingerprint)
        self.assertNotIn("10000%", repr(genome))

    def _user_intent(self):
        return UserStrategyIntent(
            "USR-NAS-0001", "Asia to London/NY expansion",
            "Find a NAS entry during Asia and seek a high-volume London/NY exit.",
            datetime(2026, 9, 4, tzinfo=timezone.utc), ("NAS",), ("M15",),
            (
                StrategyConstraint("entry.session", "asia", ConstraintMode.LOCKED),
                StrategyConstraint("entry.trigger", "unknown", ConstraintMode.RESEARCHABLE),
                StrategyConstraint("exit.trigger", "high_volume", ConstraintMode.RESEARCHABLE),
                StrategyConstraint("exit.session", "london_or_new_york", ConstraintMode.LOCKED),
            ),
        )

    def test_m145_user_strategy_lab_preserves_original_thesis_and_origin(self):
        intent = self._user_intent()
        genome = compile_user_strategy_intent(intent)
        self.assertEqual(genome.origin, StrategyOrigin.USER)
        self.assertEqual(genome.source_fingerprint, intent.fingerprint)
        self.assertEqual(genome.rule_map()["entry.session"], "asia")
        self.assertIn("risk.martingale", genome.constraint_map())

    def test_m146_intent_compiler_marks_unknowns_researchable_without_guessing(self):
        genome = compile_user_strategy_intent(self._user_intent())
        self.assertIn("entry.trigger", genome.unresolved)
        self.assertNotIn("entry.trigger", genome.rule_map())
        self.assertEqual(genome.constraint_map()["entry.trigger"].mode, ConstraintMode.RESEARCHABLE)

    def test_m147_experiment_resolver_cannot_mutate_locked_or_forbidden(self):
        genome = compile_user_strategy_intent(self._user_intent())
        with self.assertRaises(PermissionError):
            resolve_strategy_experiments(genome, {"entry.session": ("london",)})
        variants = resolve_strategy_experiments(
            genome, {"entry.trigger": ("breakout", "pullback"), "exit.trigger": ("volume_exhaustion",)}
        )
        self.assertTrue(variants)
        self.assertTrue(all(1 <= len(row.changes) <= 2 for row in variants))

    def test_m148_dusty_in_house_composer_creates_descendant_not_parent_rewrite(self):
        parent = compile_user_strategy_intent(self._user_intent())
        child = compose_in_house_strategy(
            parent, genome_id="DD-NAS-0001-A", hypothesis="test Asia breakout timing",
            changes={"entry.trigger": "asia_range_breakout"},
            lesson_fingerprints=(_digest("graveyard-lesson"),),
        )
        self.assertEqual(child.origin, StrategyOrigin.DUSTY)
        self.assertEqual(child.generation, parent.generation + 1)
        self.assertIn(parent.fingerprint, child.parent_fingerprints)
        self.assertNotEqual(child.fingerprint, parent.fingerprint)
        self.assertNotIn("entry.trigger", child.unresolved)

    def _metrics(self, **updates):
        values = dict(
            sample_count=100, oos_expectancy=0.5, cost_stress_expectancy=0.3,
            max_drawdown_fraction=0.10, walk_forward_efficiency=0.75,
            parameter_stable=True, constitution_compliant=True,
            forward_sample_count=25, forward_expectancy=0.2,
            entries_per_hour=1.0, resource_seconds=20.0,
        )
        values.update(updates)
        return ResearchMetrics(**values)

    def test_m149_a1_edge_discovery_is_mandatory_first_gate(self):
        result = run_research_funnel(self._metrics(oos_expectancy=-0.01))
        self.assertFalse(result.a1.passed)
        self.assertFalse(result.a2.passed)
        self.assertFalse(result.a3.passed)
        self.assertIsNone(result.deepest_passed_school)

    def test_m150_a2_profitability_requires_cost_and_walk_forward_survival(self):
        result = run_research_funnel(self._metrics(cost_stress_expectancy=-0.01))
        self.assertTrue(result.a1.passed)
        self.assertFalse(result.a2.passed)
        self.assertIn("cost_stress_failed", result.a2.reasons)

    def test_m151_a3_velocity_keeps_constitution_and_measures_mfe_capture(self):
        result = run_research_funnel(self._metrics(entries_per_hour=4.0))
        self.assertTrue(result.a2.passed)
        self.assertFalse(result.a3.passed)
        self.assertIn("entry_rate_constitution_failed", result.a3.reasons)
        summary = summarize_profit_velocity((
            ProfitVelocityObservation(160.0, 200.0, 40.0),
            ProfitVelocityObservation(80.0, 100.0, 20.0),
        ))
        self.assertAlmostEqual(summary.mean_capture_efficiency, 0.8)
        self.assertAlmostEqual(summary.mean_giveback_fraction, 0.2)

    def test_m152_failure_redesign_changes_only_supported_research_variable(self):
        genome = compile_user_strategy_intent(self._user_intent())
        diagnosis = FailureDiagnosis(
            genome.fingerprint, FailureMechanism.EXIT,
            "exit surrendered too much favorable excursion", (_digest("attribution"),),
            "exit.trigger", ("volume_exhaustion", "atr_trail"),
        )
        plan = redesign_from_failure(genome, diagnosis)
        self.assertFalse(plan.champion_modified)
        self.assertEqual(len(plan.variants), 2)
        self.assertTrue(all(row.changes[0][0] == "exit.trigger" for row in plan.variants))

    def test_m153_qwen_reviewer_verifies_model_digest_and_strict_schema_without_tools(self):
        model_digest = _digest("qwen3:1.7b")
        calls = []

        def transport(method, url, payload, timeout):
            calls.append((method, url, payload, timeout))
            if url.endswith("/api/tags"):
                return {"models": [{"name": "qwen3:1.7b", "model": "qwen3:1.7b", "digest": model_digest}]}
            self.assertNotIn("tools", payload)
            self.assertFalse(payload["stream"])
            self.assertEqual(payload["options"]["temperature"], 0)
            self.assertFalse(payload["format"]["additionalProperties"])
            return {"message": {"content": json.dumps({
                "state": "research_required", "rationale_codes": ["forecast_disagreement"],
                "cited_fingerprints": [_digest("forecast")],
                "proposed_research": ["run matched provider-ablation campaign"],
            })}}

        request = QuantReviewRequest(
            "review-1", "qwen3:1.7b", model_digest, (_digest("forecast"),),
            (_digest("strategy"),), (_digest("scorecard"),), "deterministic scorecard",
            "Resolve or request bounded research.",
        )
        result = OllamaQuantReviewer(transport=transport).review(request)
        self.assertEqual(result.status, QuantReviewerAvailability.AVAILABLE)
        self.assertEqual(result.evidence.state, QuantReviewState.RESEARCH_REQUIRED)
        self.assertFalse(result.evidence.broker_write_authority)
        self.assertEqual(len(calls), 2)

    def test_m153_qwen_digest_drift_fails_to_unavailable(self):
        expected = _digest("expected")

        def transport(method, url, payload, timeout):
            return {"models": [{"name": "qwen3:1.7b", "model": "qwen3:1.7b", "digest": _digest("other")}]}

        request = QuantReviewRequest(
            "review-2", "qwen3:1.7b", expected, (_digest("forecast"),), (), (), "score", "question"
        )
        result = OllamaQuantReviewer(transport=transport).review(request)
        self.assertFalse(result.available)
        self.assertIn("digest_mismatch", result.error)

    def test_m154_heartbeat_persists_board_and_resumes_failed_stage(self):
        kinds = {
            ResearchStage.ACQUIRE: BlackboardKind.SOURCE,
            ResearchStage.FORECAST: BlackboardKind.FORECAST,
            ResearchStage.SCORE: BlackboardKind.SCORECARD,
            ResearchStage.INTAKE: BlackboardKind.STRATEGY,
            ResearchStage.SCREEN: BlackboardKind.EXPERIMENT,
            ResearchStage.EXPERIMENT: BlackboardKind.EXPERIMENT,
            ResearchStage.ATTRIBUTE: BlackboardKind.ATTRIBUTION,
            ResearchStage.REMEMBER: BlackboardKind.LESSON,
        }
        times = iter(datetime(2026, 9, 4, 12, tzinfo=timezone.utc) + timedelta(seconds=i) for i in range(30))
        clock = lambda: next(times)

        def handler(stage):
            def run(board):
                return StageWork(
                    items=(BlackboardItem(kinds[stage], stage.name.lower(), _digest(f"payload:{stage.name}")),),
                    completed_job_fingerprints=(_digest(f"job:{stage.name}"),),
                )
            return run

        handlers = {stage: handler(stage) for stage in kinds}
        initial = ResearchBlackboard("cycle-154", datetime(2026, 9, 4, 11, tzinfo=timezone.utc), ())

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "organism.sqlite3"
            store = SQLiteResearchOrganismStore(path)
            failing = dict(handlers)

            def fail_experiment(board):
                raise RuntimeError("synthetic interruption")

            failing[ResearchStage.EXPERIMENT] = fail_experiment
            organism = ResearchOrganism(store, clock=clock)
            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                organism.run_until_complete(initial, failing)
            self.assertEqual(store.cycle_store.latest("cycle-154").stage, ResearchStage.SCREEN)
            store.close()

            reopened = SQLiteResearchOrganismStore(path)
            try:
                result = ResearchOrganism(reopened, clock=clock).run_until_complete(initial, handlers)
                self.assertEqual(result.checkpoint.stage, ResearchStage.COMPLETE)
                self.assertIn(ResearchStage.EXPERIMENT, result.stages_completed)
                self.assertFalse(result.broker_write_authority)
                self.assertFalse(result.promotion_authority)
                self.assertTrue(reopened.integrity_ok())
                replay = ResearchOrganism(reopened, clock=clock).run_until_complete(initial, handlers)
                self.assertEqual(replay.stages_completed, ())
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
