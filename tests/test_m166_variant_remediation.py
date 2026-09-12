from __future__ import annotations

from datetime import datetime, timezone
import unittest

from dusty.experience import TradeSide
from dusty.m166_research_identity import parameter_fingerprint
from dusty.m166_variant_remediation import derive_event_hypothesis_variant
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)

UTC = timezone.utc
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def parent(*, actor: ReconstructionActor = ReconstructionActor.OLLAMA, rules=(), unresolved=()) -> StrategyReconstruction:
    spec = StrategySpecV2(
        strategy_id="ollama-recon-parent",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((Clause("rsi", RuleOp.GE, 55.0),)),),
        exit_plan=ExitPlan("pct:0.01", "rr:2", max_hold_steps=4),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
        session_filters=("ASIA", "LONDON_NY_OVERLAP"),
        event_exclusion_minutes=15,
    )
    base_rules = (
        ReconstructionRule("hypothesis.direction", "long", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
    )
    return StrategyReconstruction(
        SHA_A,
        "source",
        "https://example.com/strategy",
        SHA_B,
        SHA_C,
        "Parent",
        ("EURUSD",),
        "M15",
        spec,
        tuple(base_rules) + tuple(rules),
        tuple(unresolved),
        actor,
        SHA_A,
        datetime(2026, 9, 1, tzinfo=UTC),
    )


def requirement(row: StrategyReconstruction) -> dict[str, object]:
    payload = {
        "protocol": "dusty-m166-event-requirement-inspector-v3",
        "strategy_fingerprint": row.candidate_spec.strategy_hash,
        "reconstruction_fingerprint": row.fingerprint,
        "event_exclusion_minutes": row.candidate_spec.event_exclusion_minutes,
        "event_exclusion_basis": "research_hypothesis",
        "event_related_rules": [],
        "source_declared_event_rules": [],
        "unresolved_event_rules": [],
        "requirement_fingerprint": "d" * 64,
    }
    return payload


class M166VariantRemediationTests(unittest.TestCase):
    def test_eventless_variant_is_new_immutable_identity_and_parent_is_unchanged(self) -> None:
        original = parent()
        parent_spec = original.candidate_spec
        parent_fp = original.fingerprint
        parameter_fp = parameter_fingerprint(parent_spec)

        result = derive_event_hypothesis_variant(
            original,
            parent_parameter_fingerprint=parameter_fp,
            requirement=requirement(original),
        )

        variant = result.reconstruction
        self.assertEqual(original.fingerprint, parent_fp)
        self.assertEqual(original.candidate_spec.event_exclusion_minutes, 15)
        self.assertEqual(variant.candidate_spec.event_exclusion_minutes, 0)
        self.assertNotEqual(variant.candidate_spec.strategy_id, original.candidate_spec.strategy_id)
        self.assertNotEqual(variant.candidate_spec.strategy_hash, original.candidate_spec.strategy_hash)
        self.assertNotEqual(variant.fingerprint, original.fingerprint)
        self.assertEqual(variant.symbols, original.symbols)
        self.assertEqual(variant.timeframe, original.timeframe)
        self.assertEqual(variant.actor, ReconstructionActor.DUSTY_RESEARCH)
        self.assertTrue(any(rule.name == "remediation.event_exclusion_minutes" for rule in variant.rules))
        self.assertTrue(result.payload["parent_preserved"])
        self.assertFalse(result.payload["authority"]["broker_write"])

    def test_derivation_is_deterministic(self) -> None:
        original = parent()
        kwargs = {
            "parent_parameter_fingerprint": parameter_fingerprint(original.candidate_spec),
            "requirement": requirement(original),
        }
        first = derive_event_hypothesis_variant(original, **kwargs)
        second = derive_event_hypothesis_variant(original, **kwargs)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.reconstruction.fingerprint, second.reconstruction.fingerprint)
        self.assertEqual(first.reconstruction.candidate_spec.strategy_hash, second.reconstruction.candidate_spec.strategy_hash)

    def test_source_declared_event_rule_prevents_automatic_remediation(self) -> None:
        original = parent(rules=(
            ReconstructionRule("news_filter", "avoid CPI", ReconstructionRuleBasis.SOURCE_DECLARED),
        ))
        req = requirement(original)
        req["event_related_rules"] = [{"name": "news_filter", "value": "avoid CPI", "basis": "source_declared"}]
        req["source_declared_event_rules"] = list(req["event_related_rules"])
        with self.assertRaises(PermissionError):
            derive_event_hypothesis_variant(
                original,
                parent_parameter_fingerprint=parameter_fingerprint(original.candidate_spec),
                requirement=req,
            )

    def test_unresolved_event_rule_prevents_automatic_remediation(self) -> None:
        original = parent(unresolved=("news impact tier unknown",))
        req = requirement(original)
        req["unresolved_event_rules"] = ["news impact tier unknown"]
        with self.assertRaises(PermissionError):
            derive_event_hypothesis_variant(
                original,
                parent_parameter_fingerprint=parameter_fingerprint(original.candidate_spec),
                requirement=req,
            )

    def test_non_ollama_parent_prevents_automatic_remediation(self) -> None:
        original = parent(actor=ReconstructionActor.DETERMINISTIC_TRANSLATOR)
        with self.assertRaises(PermissionError):
            derive_event_hypothesis_variant(
                original,
                parent_parameter_fingerprint=parameter_fingerprint(original.candidate_spec),
                requirement=requirement(original),
            )

    def test_identity_drift_fails_closed(self) -> None:
        original = parent()
        req = requirement(original)
        req["strategy_fingerprint"] = "e" * 64
        with self.assertRaisesRegex(ValueError, "strategy identity mismatch"):
            derive_event_hypothesis_variant(
                original,
                parent_parameter_fingerprint=parameter_fingerprint(original.candidate_spec),
                requirement=req,
            )

    def test_unreported_event_related_rule_prevents_automatic_remediation(self) -> None:
        original = parent(rules=(
            ReconstructionRule("hypothesis.news_window", "15", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ))
        with self.assertRaises(PermissionError):
            derive_event_hypothesis_variant(
                original,
                parent_parameter_fingerprint=parameter_fingerprint(original.candidate_spec),
                requirement=requirement(original),
            )


if __name__ == "__main__":
    unittest.main()
