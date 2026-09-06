from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dusty.experience import TradeSide
from dusty.ollama_strategy_classifier import (
    ClassificationAvailability,
    QuantStrategyClassification,
    QuantStrategyClassificationResult,
)
from dusty.ollama_strategy_reconstruction import (
    LocalStrategyReconstructionResult,
    ReconstructionAvailability,
)
from dusty.research import Clause, RuleOp
from dusty.source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal
from dusty.strategy_estate import load_strategy_estate, register_reconstruction
from dusty.strategy_estate_builder import EstatePopulationStatus, StrategyEstateBuilder
from dusty.strategy_estate_cli import DEFAULT_SESSIONS, installed_model_digest
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_seed_proposals import starter_strategy_proposals
from dusty.strategy_taxonomy import (
    QuantStrategyIdentity,
    StrategyArchetype,
    StrategyCatalyst,
    StrategySessionProfile,
    StrategyStructure,
)
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    reconstruct_strategy,
)


NOW = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64


def _proposal(symbol: str = "EURUSD", timeframe: str = "M15") -> StrategyProposal:
    return StrategyProposal(
        f"test:{symbol}:{timeframe}",
        SourceSnapshot(
            "dusty-research",
            "http://localhost/dusty/test",
            NOW,
            H("a"),
            SourceAccess.AUTHENTICATED_TOOL,
            True,
        ),
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.CONCEPT_ONLY,
        "test breakout",
        symbols=(symbol,),
        timeframes=(timeframe,),
        components=("breakout",),
        declared_rules=(("research_family", "test breakout"),),
        unresolved=("entry_logic", "exit_logic", "risk_logic"),
    )


