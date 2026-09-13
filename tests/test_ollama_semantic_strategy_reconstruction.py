from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from dusty.ollama_semantic_strategy_reconstruction import SemanticContractOllamaStrategyReconstructor
from dusty.ollama_strategy_reconstruction import OllamaReconstructionRequest, ReconstructionAvailability
from dusty.reconstruction_runtime_features import ATR_14_FRACTION, CLOSE_SMA_20_DISTANCE_FRAC
from dusty.strategy_seed_proposals import starter_strategy_proposals


DIGEST = "a" * 64


def _proposal():
    return next(row for row in starter_strategy_proposals() if row.proposal_id == "dusty:eurusd-momentum-pullback")


def _request() -> OllamaReconstructionRequest:
    return OllamaReconstructionRequest(
        "semantic-test",
        _proposal(),
        "qwen3:1.7b",
        DIGEST,
        ("EURUSD",),
        ("M15",),
        ("return_1", "rsi", CLOSE_SMA_20_DISTANCE_FRAC, ATR_14_FRACTION),
        ("LONDON",),
    )


def _response(value: float = 0.002) -> dict[str, object]:
    return {
        "direction": "long",
        "symbols": ["EURUSD"],
        "timeframe": "M15",
        "entry_groups": [{
            "mode": "all",
            "clauses": [
                {"feature": CLOSE_SMA_20_DISTANCE_FRAC, "op": "gt", "value": value},
                {"feature": "rsi", "op": "lt", "value": 40.0},
            ],
        }],
        "stop": {"kind": "pct", "value": 0.01},
        "target": {"kind": "pct", "value": 0.02},
        "trailing": {"kind": "off", "value": 0.0},
        "breakeven_rr": 0.0,
        "max_hold_steps": 4,
        "intended_horizon_minutes": 60,
        "cooldown_steps": 0,
        "session_filters": ["LONDON"],
        "event_exclusion_minutes": 0,
        "execution_sensitivity": "normal",
    }


class SemanticOllamaReconstructionTests(unittest.TestCase):
    def test_prompt_carries_units_and_valid_candidate_is_available(self) -> None:
        calls: list[tuple[str, str, object]] = []

        def transport(method: str, url: str, payload: object, timeout: float):
            calls.append((method, url, payload))
            if method == "GET":
                return {"models": [{"name": "qwen3:1.7b", "digest": DIGEST}]}
            return {"message": {"content": json.dumps(_response())}, "done": True}

        result = SemanticContractOllamaStrategyReconstructor(transport=transport).reconstruct(
            _request(), created_at=datetime(2026, 9, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(result.status, ReconstructionAvailability.AVAILABLE)
        self.assertIsNotNone(result.reconstruction)
        post_payload = calls[-1][2]
        self.assertIsInstance(post_payload, dict)
        user_message = post_payload["messages"][1]["content"]
        prompt = json.loads(user_message)
        contracts = {row["name"]: row for row in prompt["allowed"]["feature_contract"]}
        self.assertEqual(contracts[CLOSE_SMA_20_DISTANCE_FRAC]["unit"], "signed_price_distance_fraction")
        self.assertIn("lookback period", post_payload["messages"][0]["content"])

    def test_semantically_absurd_threshold_is_unavailable_after_parse(self) -> None:
        def transport(method: str, url: str, payload: object, timeout: float):
            if method == "GET":
                return {"models": [{"name": "qwen3:1.7b", "digest": DIGEST}]}
            return {"message": {"content": json.dumps(_response(200.0))}, "done": True}

        result = SemanticContractOllamaStrategyReconstructor(transport=transport).reconstruct(
            _request(), created_at=datetime(2026, 9, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(result.status, ReconstructionAvailability.UNAVAILABLE)
        self.assertIn("signed_price_distance_fraction domain", result.error)

    def test_model_digest_mismatch_fails_closed_without_chat_call(self) -> None:
        calls: list[str] = []

        def transport(method: str, url: str, payload: object, timeout: float):
            calls.append(method)
            return {"models": [{"name": "qwen3:1.7b", "digest": "b" * 64}]}

        result = SemanticContractOllamaStrategyReconstructor(transport=transport).reconstruct(
            _request(), created_at=datetime(2026, 9, 12, tzinfo=timezone.utc)
        )
        self.assertEqual(result.status, ReconstructionAvailability.UNAVAILABLE)
        self.assertEqual(result.error, "ollama_model_digest_mismatch")
        self.assertEqual(calls, ["GET"])


if __name__ == "__main__":
    unittest.main()
