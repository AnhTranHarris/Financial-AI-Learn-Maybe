from __future__ import annotations

import json
from hashlib import sha256
import unittest

from dusty.ollama_quant_reviewer import (
    HARDWARE_RESPONSE_SCHEMA,
    HARDWARE_REVIEW_NUM_CTX,
    HARDWARE_REVIEW_NUM_PREDICT,
    REVIEW_NUM_PREDICT,
    OllamaQuantReviewer,
    QuantReviewerAvailability,
)
from dusty.quant_reviewer import QuantReviewRequest


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _hardware_request(model_digest: str) -> QuantReviewRequest:
    return QuantReviewRequest(
        "m1541-hardware-EURUSD-M15",
        "qwen3:1.7b",
        model_digest,
        (_digest("chronos"), _digest("kronos"), _digest("timesfm")),
        (),
        (_digest("board"),),
        json.dumps(
            {
                "purpose": "workstation_integration_only",
                "forecast_skill_claimed": False,
                "providers": ["chronos2", "kronos-small", "timesfm-2.5"],
                "authority": "research_only",
            },
            separators=(",", ":"),
        ),
        "Review only whether the forecast evidence can continue in research.",
    )


class M1543QwenHardwareBudgetTests(unittest.TestCase):
    def test_hardware_review_uses_compact_schema_context_and_generation_budget(self):
        model_digest = _digest("qwen3:1.7b")
        calls: list[tuple[str, str, dict[str, object] | None, float]] = []

        def transport(method, url, payload, timeout):
            calls.append((method, url, payload, timeout))
            if url.endswith("/api/tags"):
                return {
                    "models": [
                        {
                            "name": "qwen3:1.7b",
                            "model": "qwen3:1.7b",
                            "digest": model_digest,
                        }
                    ]
                }
            return {
                "done_reason": "stop",
                "message": {
                    "content": json.dumps(
                        {
                            "state": "wait",
                            "rationale_codes": ["hardware_integration_only"],
                            "cited_fingerprints": [_digest("chronos")],
                            "proposed_research": [],
                        },
                        separators=(",", ":"),
                    )
                },
            }

        result = OllamaQuantReviewer(transport=transport).review(_hardware_request(model_digest))

        self.assertEqual(result.status, QuantReviewerAvailability.AVAILABLE)
        self.assertEqual(len(calls), 2)
        method, url, payload, timeout = calls[1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/api/chat"))
        self.assertEqual(timeout, 180.0)
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertNotIn("tools", payload)
        self.assertEqual(payload["format"], HARDWARE_RESPONSE_SCHEMA)
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertEqual(payload["options"]["num_ctx"], HARDWARE_REVIEW_NUM_CTX)
        self.assertEqual(payload["options"]["num_predict"], HARDWARE_REVIEW_NUM_PREDICT)
        self.assertEqual(HARDWARE_REVIEW_NUM_CTX, 2048)
        self.assertLess(HARDWARE_REVIEW_NUM_PREDICT, REVIEW_NUM_PREDICT)
        self.assertLessEqual(HARDWARE_RESPONSE_SCHEMA["properties"]["rationale_codes"]["maxItems"], 3)
        self.assertLessEqual(HARDWARE_RESPONSE_SCHEMA["properties"]["cited_fingerprints"]["maxItems"], 4)
        self.assertLessEqual(HARDWARE_RESPONSE_SCHEMA["properties"]["proposed_research"]["maxItems"], 2)
        user_prompt = payload["messages"][1]["content"]
        self.assertIn("hardware_certification_research_reviewer", user_prompt)
        self.assertIn("research_only", user_prompt)
        self.assertNotIn("place a trade", user_prompt)

    def test_hardware_generation_cap_truncation_fails_closed(self):
        model_digest = _digest("qwen3:1.7b")

        def transport(method, url, payload, timeout):
            if url.endswith("/api/tags"):
                return {
                    "models": [
                        {
                            "name": "qwen3:1.7b",
                            "model": "qwen3:1.7b",
                            "digest": model_digest,
                        }
                    ]
                }
            return {
                "done_reason": "length",
                "message": {
                    "content": json.dumps(
                        {
                            "state": "wait",
                            "rationale_codes": ["truncated"],
                            "cited_fingerprints": [],
                            "proposed_research": [],
                        }
                    )
                },
            }

        result = OllamaQuantReviewer(transport=transport).review(_hardware_request(model_digest))

        self.assertFalse(result.available)
        self.assertEqual(result.error, "ollama_quant_review_truncated")

    def test_non_hardware_reviewer_keeps_m153_richer_budget(self):
        model_digest = _digest("qwen3:1.7b")
        payloads: list[dict[str, object]] = []

        def transport(method, url, payload, timeout):
            if url.endswith("/api/tags"):
                return {
                    "models": [
                        {
                            "name": "qwen3:1.7b",
                            "model": "qwen3:1.7b",
                            "digest": model_digest,
                        }
                    ]
                }
            assert payload is not None
            payloads.append(payload)
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "state": "wait",
                            "rationale_codes": ["research_review"],
                            "cited_fingerprints": [],
                            "proposed_research": [],
                        }
                    )
                }
            }

        request = QuantReviewRequest(
            "m153-normal-review",
            "qwen3:1.7b",
            model_digest,
            (_digest("forecast"),),
            (),
            (_digest("evidence"),),
            "normal research scorecard",
            "Review for continued research.",
        )
        result = OllamaQuantReviewer(transport=transport).review(request)

        self.assertTrue(result.available)
        self.assertEqual(payloads[0]["options"]["num_predict"], REVIEW_NUM_PREDICT)
        self.assertNotIn("num_ctx", payloads[0]["options"])
        self.assertNotEqual(payloads[0]["format"], HARDWARE_RESPONSE_SCHEMA)


if __name__ == "__main__":
    unittest.main()
