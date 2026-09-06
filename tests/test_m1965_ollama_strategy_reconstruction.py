from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import unittest

from dusty.ollama_strategy_reconstruction import (
    OllamaReconstructionRequest,
    OllamaStrategyReconstructor,
    ReconstructionAvailability,
)
from dusty.source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal
from dusty.trading_skills import ReconstructionRuleBasis


NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64
MODEL = "qwen3:1.7b"
MODEL_DIGEST = H("d")


def proposal() -> StrategyProposal:
    return StrategyProposal(
        "external:ollama",
        SourceSnapshot(
            "myfxbook",
            "https://www.myfxbook.com/strategies/ollama-test/1",
            NOW - timedelta(days=1),
            H("a"),
            SourceAccess.MANUAL_REVIEW,
            False,
        ),
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.PARTIAL,
        "Trend theory",
        symbols=("EURUSD",),
        timeframes=("M15",),
        components=("rsi", "trend"),
        declared_rules=(("concept", "trend-following"),),
        unresolved=("entry_logic", "exit_logic", "risk_logic"),
        claimed_performance=(("return", "marketing claim"),),
        tags=("source:myfxbook", "research_only"),
    )


def request(*, digest: str = MODEL_DIGEST) -> OllamaReconstructionRequest:
    return OllamaReconstructionRequest(
        "m1965-ollama-test",
        proposal(),
        MODEL,
        digest,
        ("EURUSD", "GBPUSD"),
        ("M15", "H1"),
        ("rsi", "return_1", "atr"),
        ("LONDON", "NEW_YORK"),
    )


def valid_response() -> dict[str, object]:
    return {
        "direction": "long",
        "symbols": ["EURUSD"],
        "timeframe": "M15",
        "entry_groups": [
            {
                "mode": "all",
                "clauses": [
                    {"feature": "rsi", "op": "ge", "value": 55.0},
                    {"feature": "return_1", "op": "gt", "value": 0.0},
                ],
            }
        ],
        "stop": {"kind": "atr", "value": 2.0},
        "target": {"kind": "rr", "value": 2.0},
        "trailing": {"kind": "off", "value": 0.0},
        "breakeven_rr": 0.0,
        "max_hold_steps": 16,
        "intended_horizon_minutes": 240,
        "cooldown_steps": 4,
        "session_filters": ["LONDON"],
        "event_exclusion_minutes": 0,
        "execution_sensitivity": "normal",
    }


class FakeTransport:
    def __init__(self, response: dict[str, object], *, installed_digest: str = MODEL_DIGEST) -> None:
        self.response = response
        self.installed_digest = installed_digest
        self.calls: list[tuple[str, str, dict[str, object] | None, float]] = []

    def __call__(self, method: str, url: str, payload: dict[str, object] | None, timeout: float) -> dict[str, object]:
        self.calls.append((method, url, payload, timeout))
        if method == "GET":
            return {"models": [{"name": MODEL, "model": MODEL, "digest": self.installed_digest}]}
        return {"message": {"content": json.dumps(self.response, separators=(",", ":"), allow_nan=True)}, "done_reason": "stop"}