def _reconstruction(proposal: StrategyProposal):
    spec = StrategySpecV2(
        f"candidate-{proposal.symbols[0].lower()}-v1",
        TradeSide.LONG,
        (RuleGroup((Clause("return_1", RuleOp.GT, 0.0),)),),
        ExitPlan("atr:2", "rr:2", max_hold_steps=12),
        15,
        180,
    )
    return reconstruct_strategy(
        proposal,
        candidate_spec=spec,
        symbols=proposal.symbols,
        timeframe="M15",
        rules=(
            ReconstructionRule("research_family", "test breakout", ReconstructionRuleBasis.SOURCE_DECLARED),
            ReconstructionRule("entry_logic", "return_1 > 0", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("exit_logic", "ATR stop / RR target", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("risk_logic", "Dusty constitution", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ),
        actor=ReconstructionActor.OLLAMA,
        actor_fingerprint=H("b"),
        created_at=NOW,
    )


class _Reconstructor:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls = 0

    def reconstruct(self, request, *, created_at):
        self.calls += 1
        if not self.available:
            return LocalStrategyReconstructionResult(
                ReconstructionAvailability.UNAVAILABLE,
                error="fake_reconstruction_failure",
            )
        row = _reconstruction(request.proposal)
        return LocalStrategyReconstructionResult(
            ReconstructionAvailability.AVAILABLE,
            row,
            H("c"),
            H("d"),
        )


class _Classifier:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls = 0

    def classify(self, reconstruction, *, model_tag, model_digest):
        self.calls += 1
        if not self.available:
            return QuantStrategyClassificationResult(
                ClassificationAvailability.UNAVAILABLE,
                error="fake_classification_failure",
            )
        classification = QuantStrategyClassification(
            QuantStrategyIdentity(
                StrategyArchetype.BREAKOUT,
                StrategyCatalyst.NONE,
                StrategyStructure.PRICE_ACTION,
                StrategySessionProfile.UNRESTRICTED,
            ),
            model_tag,
            model_digest,
            H("e"),
        )
        return QuantStrategyClassificationResult(
            ClassificationAvailability.AVAILABLE,
            classification,
        )


class M1965StrategyEstatePopulationTests(unittest.TestCase):
    def test_starter_seeds_cover_first_three_pc_symbols_without_claiming_completeness(self) -> None:
        rows = starter_strategy_proposals()
        symbols = {symbol for row in rows for symbol in row.symbols}
        self.assertEqual(symbols, {"EURUSD", "XAUUSD", "NASUSD"})
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(row.evidence_class is EvidenceClass.STRATEGY_HYPOTHESIS for row in rows))
        self.assertTrue(all(row.completeness is ProposalCompleteness.CONCEPT_ONLY for row in rows))
        self.assertTrue(all(row.timeframes == ("M15",) for row in rows))
        self.assertTrue(all("research_only" in row.tags for row in rows))

    def test_sub_m5_proposal_is_rejected_without_calling_ollama(self) -> None:
        reconstructor = _Reconstructor()
        classifier = _Classifier()
        builder = StrategyEstateBuilder(reconstructor=reconstructor, classifier=classifier)
        with TemporaryDirectory() as temp:
            result = builder.populate(
                (_proposal(timeframe="M1"),),
                model_tag="qwen-test",
                model_digest=H("f"),
                allowed_symbols=("EURUSD",),
                allowed_timeframes=("M1", "M15"),
                allowed_features=("return_1",),
                allowed_sessions=DEFAULT_SESSIONS,
                estate_path=Path(temp) / "estate.json",
                created_at=NOW,
            )
        self.assertEqual(result.rows[0].status, EstatePopulationStatus.INELIGIBLE_PROPOSAL)
        self.assertEqual(reconstructor.calls, 0)
        self.assertEqual(classifier.calls, 0)

    def test_classification_failure_does_not_register_candidate(self) -> None:
        builder = StrategyEstateBuilder(reconstructor=_Reconstructor(), classifier=_Classifier(available=False))
        with TemporaryDirectory() as temp:
            path = Path(temp) / "estate.json"
            result = builder.populate(
                (_proposal(),),
                model_tag="qwen-test",
                model_digest=H("f"),
                allowed_symbols=("EURUSD",),
                allowed_timeframes=("M15",),
                allowed_features=("return_1",),
                allowed_sessions=DEFAULT_SESSIONS,
                estate_path=path,
                created_at=NOW,
            )
            self.assertFalse(path.exists())
        self.assertEqual(result.rows[0].status, EstatePopulationStatus.CLASSIFICATION_UNAVAILABLE)
        self.assertIsNone(result.estate_update)

    def test_successful_population_persists_quant_identity(self) -> None:
        builder = StrategyEstateBuilder(reconstructor=_Reconstructor(), classifier=_Classifier())
        with TemporaryDirectory() as temp:
            path = Path(temp) / "estate.json"
            result = builder.populate(
                (_proposal(),),
                model_tag="qwen-test",
                model_digest=H("f"),
                allowed_symbols=("EURUSD",),
                allowed_timeframes=("M15",),
                allowed_features=("return_1", "rsi"),
                allowed_sessions=DEFAULT_SESSIONS,
                estate_path=path,
                created_at=NOW,
            )
            loaded = load_strategy_estate(path)
        self.assertEqual(result.added_count, 1)
        self.assertEqual(len(loaded), 1)
        rules = {rule.name: rule.value for rule in loaded[0].rules}
        self.assertEqual(rules["identity.archetype"], "breakout")
        self.assertEqual(rules["identity.structure"], "price_action")

    def test_naive_population_time_is_rejected_before_model_call(self) -> None:
        reconstructor = _Reconstructor()
        builder = StrategyEstateBuilder(reconstructor=reconstructor, classifier=_Classifier())
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            builder.populate(
                (_proposal(),),
                model_tag="qwen-test",
                model_digest=H("f"),
                allowed_symbols=("EURUSD",),
                allowed_timeframes=("M15",),
                allowed_features=("return_1",),
                allowed_sessions=DEFAULT_SESSIONS,
                created_at=datetime(2026, 9, 6, 3, 0),
            )
        self.assertEqual(reconstructor.calls, 0)

    def test_cli_model_discovery_is_localhost_only_and_exact_digest(self) -> None:
        with self.assertRaisesRegex(ValueError, "localhost HTTP"):
            installed_model_digest("qwen-test", base_url="https://example.com")
        with patch("dusty.strategy_estate_cli._urllib_transport", return_value={
            "models": [{"name": "qwen-test", "digest": H("f")}]
        }):
            self.assertEqual(installed_model_digest("qwen-test"), H("f"))

    def test_skills_ui_auto_loads_default_estate_and_uses_quant_title(self) -> None:
        from dusty import skills_ui
        row = _reconstruction(_proposal())
        classification = QuantStrategyClassification(
            QuantStrategyIdentity(
                StrategyArchetype.SESSION_HANDOFF,
                StrategyCatalyst.SESSION_OPEN,
                StrategyStructure.COMPRESSION,
                StrategySessionProfile.ASIA_TO_LONDON_NY,
            ),
            "qwen-test", H("f"), H("e"),
        )
        from dusty.ollama_strategy_classifier import attach_quant_identity
        row = attach_quant_identity(row, classification)

        with TemporaryDirectory() as temp:
            estate = Path(temp) / "estate.json"
            register_reconstruction(row, path=estate)
            observed = {}

            def fake_ui(argv):
                index = argv.index("--catalog")
                import json
                catalog = json.loads(Path(argv[index + 1]).read_text(encoding="utf-8"))
                observed["titles"] = {entry["strategy_id"]: entry["title"] for entry in catalog}
                return 17

            with patch("dusty.skills_ui.default_strategy_estate_path", return_value=estate), patch(
                "dusty.skills_ui.basic_ui.main", side_effect=fake_ui
            ):
                result = skills_ui.main(["--repository", "."])

        self.assertEqual(result, 17)
        self.assertEqual(
            observed["titles"][row.candidate_spec.strategy_id],
            "Asian Compression → London/NY Expansion · EURUSD · M15 · Long",
        )


if __name__ == "__main__":
    unittest.main()
