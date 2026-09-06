from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dusty.experience import TradeSide
from dusty.ollama_strategy_classifier import (
    ClassificationAvailability,
    OllamaStrategyClassifier,
    QuantStrategyClassification,
    attach_quant_identity,
)
from dusty.research import Clause, RuleOp
from dusty.source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal
from dusty.strategy_estate import default_strategy_estate_path, load_strategy_estate, register_reconstruction
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_taxonomy import (
    QuantStrategyIdentity,
    StrategyArchetype,
    StrategyCatalyst,
    StrategySessionProfile,
    StrategyStructure,
    catalog_entry_for_reconstruction,
    quant_title_for_reconstruction,
)
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    reconstruct_strategy,
)


NOW = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64


def reconstruction(*, strategy_id: str = "estate-breakout-v1", title: str = "external breakout hypothesis"):
    proposal = StrategyProposal(
        "external:estate-test",
        SourceSnapshot(
            "myfxbook",
            "https://www.myfxbook.com/strategies/example/2",
            NOW,
            H("a"),
            SourceAccess.MANUAL_REVIEW,
            False,
        ),
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.PARTIAL,
        title,
        symbols=("EURUSD",),
        timeframes=("M15",),
        components=("breakout",),
        declared_rules=(("concept", "breakout"),),
        unresolved=("entry_logic", "exit_logic", "risk_logic"),
        tags=("research_only",),
    )
    spec = StrategySpecV2(
        strategy_id,
        TradeSide.LONG,
        (RuleGroup((Clause("return_1", RuleOp.GT, 0.0), Clause("rsi", RuleOp.GE, 55.0))),),
        ExitPlan("atr:2", "rr:2", max_hold_steps=12),
        15,
        180,
        cooldown_steps=4,
    )
    return reconstruct_strategy(
        proposal,
        candidate_spec=spec,
        symbols=("EURUSD",),
        timeframe="M15",
        rules=(
            ReconstructionRule("concept", "breakout", ReconstructionRuleBasis.SOURCE_DECLARED),
            ReconstructionRule("entry_logic", "return_1 > 0 and RSI >= 55", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("exit_logic", "ATR 2 stop and RR 2 target", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("risk_logic", "Dusty constitution", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ),
        actor=ReconstructionActor.OLLAMA,
        actor_fingerprint=H("b"),
        created_at=NOW,
    )


def classified(row, identity: QuantStrategyIdentity):
    result = QuantStrategyClassification(identity, "qwen-test", H("c"), H("d"))
    return attach_quant_identity(row, result)


class M1965StrategyEstateQuantTitleTests(unittest.TestCase):
    def test_legacy_breakout_gets_quant_title_not_source_marketing_title(self) -> None:
        title = quant_title_for_reconstruction(reconstruction(title="BEST PROFIT EA 9000 breakout"))
        self.assertEqual(title, "Price-Structure Breakout · EURUSD · M15 · Long")
        self.assertNotIn("BEST", title)

    def test_fibonacci_asia_to_london_ny_title_is_deterministic(self) -> None:
        row = classified(
            reconstruction(title="Fibonacci Asian session to London/New York continuation"),
            QuantStrategyIdentity(
                StrategyArchetype.SESSION_HANDOFF,
                StrategyCatalyst.SESSION_OPEN,
                StrategyStructure.FIBONACCI,
                StrategySessionProfile.ASIA_TO_LONDON_NY,
            ),
        )
        self.assertEqual(
            quant_title_for_reconstruction(row),
            "Fibonacci Asia→London/NY Continuation · EURUSD · M15 · Long",
        )

    def test_tech_news_and_sec_runner_titles_are_quant_titles(self) -> None:
        tech = classified(
            reconstruction(strategy_id="tech-v1", title="technology news volatility breakout"),
            QuantStrategyIdentity(
                StrategyArchetype.BREAKOUT,
                StrategyCatalyst.TECH_NEWS,
                StrategyStructure.VOLATILITY,
            ),
        )
        sec = classified(
            reconstruction(strategy_id="sec-v1", title="SEC filing momentum runner"),
            QuantStrategyIdentity(
                StrategyArchetype.CATALYST_RUNNER,
                StrategyCatalyst.SEC_FILING,
                StrategyStructure.MOMENTUM,
            ),
        )
        self.assertTrue(quant_title_for_reconstruction(tech).startswith("Tech-News Volatility Breakout"))
        self.assertTrue(quant_title_for_reconstruction(sec).startswith("SEC Filing Momentum Runner"))

    def test_quant_catalog_preserves_machine_strategy_identity_and_hash(self) -> None:
        row = classified(
            reconstruction(),
            QuantStrategyIdentity(StrategyArchetype.REVERSAL, structure=StrategyStructure.MOMENTUM),
        )
        entry = catalog_entry_for_reconstruction(row)
        self.assertEqual(entry.strategy_id, row.candidate_spec.strategy_id)
        self.assertEqual(entry.strategy_hash, row.candidate_spec.strategy_hash)
        self.assertEqual(entry.allowed_symbols, ("EURUSD",))
        self.assertIn("Reversal", entry.title)

    def test_partial_quant_identity_fails_closed(self) -> None:
        row = reconstruction()
        broken = replace(
            row,
            rules=(*row.rules, ReconstructionRule(
                "identity.archetype", "breakout", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS
            )),
        )
        with self.assertRaisesRegex(ValueError, "partial quant strategy identity"):
            quant_title_for_reconstruction(broken)

    def test_default_estate_path_uses_localappdata_on_windows(self) -> None:
        path = default_strategy_estate_path({"LOCALAPPDATA": r"C:\Local"})
        self.assertEqual(path, Path(r"C:\Local") / "DustyDragon" / "strategy-estate" / "reconstructions.json")

    def test_estate_merge_is_idempotent_and_hash_verified(self) -> None:
        row = reconstruction()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "estate.json"
            first = register_reconstruction(row, path=path)
            second = register_reconstruction(row, path=path)
            loaded = load_strategy_estate(path)
        self.assertEqual(first.added, 1)
        self.assertEqual(second.added, 0)
        self.assertEqual(second.total, 1)
        self.assertEqual(loaded, (row,))

    def test_estate_rejects_same_strategy_id_with_different_identity(self) -> None:
        row = reconstruction()
        other = reconstruction(strategy_id=row.candidate_spec.strategy_id, title="different source breakout title")
        with TemporaryDirectory() as temp:
            path = Path(temp) / "estate.json"
            register_reconstruction(row, path=path)
            with self.assertRaisesRegex(ValueError, "strategy_id collision"):
                register_reconstruction(other, path=path)

    def test_estate_corruption_fails_closed(self) -> None:
        row = reconstruction()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "estate.json"
            register_reconstruction(row, path=path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["unexpected"] = True
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema mismatch"):
                load_strategy_estate(path)

    def test_classifier_revalidates_schema_and_rejects_marketing_title_field(self) -> None:
        def transport(method, url, payload, timeout):
            if method == "GET":
                return {"models": [{"name": "qwen-test", "digest": H("c")}]}
            return {"message": {"content": json.dumps({
                "archetype": "breakout",
                "catalyst": "none",
                "structure": "price_action",
                "session_profile": "unrestricted",
                "marketing_title": "ULTRA PROFIT BOT",
            })}}

        result = OllamaStrategyClassifier(transport=transport).classify(
            reconstruction(), model_tag="qwen-test", model_digest=H("c")
        )
        self.assertEqual(result.status, ClassificationAvailability.UNAVAILABLE)
        self.assertIn("schema mismatch", result.error)

    def test_classifier_rejects_unsupported_sec_or_session_claim(self) -> None:
        responses = (
            {
                "archetype": "catalyst_runner",
                "catalyst": "sec_filing",
                "structure": "momentum",
                "session_profile": "unrestricted",
            },
            {
                "archetype": "session_handoff",
                "catalyst": "session_open",
                "structure": "compression",
                "session_profile": "asia_to_london_ny",
            },
        )
        for response in responses:
            with self.subTest(response=response):
                def transport(method, url, payload, timeout, response=response):
                    if method == "GET":
                        return {"models": [{"name": "qwen-test", "digest": H("c")}]}
                    return {"message": {"content": json.dumps(response)}}

                result = OllamaStrategyClassifier(transport=transport).classify(
                    reconstruction(), model_tag="qwen-test", model_digest=H("c")
                )
                self.assertEqual(result.status, ClassificationAvailability.UNAVAILABLE)
                self.assertIn("evidence support", result.error)

    def test_classifier_valid_enums_attach_provenance_and_render_title(self) -> None:
        def transport(method, url, payload, timeout):
            if method == "GET":
                return {"models": [{"model": "qwen-test", "digest": H("c")}]}
            return {"message": {"content": json.dumps({
                "archetype": "session_handoff",
                "catalyst": "session_open",
                "structure": "compression",
                "session_profile": "asia_to_london_ny",
            })}}

        source = reconstruction(title="Asian compression session handoff to London/New York")
        result = OllamaStrategyClassifier(transport=transport).classify(
            source, model_tag="qwen-test", model_digest=H("c")
        )
        self.assertTrue(result.available, result.error)
        attached = attach_quant_identity(source, result.classification)
        self.assertEqual(
            quant_title_for_reconstruction(attached),
            "Asian Compression → London/NY Expansion · EURUSD · M15 · Long",
        )
        names = {rule.name for rule in attached.rules}
        self.assertIn("identity.classifier_model_digest", names)
        self.assertIn("identity.classifier_raw_sha256", names)

    def test_classifier_model_digest_mismatch_is_unavailable(self) -> None:
        def transport(method, url, payload, timeout):
            return {"models": [{"name": "qwen-test", "digest": H("d")}]}

        result = OllamaStrategyClassifier(transport=transport).classify(
            reconstruction(), model_tag="qwen-test", model_digest=H("c")
        )
        self.assertEqual(result.status, ClassificationAvailability.UNAVAILABLE)
        self.assertIn("digest_mismatch", result.error)

    def test_estate_and_classifier_have_no_trading_authority(self) -> None:
        import dusty.strategy_estate as estate
        classifier = OllamaStrategyClassifier(transport=lambda *args: {})
        for name in (
            "broker_write_authority", "live_write_authority", "promotion_authority",
            "risk_override_authority", "guardian_override_authority",
        ):
            self.assertFalse(getattr(estate, name))
            self.assertFalse(getattr(classifier, name))


if __name__ == "__main__":
    unittest.main()
