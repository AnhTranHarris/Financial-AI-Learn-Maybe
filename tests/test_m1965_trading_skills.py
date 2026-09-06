from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from dusty.champion_registry import ChampionLifecycleState, FrozenChampionRecord
from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp
from dusty.single_desk_demo_certification import (
    SingleDeskDemoCertification,
    SingleDeskDemoStatus,
)
from dusty.source_intake import (
    EvidenceClass,
    ProposalCompleteness,
    SourceAccess,
    SourceSnapshot,
    StrategyProposal,
)
from dusty.strategy_catalog import OperatingMode, StrategyCatalogEntry, StrategyStage
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    ReconstructedResearchPackage,
    SkillCompetency,
    SkillEvidenceKind,
    SkillEvidenceRef,
    SkillRouteContext,
    SkillRouteStatus,
    build_trading_skill,
    reconstruct_strategy,
    resolve_research_package_from_library,
    route_auto_skill,
    strategy_catalog_projection,
)


NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64


def proposal(*, unresolved: tuple[str, ...] = ("entry_logic", "exit_logic", "risk_logic")) -> StrategyProposal:
    snap = SourceSnapshot(
        "myfxbook",
        "https://www.myfxbook.com/strategies/example/1",
        NOW - timedelta(days=1),
        H("a"),
        SourceAccess.MANUAL_REVIEW,
        False,
    )
    return StrategyProposal(
        "myfxbook:example",
        snap,
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.PARTIAL,
        "Example trend concept",
        symbols=("EURUSD",),
        timeframes=("M15",),
        components=("rsi", "trend"),
        declared_rules=(("concept", "trend-following"),),
        unresolved=unresolved,
        claimed_performance=(("return", "999% claimed"),),
        tags=("source:myfxbook", "research_only"),
    )


def candidate(strategy_id: str = "recon-eurusd-long", *, hft: bool = False) -> StrategySpecV2:
    return StrategySpecV2(
        strategy_id=strategy_id,
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((Clause("rsi", RuleOp.GE, 55.0), Clause("return_1", RuleOp.GT, 0.0))),),
        exit_plan=ExitPlan("atr:2", "rr:2", max_hold_steps=16),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=240,
        cooldown_steps=4,
        is_hft=hft,
    )


