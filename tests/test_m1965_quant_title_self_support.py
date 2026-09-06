from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import unittest

from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp
from dusty.source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_taxonomy import quant_title_for_reconstruction
from dusty.trading_skills import ReconstructionActor, ReconstructionRule, ReconstructionRuleBasis, reconstruct_strategy


NOW = datetime(2026, 9, 6, 3, 15, tzinfo=timezone.utc)
H = lambda ch: ch * 64


class M1965QuantTitleSelfSupportTests(unittest.TestCase):
    def test_persisted_identity_rules_cannot_supply_their_own_sec_or_session_evidence(self) -> None:
        proposal = StrategyProposal(
            "test:self-support",
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
            "plain price breakout",
            symbols=("EURUSD",),
            timeframes=("M15",),
            components=("breakout",),
            declared_rules=(("research_family", "plain price breakout"),),
            unresolved=("entry_logic", "exit_logic", "risk_logic"),
        )
        spec = StrategySpecV2(
            "self-support-v1",
            TradeSide.LONG,
            (RuleGroup((Clause("return_1", RuleOp.GT, 0.0),)),),
            ExitPlan("atr:2", "rr:2", max_hold_steps=12),
            15,
            180,
        )
        row = reconstruct_strategy(
            proposal,
            candidate_spec=spec,
            symbols=("EURUSD",),
            timeframe="M15",
            rules=(
                ReconstructionRule("research_family", "plain price breakout", ReconstructionRuleBasis.SOURCE_DECLARED),
                ReconstructionRule("entry_logic", "return_1 > 0", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
                ReconstructionRule("exit_logic", "ATR stop / RR target", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
                ReconstructionRule("risk_logic", "Dusty constitution", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ),
            actor=ReconstructionActor.OLLAMA,
            actor_fingerprint=H("b"),
            created_at=NOW,
        )
        malicious = replace(
            row,
            rules=(
                *row.rules,
                ReconstructionRule("identity.archetype", "catalyst_runner", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
                ReconstructionRule("identity.catalyst", "sec_filing", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
                ReconstructionRule("identity.structure", "momentum", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
                ReconstructionRule("identity.session_profile", "asia_to_london_ny", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
                ReconstructionRule("identity.classifier_model_digest", H("c"), ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
                ReconstructionRule("identity.classifier_raw_sha256", H("d"), ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ),
        )
        with self.assertRaisesRegex(ValueError, "evidence support"):
            quant_title_for_reconstruction(malicious)


if __name__ == "__main__":
    unittest.main()