class M1965OllamaReconstructionTests(unittest.TestCase):
    def test_valid_local_model_draft_becomes_hypothesis_not_source_claim(self) -> None:
        transport = FakeTransport(valid_response())
        adapter = OllamaStrategyReconstructor(transport=transport)
        result = adapter.reconstruct(request(), created_at=NOW)
        self.assertEqual(result.status, ReconstructionAvailability.AVAILABLE)
        self.assertIsNotNone(result.reconstruction)
        row = result.reconstruction
        assert row is not None
        self.assertEqual(row.unresolved_source_rules, ("entry_logic", "exit_logic", "risk_logic"))
        source_rules = [rule for rule in row.rules if rule.basis is ReconstructionRuleBasis.SOURCE_DECLARED]
        hypothesis_rules = [rule for rule in row.rules if rule.basis is ReconstructionRuleBasis.RESEARCH_HYPOTHESIS]
        self.assertEqual(tuple((rule.name, rule.value) for rule in source_rules), (("concept", "trend-following"),))
        self.assertGreater(len(hypothesis_rules), 0)
        self.assertFalse(row.source_claim_complete)
        self.assertEqual(row.candidate_spec.strategy_id.split("-")[0:2], ["ollama", "recon"])
        self.assertFalse(adapter.broker_write_authority)
        self.assertFalse(adapter.promotion_authority)

    def test_prompt_excludes_marketing_performance_claims(self) -> None:
        transport = FakeTransport(valid_response())
        result = OllamaStrategyReconstructor(transport=transport).reconstruct(request(), created_at=NOW)
        self.assertTrue(result.available)
        post = next(call for call in transport.calls if call[0] == "POST")[2]
        assert post is not None
        rendered = json.dumps(post, sort_keys=True)
        self.assertNotIn("marketing claim", rendered)
        self.assertNotIn("claimed_performance", rendered)

    def test_wrong_installed_model_digest_degrades_to_unavailable(self) -> None:
        transport = FakeTransport(valid_response(), installed_digest=H("e"))
        result = OllamaStrategyReconstructor(transport=transport).reconstruct(request(), created_at=NOW)
        self.assertFalse(result.available)
        self.assertIn("model_digest_mismatch", result.error)
        self.assertFalse(any(call[0] == "POST" for call in transport.calls))

    def test_schema_ignored_and_authority_field_added_fails_closed(self) -> None:
        response = valid_response()
        response["live_write_authority"] = True
        result = OllamaStrategyReconstructor(transport=FakeTransport(response)).reconstruct(request(), created_at=NOW)
        self.assertFalse(result.available)
        self.assertIn("schema mismatch", result.error)

    def test_string_coercion_is_not_accepted_for_integer(self) -> None:
        response = valid_response()
        response["max_hold_steps"] = "16"
        result = OllamaStrategyReconstructor(transport=FakeTransport(response)).reconstruct(request(), created_at=NOW)
        self.assertFalse(result.available)
        self.assertIn("max_hold_steps", result.error)

    def test_nonfinite_clause_value_is_rejected_after_json_parse(self) -> None:
        response = valid_response()
        response["entry_groups"][0]["clauses"][0]["value"] = float("nan")  # type: ignore[index]
        result = OllamaStrategyReconstructor(transport=FakeTransport(response)).reconstruct(request(), created_at=NOW)
        self.assertFalse(result.available)
        self.assertIn("finite", result.error)

    def test_unapproved_feature_is_rejected_even_if_transport_ignores_schema(self) -> None:
        response = valid_response()
        response["entry_groups"][0]["clauses"][0]["feature"] = "future_return"  # type: ignore[index]
        result = OllamaStrategyReconstructor(transport=FakeTransport(response)).reconstruct(request(), created_at=NOW)
        self.assertFalse(result.available)
        self.assertIn("unapproved feature", result.error)

    def test_unapproved_symbol_and_session_are_rejected(self) -> None:
        for field, value in (("symbols", ["XAUUSD"]), ("session_filters", ["ASIA"])):
            response = valid_response()
            response[field] = value
            result = OllamaStrategyReconstructor(transport=FakeTransport(response)).reconstruct(request(), created_at=NOW)
            self.assertFalse(result.available, field)
            self.assertIn("unapproved", result.error, field)

    def test_truncation_returns_unavailable_without_partial_candidate(self) -> None:
        class Truncated(FakeTransport):
            def __call__(self, method, url, payload, timeout):
                if method == "GET":
                    return super().__call__(method, url, payload, timeout)
                return {"message": {"content": "{}"}, "done_reason": "length"}

        result = OllamaStrategyReconstructor(transport=Truncated(valid_response())).reconstruct(request(), created_at=NOW)
        self.assertFalse(result.available)
        self.assertIsNone(result.reconstruction)
        self.assertIn("truncated", result.error)

    def test_endpoint_is_localhost_only(self) -> None:
        with self.assertRaisesRegex(ValueError, "localhost"):
            OllamaStrategyReconstructor(base_url="https://example.com")

    def test_sub_m5_reconstruction_universe_is_rejected_before_model_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "sub-M5"):
            OllamaReconstructionRequest(
                "bad-timeframe",
                proposal(),
                MODEL,
                MODEL_DIGEST,
                ("EURUSD",),
                ("M1",),
                ("rsi",),
            )


if __name__ == "__main__":
    unittest.main()
