from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import unittest

from dusty.a1_expert_agent import A1ExpertEvidence, A1ExpertState, A1Hypothesis, A1TestFamily
from dusty.a2_expert_agent import (
    A2ExpertAgent,
    A2ExpertAvailability,
    A2ExpertRequest,
    A2ExpertState,
    A2TestFamily,
    A2_RESPONSE_SCHEMA,
    build_a2_prompt_payload,
    parse_a2_expert_response,
)
from dusty.autonomous_research_campaign import CampaignCheckpoint, CampaignStatus
from dusty.research_expert_runtime import StructuredResearchResponse
from dusty.research_loop_governor import LoopState


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 23, 0, tzinfo=UTC)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class FakeGenerator:
    def __init__(self, response: StructuredResearchResponse | None = None, error: BaseException | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, object]] = []

    def generate(self, **kwargs: object) -> StructuredResearchResponse:
        self.calls.append(dict(kwargs))
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


class M191A2ExpertAgentTests(unittest.TestCase):
    def checkpoint(self, *, school: int = 1, status: CampaignStatus = CampaignStatus.ACTIVE) -> CampaignCheckpoint:
        return CampaignCheckpoint(
            "campaign-1", fp("manifest"), fp("loop"), school, LoopState.TESTING, 2, 4,
            (fp("experiment"),), (fp("a1-stage-result"),), 20.0, fp("action"), 0,
            status, "campaign_step_recorded", NOW,
        )

    def a1(self, *, state: A1ExpertState = A1ExpertState.HYPOTHESES) -> A1ExpertEvidence:
        hypotheses = () if state is A1ExpertState.INSUFFICIENT_EVIDENCE else (
            A1Hypothesis(
                "H1",
                "The apparent edge may be regime dependent.",
                "Reject if purged regime slices do not preserve positive OOS behavior.",
                A1TestFamily.REGIME_SLICE,
                "Run a predeclared regime slice without altering the frozen strategy.",
                (fp("strategy"),),
            ),
        )
        return A1ExpertEvidence(
            fp("a1-request"), "qwen3:1.7b", fp("model"), state,
            ("a1_hypothesis_generated",), hypotheses, fp("a1-raw"),
        )

    def request(self, **changes: object) -> A2ExpertRequest:
        values = dict(
            request_id="m191-case-1",
            model_tag="qwen3:1.7b",
            model_digest=fp("model"),
            campaign_checkpoint=self.checkpoint(),
            context_fingerprint=fp("context"),
            a1_evidence=(self.a1(),),
            strategy_fingerprints=(fp("strategy-a2"),),
            walk_forward_fingerprints=(fp("walk-forward"),),
            purged_validation_fingerprints=(fp("purged"),),
            parameter_stability_fingerprints=(fp("parameter"),),
            regime_torture_fingerprints=(fp("regime"),),
            cost_torture_fingerprints=(fp("cost"),),
            forward_decay_fingerprints=(fp("decay"),),
            tail_risk_fingerprints=(fp("tail"),),
            dependency_fingerprints=(fp("dependency"),),
            robustness_gate_fingerprints=(fp("m174"),),
            evidence_summary="Supplied A2 evidence contains walk-forward, stability, cost, decay, tail and dependency observations.",
            question="Which bounded robustness concerns still require discriminating tests?",
        )
        values.update(changes)
        return A2ExpertRequest(**values)

    def concern(self, key: str = "C1", **changes: object) -> dict[str, object]:
        values: dict[str, object] = {
            "concern_key": key,
            "concern": "The observed edge may be fragile outside the selected parameter neighborhood.",
            "failure_condition": "Treat the concern as supported if neighboring frozen parameter points materially degrade under the declared policy.",
            "test_family": A2TestFamily.PARAMETER_STABILITY.value,
            "test_plan": "Compare the supplied M168 neighborhood evidence against the predeclared robustness policy without changing thresholds.",
            "cited_fingerprints": [fp("parameter"), self.a1().hypotheses[0].fingerprint],
        }
        values.update(changes)
        return values

    def response_text(
        self,
        *,
        state: str = A2ExpertState.ROBUSTNESS_TESTS.value,
        rationale: list[object] | None = None,
        concerns: list[dict[str, object]] | None = None,
        extra: dict[str, object] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "state": state,
            "rationale_codes": ["parameter_robustness_requires_review"] if rationale is None else rationale,
            "concerns": [self.concern()] if concerns is None else concerns,
        }
        if extra:
            payload.update(extra)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def test_request_requires_active_a2_campaign_upstream_a1_and_robustness_evidence(self) -> None:
        request = self.request()
        self.assertEqual(request.campaign_checkpoint.school_index, 1)
        self.assertIn(self.a1().hypotheses[0].fingerprint, request.allowed_citations)
        with self.assertRaisesRegex(ValueError, "active M189"):
            self.request(campaign_checkpoint=self.checkpoint(status=CampaignStatus.PAUSED))
        with self.assertRaisesRegex(ValueError, "outside A2"):
            self.request(campaign_checkpoint=self.checkpoint(school=0))
        with self.assertRaisesRegex(ValueError, "upstream M190"):
            self.request(a1_evidence=())
        with self.assertRaisesRegex(ValueError, "hypothesis-bearing"):
            self.request(a1_evidence=(self.a1(state=A1ExpertState.INSUFFICIENT_EVIDENCE),))
        with self.assertRaisesRegex(ValueError, "supplied robustness evidence"):
            self.request(
                walk_forward_fingerprints=(), purged_validation_fingerprints=(), parameter_stability_fingerprints=(),
                regime_torture_fingerprints=(), cost_torture_fingerprints=(), forward_decay_fingerprints=(),
                tail_risk_fingerprints=(), dependency_fingerprints=(), robustness_gate_fingerprints=(),
            )

    def test_strategy_identity_and_evidence_classes_cannot_be_omitted_or_masqueraded(self) -> None:
        with self.assertRaisesRegex(ValueError, "frozen strategy identity"):
            self.request(strategy_fingerprints=())
        shared = fp("shared")
        with self.assertRaisesRegex(ValueError, "masquerade"):
            self.request(walk_forward_fingerprints=(shared,), cost_torture_fingerprints=(shared,))

    def test_prompt_and_schema_cannot_certify_profitability_or_invent_thresholds(self) -> None:
        prompt = build_a2_prompt_payload(self.request())
        constraints = set(prompt["constraints"])
        self.assertIn("do_not_certify_profitability_or_A2_pass", constraints)
        self.assertIn("do_not_invent_numeric_policy_thresholds", constraints)
        self.assertIn("M174_and_M160_remain_decision_authorities", constraints)
        self.assertNotIn("passed", A2_RESPONSE_SCHEMA["properties"])
        self.assertNotIn("profitability_score", A2_RESPONSE_SCHEMA["properties"])
        concern_properties = A2_RESPONSE_SCHEMA["properties"]["concerns"]["items"]["properties"]
        for forbidden in ("threshold", "new_value", "priority", "risk_fraction", "trade_action", "passed"):
            self.assertNotIn(forbidden, concern_properties)

    def test_valid_concern_is_research_evidence_not_profitability_certification(self) -> None:
        first = parse_a2_expert_response(self.request(), self.response_text())
        second = parse_a2_expert_response(self.request(), self.response_text())
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.state, A2ExpertState.ROBUSTNESS_TESTS)
        concern = first.concerns[0]
        self.assertFalse(concern.profitability_certified)
        self.assertFalse(concern.mutation_authority)
        self.assertFalse(concern.scheduler_priority_authority)
        self.assertFalse(first.profitability_certified)
        self.assertFalse(first.broker_write_authority)
        self.assertFalse(first.risk_override_authority)
        self.assertFalse(first.guardian_override_authority)
        self.assertFalse(first.promotion_authority)
        self.assertFalse(first.mutation_authority)
        self.assertFalse(first.scheduler_priority_authority)

    def test_insufficient_evidence_and_test_state_are_mutually_consistent(self) -> None:
        result = parse_a2_expert_response(
            self.request(),
            self.response_text(state=A2ExpertState.INSUFFICIENT_EVIDENCE.value, concerns=[], rationale=["missing_required_slice"]),
        )
        self.assertEqual(result.state, A2ExpertState.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.concerns, ())
        with self.assertRaisesRegex(ValueError, "insufficient A2 evidence"):
            parse_a2_expert_response(self.request(), self.response_text(state=A2ExpertState.INSUFFICIENT_EVIDENCE.value))
        with self.assertRaisesRegex(ValueError, "requires at least one"):
            parse_a2_expert_response(self.request(), self.response_text(concerns=[]))

    def test_hallucinated_citations_and_non_a2_test_family_fail_closed(self) -> None:
        bad = self.concern(cited_fingerprints=[fp("hallucinated")])
        with self.assertRaisesRegex(ValueError, "not supplied"):
            parse_a2_expert_response(self.request(), self.response_text(concerns=[bad]))
        bad = self.concern(test_family="profitability_approval")
        with self.assertRaisesRegex(ValueError, "outside A2 scope"):
            parse_a2_expert_response(self.request(), self.response_text(concerns=[bad]))

    def test_extra_certification_threshold_or_mutation_fields_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "response schema mismatch"):
            parse_a2_expert_response(self.request(), self.response_text(extra={"passed": True}))
        for name, value in (("threshold", 0.7), ("priority", 100), ("new_value", 25), ("trade_action", "buy")):
            bad = self.concern()
            bad[name] = value
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "concern schema mismatch"):
                parse_a2_expert_response(self.request(), self.response_text(concerns=[bad]))

    def test_raw_json_types_are_not_coerced(self) -> None:
        with self.assertRaisesRegex(ValueError, "state must be a string"):
            parse_a2_expert_response(self.request(), json.dumps({"state": 2, "rationale_codes": ["x"], "concerns": []}))
        with self.assertRaisesRegex(ValueError, "string array"):
            parse_a2_expert_response(self.request(), self.response_text(rationale=[2]))
        bad = self.concern(concern=123)
        with self.assertRaisesRegex(ValueError, "text fields must be strings"):
            parse_a2_expert_response(self.request(), self.response_text(concerns=[bad]))

    def test_concerns_are_bounded_and_structurally_distinct(self) -> None:
        duplicate_key = [self.concern("C1"), self.concern("C1", concern="different")]
        with self.assertRaisesRegex(ValueError, "keys must be unique"):
            parse_a2_expert_response(self.request(), self.response_text(concerns=duplicate_key))
        duplicate_shape = [self.concern("C1"), self.concern("C2")]
        with self.assertRaisesRegex(ValueError, "structurally distinct"):
            parse_a2_expert_response(self.request(), self.response_text(concerns=duplicate_shape))
        too_many = [
            self.concern(
                f"C{i}", concern=f"Concern {i}", failure_condition=f"Failure {i}", test_plan=f"Test {i}",
            )
            for i in range(5)
        ]
        with self.assertRaisesRegex(ValueError, "bounded array"):
            parse_a2_expert_response(self.request(), self.response_text(concerns=too_many))

    def test_provider_model_identity_or_transport_failure_is_unavailable(self) -> None:
        request = self.request()
        content = self.response_text()
        wrong = A2ExpertAgent(FakeGenerator(StructuredResearchResponse("wrong-model", request.model_digest, content))).research(request)
        self.assertEqual(wrong.status, A2ExpertAvailability.UNAVAILABLE)
        self.assertIn("model tag drift", wrong.error)
        wrong = A2ExpertAgent(FakeGenerator(StructuredResearchResponse(request.model_tag, fp("other-model"), content))).research(request)
        self.assertEqual(wrong.status, A2ExpertAvailability.UNAVAILABLE)
        self.assertIn("model digest drift", wrong.error)
        failed = A2ExpertAgent(FakeGenerator(error=RuntimeError("provider unavailable"))).research(request)
        self.assertFalse(failed.available)
        self.assertIn("provider unavailable", failed.error)

    def test_available_agent_uses_only_structured_contract_and_has_no_operational_authority(self) -> None:
        request = self.request()
        generator = FakeGenerator(StructuredResearchResponse(request.model_tag, request.model_digest, self.response_text()))
        agent = A2ExpertAgent(generator)
        result = agent.research(request)
        self.assertTrue(result.available)
        self.assertFalse(agent.broker_write_authorized)
        self.assertFalse(agent.profitability_certification_authorized)
        self.assertFalse(agent.mutation_authorized)
        self.assertFalse(agent.scheduler_priority_authorized)
        self.assertFalse(result.broker_write_authority)
        self.assertEqual(len(generator.calls), 1)
        call = generator.calls[0]
        self.assertEqual(call["response_schema"], A2_RESPONSE_SCHEMA)
        self.assertEqual(call["prompt_payload"]["request_fingerprint"], request.fingerprint)
        self.assertIn("cannot certify profitability", str(call["system_message"]).lower())


if __name__ == "__main__":
    unittest.main()
