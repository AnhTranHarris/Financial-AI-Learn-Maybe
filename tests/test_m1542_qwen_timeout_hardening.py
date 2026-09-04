from __future__ import annotations

import json
from hashlib import sha256
import unittest

from dusty.ollama_quant_reviewer import (
    REVIEW_KEEP_ALIVE,
    REVIEW_NUM_PREDICT,
    OllamaQuantReviewer,
    QuantReviewerAvailability,
)
from dusty.quant_reviewer import QuantReviewRequest


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M1542QwenTimeoutHardeningTests(unittest.TestCase):
    def test_qwen_review_disables_thinking_and_bounds_generation(self):
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
            assert payload is not None
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "state": "research_required",
                            "rationale_codes": ["bounded_review"],
                            "cited_fingerprints": [_digest("forecast")],
                            "proposed_research": ["continue bounded research"],
                        }
                    )
                }
            }

        request = QuantReviewRequest(
            "m1542-review",
            "qwen3:1.7b",
            model_digest,
            (_digest("forecast"),),
            (),
            (_digest("scorecard"),),
            "deterministic scorecard",
            "Review only for continued research.",
        )
        result = OllamaQuantReviewer(transport=transport).review(request)

        self.assertEqual(result.status, QuantReviewerAvailability.AVAILABLE)
        self.assertEqual(len(calls), 2)
        method, url, payload, timeout = calls[1]
        self.assertEqual(method, "POST")
        self.assertTrue(url.endswith("/api/chat"))
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["keep_alive"], REVIEW_KEEP_ALIVE)
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertEqual(payload["options"]["num_predict"], REVIEW_NUM_PREDICT)
        self.assertLessEqual(REVIEW_NUM_PREDICT, 512)
        self.assertEqual(timeout, 180.0)
        self.assertNotIn("tools", payload)

    def test_qwen_timeout_still_fails_closed(self):
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
            raise TimeoutError("synthetic timeout")

        request = QuantReviewRequest(
            "m1542-timeout",
            "qwen3:1.7b",
            model_digest,
            (_digest("forecast"),),
            (),
            (_digest("scorecard"),),
            "deterministic scorecard",
            "Review only for continued research.",
        )
        result = OllamaQuantReviewer(transport=transport).review(request)

        self.assertFalse(result.available)
        self.assertIn("TimeoutError", result.error)


if __name__ == "__main__":
    unittest.main()