def reconstruction(*, strategy_id: str = "recon-eurusd-long"):
    return reconstruct_strategy(
        proposal(),
        candidate_spec=candidate(strategy_id),
        symbols=("EURUSD",),
        timeframe="M15",
        rules=(
            ReconstructionRule("concept", "trend-following", ReconstructionRuleBasis.SOURCE_DECLARED),
            ReconstructionRule("entry_logic", "RSI >= 55 and return_1 > 0", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("exit_logic", "ATR 2 stop and RR 2 target", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("risk_logic", "bounded by Dusty constitution", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ),
        actor=ReconstructionActor.OLLAMA,
        actor_fingerprint=H("b"),
        created_at=NOW,
    )


def champion(*, marker: str = "1", lifecycle_family: str = "trend") -> FrozenChampionRecord:
    return FrozenChampionRecord(
        "general-eurusd",
        f"generation-{marker}",
        lifecycle_family,
        H(marker),
        H("2" if marker != "2" else "3"),
        (H("4"),),
        "596d996e156b530697158eadc596bf475f64ad68",
        H("5"),
        H("6"),
        None,
        None,
        NOW - timedelta(days=10),
    )


def skill(*, marker: str = "1", state: ChampionLifecycleState = ChampionLifecycleState.ACTIVE):
    row = champion(marker=marker, lifecycle_family=f"family-{marker}")
    refs = (
        SkillEvidenceRef(SkillEvidenceKind.ROBUSTNESS, row.robustness_fingerprint),
        SkillEvidenceRef(SkillEvidenceKind.OTHER, row.selection_evidence_fingerprint),
    )
    return build_trading_skill(
        row,
        lifecycle_state=state,
        symbols=("EURUSD",),
        timeframes=("M15",),
        evidence=refs,
        demo_certification=None,
        created_at=NOW - timedelta(days=1),
    )


def competency(skill_row, score: float = 0.8, *, eligible: bool = True, evaluated_at=None, valid_until=None):
    return SkillCompetency(
        skill_row.fingerprint,
        "EURUSD",
        "M15",
        "london_ny",
        "trend_expansion",
        eligible,
        score,
        H("7"),
        evaluated_at or NOW - timedelta(minutes=30),
        valid_until or NOW + timedelta(hours=1),
    )


class M1965TradingSkillsTests(unittest.TestCase):
    def test_reconstruction_preserves_unknown_source_rules(self) -> None:
        row = reconstruction()
        self.assertEqual(row.unresolved_source_rules, ("entry_logic", "exit_logic", "risk_logic"))
        self.assertFalse(row.source_claim_complete)
        self.assertEqual(row.hypothesis_rule_count, 3)
        self.assertFalse(row.broker_write_authority)
        self.assertFalse(row.promotion_authority)

    def test_source_declared_rule_must_match_archived_source(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not match archived proposal"):
            reconstruct_strategy(
                proposal(),
                candidate_spec=candidate(),
                symbols=("EURUSD",),
                timeframe="M15",
                rules=(ReconstructionRule("concept", "mean-reversion", ReconstructionRuleBasis.SOURCE_DECLARED),),
                actor=ReconstructionActor.CARSON_USER_REVIEW,
                actor_fingerprint=H("b"),
                created_at=NOW,
            )

    def test_declared_source_rule_cannot_disappear(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing reconstruction attribution"):
            reconstruct_strategy(
                proposal(),
                candidate_spec=candidate(),
                symbols=("EURUSD",),
                timeframe="M15",
                rules=(ReconstructionRule("entry_logic", "hypothesis", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),),
                actor=ReconstructionActor.DUSTY_RESEARCH,
                actor_fingerprint=H("b"),
                created_at=NOW,
            )

    def test_reconstruction_cannot_smuggle_prohibited_hft(self) -> None:
        with self.assertRaisesRegex(ValueError, "violates strategy constitution"):
            reconstruct_strategy(
                proposal(),
                candidate_spec=candidate(hft=True),
                symbols=("EURUSD",),
                timeframe="M15",
                rules=(ReconstructionRule("concept", "trend-following", ReconstructionRuleBasis.SOURCE_DECLARED),),
                actor=ReconstructionActor.OLLAMA,
                actor_fingerprint=H("b"),
                created_at=NOW,
            )

    def test_reconstruction_timeframe_must_match_executable_spec(self) -> None:
        with self.assertRaisesRegex(ValueError, "timeframe must exactly match"):
            reconstruct_strategy(
                proposal(),
                candidate_spec=candidate(),
                symbols=("EURUSD",),
                timeframe="H1",
                rules=(ReconstructionRule("concept", "trend-following", ReconstructionRuleBasis.SOURCE_DECLARED),),
                actor=ReconstructionActor.OLLAMA,
                actor_fingerprint=H("b"),
                created_at=NOW,
            )

    def test_reconstructed_package_is_exact_executable_research_artifact(self) -> None:
        row = reconstruction()
        package = ReconstructedResearchPackage(row)
        self.assertEqual(package.catalog_entry.stage, StrategyStage.BACKTEST_CANDIDATE)
        self.assertEqual(package.catalog_entry.strategy_hash, row.candidate_spec.strategy_hash)
        self.assertEqual(package.compiled.spec.strategy_hash, row.candidate_spec.strategy_hash)

    def test_catalog_projection_shows_benchmarks_and_reconstruction(self) -> None:
        row = reconstruction()
        catalog = strategy_catalog_projection(reconstructions=(row,))
        ids = {entry.strategy_id for entry in catalog}
        self.assertIn(row.candidate_spec.strategy_id, ids)
        self.assertIn("research-rsi-momentum-long-v1", ids)
        self.assertIn("research-rsi-momentum-short-v1", ids)

    def test_arbitrary_catalog_metadata_is_not_executable(self) -> None:
        arbitrary = StrategyCatalogEntry(
            "internet-claim",
            "Internet claim",
            H("8"),
            StrategyStage.BACKTEST_CANDIDATE,
            universal_symbol_compatibility=True,
            source_url="https://example.com",
            timeframe="M15",
        )
        with self.assertRaisesRegex(ValueError, "no exact executable research artifact"):
            resolve_research_package_from_library(arbitrary, (reconstruction(),))

    def test_exact_reconstruction_resolves_for_backtest(self) -> None:
        row = reconstruction()
        package = resolve_research_package_from_library(ReconstructedResearchPackage(row).catalog_entry, (row,))
        self.assertEqual(package.compiled.spec.strategy_hash, row.candidate_spec.strategy_hash)

    def test_active_skill_without_real_demo_proof_is_not_demo_certified(self) -> None:
        row = skill()
        self.assertEqual(row.stage, StrategyStage.BACKTEST_CERTIFIED)
        self.assertIsNone(row.demo_certification_fingerprint)
        self.assertFalse(row.live_write_authority)

    def test_noncertified_m194_object_cannot_create_demo_skill(self) -> None:
        champ = champion()
        fake = SingleDeskDemoCertification(
            SingleDeskDemoStatus.PENDING,
            champ.fingerprint,
            H("9"),
            H("a"),
            (),
            (),
            ("real_demo_runtime_evidence_required",),
            (),
            H("b"),
            False,
        )
        refs = (
            SkillEvidenceRef(SkillEvidenceKind.ROBUSTNESS, champ.robustness_fingerprint),
            SkillEvidenceRef(SkillEvidenceKind.OTHER, champ.selection_evidence_fingerprint),
        )
        with self.assertRaisesRegex(ValueError, "actual M194 CERTIFIED"):
            build_trading_skill(
                champ,
                lifecycle_state=ChampionLifecycleState.ACTIVE,
                symbols=("EURUSD",),
                timeframes=("M15",),
                evidence=refs,
                demo_certification=fake,
                created_at=NOW,
            )

    def test_suspended_skill_is_visible_but_restricted(self) -> None:
        row = skill(state=ChampionLifecycleState.SUSPENDED)
        self.assertEqual(row.stage, StrategyStage.RESTRICTED)
        projection = strategy_catalog_projection(skills=(row,), include_reviewed_benchmarks=False)
        self.assertEqual(projection[0].stage, StrategyStage.RESTRICTED)

    def test_auto_router_selects_unique_best_active_skill_without_requiring_old_champion_commit(self) -> None:
        row = skill()
        context = SkillRouteContext(
            NOW,
            "EURUSD",
            "M15",
            "london_ny",
            "trend_expansion",
            OperatingMode.BACKTEST,
            "6bbb7485e1cc73114507210f9dffa84cd7443c3c",
            True,
        )
        decision = route_auto_skill(context, (row,), (competency(row),))
        self.assertEqual(decision.status, SkillRouteStatus.SELECTED)
        self.assertEqual(decision.selected_skill_fingerprint, row.fingerprint)
        self.assertIn("M195_PORTFOLIO_RISK", decision.downstream_gates)
        self.assertIn("M196_CONCENTRATION", decision.downstream_gates)
        self.assertIn("M197_FEASIBILITY", decision.downstream_gates)
        self.assertFalse(decision.broker_write_authority)

    def test_auto_router_rejects_suspended_skill(self) -> None:
        row = skill(state=ChampionLifecycleState.SUSPENDED)
        context = SkillRouteContext(NOW, "EURUSD", "M15", "london_ny", "trend_expansion", OperatingMode.BACKTEST, H("c"), True)
        self.assertEqual(route_auto_skill(context, (row,), (competency(row),)).status, SkillRouteStatus.NO_MATCH)

    def test_demo_router_requires_real_demo_qualified_skill(self) -> None:
        row = skill()
        context = SkillRouteContext(NOW, "EURUSD", "M15", "london_ny", "trend_expansion", OperatingMode.DEMO, H("c"), True)
        self.assertEqual(route_auto_skill(context, (row,), (competency(row),)).status, SkillRouteStatus.NO_MATCH)

    def test_live_nonshadow_routing_is_locked(self) -> None:
        row = skill()
        context = SkillRouteContext(NOW, "EURUSD", "M15", "london_ny", "trend_expansion", OperatingMode.LIVE, H("c"), False)
        decision = route_auto_skill(context, (row,), (competency(row),))
        self.assertEqual(decision.status, SkillRouteStatus.LIVE_LOCKED)
        self.assertIsNone(decision.selected_skill_fingerprint)
        self.assertFalse(decision.live_write_authority)

    def test_equal_top_scores_abstain_instead_of_hidden_tiebreak(self) -> None:
        first = skill(marker="1")
        second = skill(marker="2")
        context = SkillRouteContext(NOW, "EURUSD", "M15", "london_ny", "trend_expansion", OperatingMode.BACKTEST, H("c"), True)
        decision = route_auto_skill(context, (first, second), (competency(first), competency(second)))
        self.assertEqual(decision.status, SkillRouteStatus.AMBIGUOUS)
        self.assertIsNone(decision.selected_skill_fingerprint)

    def test_ineligible_future_or_expired_competency_cannot_route(self) -> None:
        row = skill()
        context = SkillRouteContext(NOW, "EURUSD", "M15", "london_ny", "trend_expansion", OperatingMode.BACKTEST, H("c"), True)
        future = competency(row, evaluated_at=NOW + timedelta(minutes=1), valid_until=NOW + timedelta(hours=1))
        expired = competency(row, evaluated_at=NOW - timedelta(hours=2), valid_until=NOW - timedelta(minutes=1))
        denied = competency(row, eligible=False)
        for evidence in (future, expired, denied):
            self.assertEqual(route_auto_skill(context, (row,), (evidence,)).status, SkillRouteStatus.NO_MATCH)

    def test_context_must_match_exact_symbol_timeframe_session_and_regime(self) -> None:
        row = skill()
        base = dict(at=NOW, symbol="EURUSD", timeframe="M15", session="london_ny", regime="trend_expansion", mode=OperatingMode.BACKTEST, source_commit=H("c"), shadow_only=True)
        for key, value in (("symbol", "GBPUSD"), ("timeframe", "H1"), ("session", "asia"), ("regime", "range")):
            args = dict(base)
            args[key] = value
            decision = route_auto_skill(SkillRouteContext(**args), (row,), (competency(row),))
            self.assertEqual(decision.status, SkillRouteStatus.NO_MATCH)

    def test_m1965_module_has_no_broker_write_or_authority_import(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "dusty" / "trading_skills.py").read_text(encoding="utf-8")
        forbidden = (
            "MetaTrader5",
            "order_send(",
            "DemoMT5ExecutionAdapter",
            "SQLitePortfolioRiskGovernor(",
            "SQLiteCorrelationConcentrationGuard(",
        )
        for token in forbidden:
            self.assertNotIn(token, source)
        self.assertIn('return ("Guardian", "M195_PORTFOLIO_RISK", "M196_CONCENTRATION", "M197_FEASIBILITY")', source)


if __name__ == "__main__":
    unittest.main()
