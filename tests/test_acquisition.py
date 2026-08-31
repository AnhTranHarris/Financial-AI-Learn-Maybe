from __future__ import annotations

import unittest

from dusty.acquisition import (
    AcquisitionState,
    ExternalRule,
    ExternalStrategy,
    LineageRelation,
    StrategyAccess,
    assess_external_strategy,
    lineage_edge,
    translate_external_strategy,
)
from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp, StrategySpec


class StrategyAcquisitionTests(unittest.TestCase):
    def test_m25_hidden_or_unlicensed_code_is_quarantined(self):
        strategy = ExternalStrategy.of(
            source_id="tv-1",
            platform="tradingview",
            source_url="https://example.test/strategy",
            external_id="abc",
            title="Protected strategy",
            direction=TradeSide.LONG,
            access=StrategyAccess.PERFORMANCE_ONLY,
            code_text="secret",
        )
        assessment = assess_external_strategy(strategy)
        self.assertIs(assessment.state, AcquisitionState.QUARANTINED)
        self.assertIn("hidden_code_not_authorized", assessment.reasons)
        self.assertIn("code_license_unknown", assessment.reasons)

    def test_m25_description_without_rules_stays_discovered(self):
        strategy = ExternalStrategy.of(
            source_id="qp-1",
            platform="quantpedia",
            source_url="https://example.test/qp",
            external_id="qp-1",
            title="Research idea",
            direction=TradeSide.LONG,
        )
        self.assertIs(assess_external_strategy(strategy).state, AcquisitionState.DISCOVERED)

    def test_m26_translation_preserves_source_but_family_ignores_threshold_popularity(self):
        base = dict(
            source_id="qc-1",
            platform="quantconnect",
            source_url="https://example.test/qc",
            title="Trend family",
            direction=TradeSide.LONG,
            access=StrategyAccess.DESCRIPTION_ONLY,
        )
        left = ExternalStrategy.of(
            external_id="left",
            rules=(ExternalRule("fast_ma", RuleOp.GT, 20), ExternalRule("atr", RuleOp.GE, 1.2)),
            **base,
        )
        right = ExternalStrategy.of(
            external_id="right",
            rules=(ExternalRule("fast_ma", RuleOp.GT, 30), ExternalRule("atr", RuleOp.GE, 1.8)),
            **base,
        )
        translated_left = translate_external_strategy(left)
        translated_right = translate_external_strategy(right)
        self.assertEqual(translated_left.family_hash, translated_right.family_hash)
        self.assertNotEqual(translated_left.spec.strategy_hash, translated_right.spec.strategy_hash)
        self.assertEqual(translated_left.source_id, "qc-1")

    def test_m26_lineage_requires_real_change(self):
        parent = StrategySpec("p", TradeSide.LONG, (Clause("x", RuleOp.GT, 1),))
        child = StrategySpec("c", TradeSide.LONG, (Clause("x", RuleOp.GT, 2),))
        edge = lineage_edge(parent, child, relation=LineageRelation.MUTATION, source_id="dusty")
        self.assertEqual(edge.parent_hash, parent.strategy_hash)
        with self.assertRaises(ValueError):
            lineage_edge(parent, parent, relation=LineageRelation.MUTATION, source_id="dusty")


if __name__ == "__main__":
    unittest.main()
