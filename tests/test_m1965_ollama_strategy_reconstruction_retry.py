from __future__ import annotations

import unittest

from dusty.ollama_strategy_reconstruction import NUM_CTX, NUM_PREDICT
from dusty.ollama_strategy_reconstruction_retry import (
    TRUNCATION_RETRY_NUM_PREDICT,
    TruncationRetryTransport,
)


class ScriptedTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, url, payload, timeout):
        self.calls.append((method, url, payload, timeout))
        if not self.responses:
            raise AssertionError("unexpected transport call")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def payload(num_predict=NUM_PREDICT):
    return {
        "model": "qwen3:1.7b",
        "messages": [{"role": "user", "content": "research"}],
        "stream": False,
        "think": False,
        "format": {"type": "object"},
        "keep_alive": "10m",
        "options": {
            "temperature": 0,
            "num_ctx": NUM_CTX,
            "num_predict": num_predict,
        },
    }


class M1965OllamaTruncationRetryTests(unittest.TestCase):
    def test_normal_success_is_one_384_call(self):
        raw = ScriptedTransport([{"done_reason": "stop", "message": {"content": "{}"}}])
        result = TruncationRetryTransport(raw)("POST", "http://127.0.0.1:11434/api/chat", payload(), 240.0)
        self.assertEqual(result["done_reason"], "stop")
        self.assertEqual(len(raw.calls), 1)
        self.assertEqual(raw.calls[0][2]["options"]["num_predict"], 384)

    def test_length_retries_exactly_once_at_640(self):
        raw = ScriptedTransport([
            {"done_reason": "length", "message": {"content": "partial"}},
            {"done_reason": "stop", "message": {"content": "{}"}},
        ])
        result = TruncationRetryTransport(raw)("POST", "http://127.0.0.1:11434/api/chat", payload(), 240.0)
        self.assertEqual(result["done_reason"], "stop")
        self.assertEqual(len(raw.calls), 2)
        self.assertEqual([call[2]["options"]["num_predict"] for call in raw.calls], [384, 640])
        self.assertEqual(raw.calls[0][2]["options"]["num_predict"], 384)
        self.assertFalse(raw.calls[1][2]["think"])
        self.assertFalse(raw.calls[1][2]["stream"])
        self.assertEqual(raw.calls[1][2]["options"]["num_ctx"], 4096)
        self.assertEqual(raw.calls[1][2]["options"]["temperature"], 0)
        self.assertEqual(raw.calls[0][2]["format"], raw.calls[1][2]["format"])
        self.assertEqual(raw.calls[0][2]["messages"], raw.calls[1][2]["messages"])

    def test_second_length_is_returned_without_third_attempt(self):
        raw = ScriptedTransport([
            {"done_reason": "length", "message": {"content": "partial-a"}},
            {"done_reason": "length", "message": {"content": "partial-b"}},
        ])
        result = TruncationRetryTransport(raw)("POST", "http://127.0.0.1:11434/api/chat", payload(), 240.0)
        self.assertEqual(result["done_reason"], "length")
        self.assertEqual(len(raw.calls), 2)

    def test_non_length_response_never_retries(self):
        raw = ScriptedTransport([{"done_reason": "error", "message": {"content": "bad"}}])
        TruncationRetryTransport(raw)("POST", "http://127.0.0.1:11434/api/chat", payload(), 240.0)
        self.assertEqual(len(raw.calls), 1)

    def test_transport_exception_never_retries(self):
        raw = ScriptedTransport([TimeoutError("bounded timeout")])
        with self.assertRaises(TimeoutError):
            TruncationRetryTransport(raw)("POST", "http://127.0.0.1:11434/api/chat", payload(), 240.0)
        self.assertEqual(len(raw.calls), 1)

    def test_get_and_non_chat_requests_never_retry(self):
        for method, url in (
            ("GET", "http://127.0.0.1:11434/api/tags"),
            ("POST", "http://127.0.0.1:11434/api/generate"),
        ):
            raw = ScriptedTransport([{"done_reason": "length"}])
            TruncationRetryTransport(raw)(method, url, None if method == "GET" else payload(), 30.0)
            self.assertEqual(len(raw.calls), 1)

    def test_nonstandard_initial_budget_is_not_escalated(self):
        raw = ScriptedTransport([{"done_reason": "length", "message": {"content": "partial"}}])
        TruncationRetryTransport(raw)("POST", "http://127.0.0.1:11434/api/chat", payload(512), 240.0)
        self.assertEqual(len(raw.calls), 1)

    def test_budget_constants_are_bounded(self):
        self.assertEqual(NUM_PREDICT, 384)
        self.assertEqual(TRUNCATION_RETRY_NUM_PREDICT, 640)
        self.assertLess(TRUNCATION_RETRY_NUM_PREDICT, NUM_CTX)


if __name__ == "__main__":
    unittest.main()
