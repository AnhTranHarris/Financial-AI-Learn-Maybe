from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import unittest

from dusty.a1_expert_agent import (
    A1ExpertAgent,
    A1ExpertAvailability,
    A1ExpertRequest,
    A1ExpertState,
    A1TestFamily,
    A1_RESPONSE_SCHEMA,
    build_a1_prompt_payload,
    parse_a1_expert_response,
)
from dusty.autonomous_research_campaign import CampaignCheckpoint, CampaignStatus
from dusty.research_expert_runtime import StructuredResearchResponse, validate_response_identity
from dusty.research_loop_governor import LoopState


UTC = timezone.utc
NOW = datetime(2026, 9, 5, 22, 0, tzinfo=UTC)


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


class M190A1ExpertAgentTests(unittest.TestCase):
    def checkpoint(
        self,
        *,
        school_index: int = 0,
        status: CampaignStatus = CampaignStatus.ACTIVE,
        reason: str = "campaign_step_recorded",
    ) -> CampaignCheckpoint:
        return CampaignCheckpoint(
            "campaign-1",
            fp("campaign-manifest"),
            fp("research-loop"),
            school_index,
            LoopState.TESTING,
            1,
            2,
            (fp("completed-experiment"),),
            (fp("prior-result"),),
            10.0,
            fp("last-action"),
            0,
            status,
            reason,
            NOW,
        )

    def request(self, **changes: object) -> A1ExpertRequest:
        values = dict(
            request_id="m190-case-1",
            model_tag="qwen3:1.7b",
            model_digest=fp("model"),
            campaign_checkpoint=self.checkpoint(),
            context_fingerprint=fp("context"),
            strategy_fingerprints=(fp("strategy"),),
            forecast_fingerprints=(fp("forecast"),),
            diagnosis_fingerprints=(fp("diagnosis"),),
            evidence_fingerprints=(fp("evidence"),),
            evidence_summary="Observed A1 research evidence remains mixed and requires falsifiable follow-up.",
            question="What bounded A1 hypotheses should be tested next?",
        )
        values.update(changes)
        return A1ExpertRequest(**values)

    def hypothesis(self, key: str = "H1", **changes: object) -> dict[str, object]:
        values: dict[str, object] = {
            "hypothesis_key": key,
            "statement": "The observed signal may be concentrated in one market regime.",
            "falsification": "Reject if purged A1 slices show no stable regime separation.",
            "test_family": A1TestFamily.REGIME_SLICE.value,
            "test_plan": "Compare the frozen strategy across predeclared regime slices using the same evidence policy.",
            "cited_fingerprints": [fp("strategy"), fp("diagnosis")],
        }
        values.update(changes)
        return values

    def response_text(
        self,
        *,
        state: str = A1ExpertState.HYPOTHESES.value,
        rationale: list[object] | None = None,
        hypotheses: list[dict[str, object]] | None = None,
        extra: dict[str, object] | None = None,
    ) -> str:
        payload: dict[str, object] = {
            "state": state,
            "rationale_codes": ["regime_dependence_requires_test"] if rationale is None else rationale,
            "hypotheses": [self.hypothesis()] if hypotheses is None else hypotheses,
        }
        if extra:
            payload.update(extra)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def test_request_requires_active_a1_campaign_and_bound_evidence(self) -> None:
        request = self.request()
        self.assertEqual(request.campaign_checkpoint.school_index, 0)
        self.assertEqual(len(request.allowed_citations), 4)
        with self.assertRaisesRegex(ValueError, "active M189"):
            self.request(campaign_checkpoint=self.checkpoint(status=CampaignStatus.PAUSED))
        with self.assertRaisesRegex(ValueError, "outside A1"):
            self.request(campaign_checkpoint=self.checkpoint(school_index=1))
        with self.assertRaisesRegex(ValueError, "requires supplied research evidence"):
            self.request(
                strategy_fingerprints=(),
                forecast_fingerprints=(),
                diagnosis_fingerprints=(),
                evidence_fingerprints=(),
            )

    def test_same_fingerprint_cannot_masquerade_as_two_evidence_classes(self) -> None:
        shared = fp("shared")
        with self.assertRaisesRegex(ValueError, "masquerade"):
            self.request(
                strategy_fingerprints=(shared,),
                forecast_fingerprints=(shared,),
                diagnosis_fingerprints=(),
                evidence_fingerprints=(),
            )

    def test_request_fingerprint_binds_campaign_context_and_evidence(self) -> None:
        base = self.request()
        changed_context = self.request(context_fingerprint=fp("different-context"))
        changed_evidence = self.request(evidence_fingerprints=(fp("different-evidence"),))
        changed_checkpoint = self.request(campaign_checkpoint=replace(self.checkpoint(), step_index=3))
        self.assertNotEqual(base.fingerprint, changed_context.fingerprint)
        self.assertNotEqual(base.fingerprint, changed_evidence.fingerprint)
        self.assertNotEqual(base.fingerprint, changed_checkpoint.fingerprint)

    def test_prompt_is_a1_research_only_and_has_no_scheduler_or_mutation_fields(self) -> None:
        prompt = build_a1_prompt_payload(self.request())
        self.assertEqual(prompt["school"], "A1")
        constraints = set(prompt["constraints"])
        self.assertIn("research_only", constraints)
        self.assertIn("no_scheduler_score_priority_or_resource_decision", constraints)
        self.assertIn("no_executable_strategy_mutation_fields_or_parameter_updates", constraints)
        self.assertNotIn("priority", prompt)
        self.assertNotIn("risk_fraction", prompt)
        self.assertNotIn("trade_action", prompt)
        self.assertEqual(A1_RESPONSE_SCHEMA["additionalProperties"], False)
        properties = A1_RESPONSE_SCHEMA["properties"]
        self.assertNotIn("priority", properties)
        self.assertNotIn("expected_information_gain", properties)
        self.assertNotIn("parameter_updates", properties)

    def test_valid_hypothesis_response_is_evidence_not_edge_proof(self) -> None:
        request = self.request()
        first = parse_a1_expert_response(request, self.response_text())
        second = parse_a1_expert_response(request, self.response_text())
        self.assertEqual(first.state, A1ExpertState.HYPOTHESES)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(len(first.hypotheses), 1)
        hypothesis = first.hypotheses[0]
        self.assertFalse(hypothesis.causal_claimed)
        self.assertFalse(hypothesis.edge_proven)
        self.assertFalse(hypothesis.mutation_authority)
        self.assertFalse(hypothesis.scheduler_priority_authority)
        self.assertFalse(first.broker_write_authority)
        self.assertFalse(first.risk_override_authority)
        self.assertFalse(first.guardian_override_authority)
        self.assertFalse(first.promotion_authority)
        self.assertFalse(first.mutation_authority)
        self.assertFalse(first.scheduler_priority_authority)

    def test_insufficient_evidence_is_valid_only_without_hypotheses(self) -> None:
        request = self.request()
        text = self.response_text(
            state=A1ExpertState.INSUFFICIENT_EVIDENCE.value,
            rationale=["evidence_not_specific_enough"],
            hypotheses=[],
        )
        result = parse_a1_expert_response(request, text)
        self.assertEqual(result.state, A1ExpertState.INSUFFICIENT_EVIDENCE)
        self.assertEqual(result.hypotheses, ())
        with self.assertRaisesRegex(ValueError, "insufficient A1 evidence"):
            parse_a1_expert_response(
                request,
                self.response_text(state=A1ExpertState.INSUFFICIENT_EVIDENCE.value),
            )

    def test_hypotheses_state_requires_at_least_one_hypothesis(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires at least one"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=[]))

    def test_hallucinated_citations_fail_closed(self) -> None:
        bad = self.hypothesis(cited_fingerprints=[fp("not-supplied")])
        with self.assertRaisesRegex(ValueError, "not supplied"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=[bad]))

    def test_non_a1_test_family_fails_closed(self) -> None:
        bad = self.hypothesis(test_family="profitability_optimization")
        with self.assertRaisesRegex(ValueError, "outside A1 scope"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=[bad]))

    def test_operational_or_scheduler_fields_are_schema_violations(self) -> None:
        with self.assertRaisesRegex(ValueError, "response schema mismatch"):
            parse_a1_expert_response(
                self.request(),
                self.response_text(extra={"trade_action": "buy"}),
            )
        bad = self.hypothesis()
        bad["priority"] = 100
        with self.assertRaisesRegex(ValueError, "hypothesis schema mismatch"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=[bad]))
        bad = self.hypothesis()
        bad["new_value"] = 30
        with self.assertRaisesRegex(ValueError, "hypothesis schema mismatch"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=[bad]))

    def test_raw_json_numbers_cannot_be_coerced_into_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "state must be a string"):
            parse_a1_expert_response(
                self.request(),
                json.dumps({"state": 1, "rationale_codes": ["x"], "hypotheses": []}),
            )
        with self.assertRaisesRegex(ValueError, "string array"):
            parse_a1_expert_response(
                self.request(),
                self.response_text(rationale=[1]),
            )
        bad = self.hypothesis(statement=123)
        with self.assertRaisesRegex(ValueError, "text fields must be strings"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=[bad]))

    def test_hypothesis_keys_and_research_shapes_must_be_distinct(self) -> None:
        duplicate_key = [self.hypothesis("H1"), self.hypothesis("H1", statement="Different idea")]
        with self.assertRaisesRegex(ValueError, "keys must be unique"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=duplicate_key))
        duplicate_shape = [self.hypothesis("H1"), self.hypothesis("H2")]
        with self.assertRaisesRegex(ValueError, "structurally distinct"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=duplicate_shape))
        invalid_key = self.hypothesis("bad key with spaces")
        with self.assertRaisesRegex(ValueError, "unsupported characters"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=[invalid_key]))

    def test_hypothesis_count_and_citations_are_bounded(self) -> None:
        hypotheses = [
            self.hypothesis(
                f"H{index}",
                statement=f"Distinct statement {index}",
                falsification=f"Distinct falsification {index}",
                test_plan=f"Distinct test {index}",
            )
            for index in range(4)
        ]
        with self.assertRaisesRegex(ValueError, "bounded array"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=hypotheses))
        repeated_citation = self.hypothesis(cited_fingerprints=[fp("strategy"), fp("strategy")])
        with self.assertRaisesRegex(ValueError, "values must be unique"):
            parse_a1_expert_response(self.request(), self.response_text(hypotheses=[repeated_citation]))

    def test_generator_model_identity_must_match_exact_request(self) -> None:
        request = self.request()
        content = self.response_text()
        wrong_tag = FakeGenerator(StructuredResearchResponse("other-model", request.model_digest, content))
        result = A1ExpertAgent(wrong_tag).research(request)
        self.assertEqual(result.status, A1ExpertAvailability.UNAVAILABLE)
        self.assertIn("model tag drift", result.error)

        wrong_digest = FakeGenerator(StructuredResearchResponse(request.model_tag, fp("other-model"), content))
        result = A1ExpertAgent(wrong_digest).research(request)
        self.assertEqual(result.status, A1ExpertAvailability.UNAVAILABLE)
        self.assertIn("model digest drift", result.error)

    def test_generator_transport_or_schema_failure_degrades_to_unavailable(self) -> None:
        request = self.request()
        transport_failure = A1ExpertAgent(FakeGenerator(error=RuntimeError("provider down"))).research(request)
        self.assertFalse(transport_failure.available)
        self.assertIn("provider down", transport_failure.error)

        invalid_json = FakeGenerator(StructuredResearchResponse(request.model_tag, request.model_digest, "not-json"))
        result = A1ExpertAgent(invalid_json).research(request)
        self.assertFalse(result.available)
        self.assertIn("a1_expert_unavailable", result.error)

    def test_available_agent_run_passes_only_structured_research_contract(self) -> None:
        request = self.request()
        generator = FakeGenerator(
            StructuredResearchResponse(request.model_tag, request.model_digest, self.response_text())
        )
        agent = A1ExpertAgent(generator)
        result = agent.research(request)
        self.assertTrue(result.available)
        self.assertIsNotNone(result.evidence)
        self.assertFalse(agent.broker_write_authorized)
        self.assertFalse(agent.mutation_authorized)
        self.assertFalse(agent.scheduler_priority_authorized)
        self.assertFalse(result.broker_write_authority)
        self.assertEqual(len(generator.calls), 1)
        call = generator.calls[0]
        self.assertEqual(call["model_tag"], request.model_tag)
        self.assertEqual(call["model_digest"], request.model_digest)
        self.assertEqual(call["response_schema"], A1_RESPONSE_SCHEMA)
        self.assertIn("no trading", str(call["system_message"]).lower())
        prompt = call["prompt_payload"]
        self.assertEqual(prompt["request_fingerprint"], request.fingerprint)
        self.assertEqual(prompt["campaign_checkpoint_fingerprint"], request.campaign_checkpoint.fingerprint)

    def test_structured_runtime_response_is_research_only_and_identity_checked(self) -> None:
        response = StructuredResearchResponse("qwen3:1.7b", fp("model"), "{}")
        self.assertFalse(response.broker_write_authority)
        self.assertFalse(response.tool_authority)
        validate_response_identity(response, model_tag="qwen3:1.7b", model_digest=fp("model"))
        with self.assertRaisesRegex(ValueError, "model tag drift"):
            validate_response_identity(response, model_tag="qwen3:4b", model_digest=fp("model"))
        with self.assertRaisesRegex(ValueError, "model digest drift"):
            validate_response_identity(response, model_tag="qwen3:1.7b", model_digest=fp("different"))


if __name__ == "__main__":
    unittest.main()
