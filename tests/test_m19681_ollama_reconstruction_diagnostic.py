from __future__ import annotations

import json
import unittest

from dusty.ollama_reconstruction_diagnostic import diagnose_ollama_reconstruction


DIGEST = "a" * 64


class M19681OllamaReconstructionDiagnosticTests(unittest.TestCase):
    def test_exact_model_basic_and_structured_health_pass(self) -> None:
        calls: list[tuple[str, str, object, float]] = []
        ticks = iter((0.0, 0.1, 0.1, 0.5, 0.5, 1.0))

        def clock() -> float:
            return next(ticks)

        def transport(method: str, url: str, payload: object, timeout: float):
            calls.append((method, url, payload, timeout))
            if method == "GET":
                return {"models": [{"name": "qwen3:1.7b", "digest": DIGEST}]}
            assert isinstance(payload, dict)
            if "format" in payload:
                return {"message": {"content": json.dumps({"status": "ok"})}, "done": True}
            return {"message": {"content": "OK"}, "done": True}

        result = diagnose_ollama_reconstruction(
            model_tag="qwen3:1.7b", model_digest=DIGEST, transport=transport, clock=clock
        )
        self.assertEqual(result.status, "pass")
        self.assertEqual([row.name for row in result.stages], ["model_identity", "basic_chat", "structured_chat"])
        self.assertTrue(all(row.status == "pass" for row in result.stages))
        self.assertFalse(result.to_payload()["canary_retry_authorized"])
        self.assertTrue(all(value is False for value in result.to_payload()["authority"].values()))
        self.assertEqual(calls[1][2]["options"]["num_ctx"], 1024)
        self.assertEqual(calls[2][2]["options"]["num_predict"], 32)

    def test_model_drift_stops_before_chat(self) -> None:
        calls: list[str] = []
        ticks = iter((0.0, 0.1))

        def transport(method: str, url: str, payload: object, timeout: float):
            calls.append(method)
            return {"models": [{"name": "qwen3:1.7b", "digest": "b" * 64}]}

        result = diagnose_ollama_reconstruction(
            model_tag="qwen3:1.7b", model_digest=DIGEST, transport=transport, clock=lambda: next(ticks)
        )
        self.assertEqual(result.status, "fail")
        self.assertEqual(calls, ["GET"])
        self.assertEqual(result.stages[-1].name, "model_identity")

    def test_basic_timeout_stops_before_structured_probe(self) -> None:
        calls: list[str] = []
        ticks = iter((0.0, 0.1))

        def transport(method: str, url: str, payload: object, timeout: float):
            calls.append(method)
            if method == "GET":
                return {"models": [{"name": "qwen3:1.7b", "digest": DIGEST}]}
            raise TimeoutError("timed out")

        result = diagnose_ollama_reconstruction(
            model_tag="qwen3:1.7b", model_digest=DIGEST, transport=transport, clock=lambda: next(ticks)
        )
        self.assertEqual(result.status, "fail")
        self.assertEqual(calls, ["GET", "POST"])
        self.assertEqual(result.stages[-1].name, "basic_chat")
        self.assertIn("TimeoutError", result.stages[-1].detail)

    def test_structured_failure_is_distinct_from_basic_chat(self) -> None:
        ticks = iter((0.0, 0.1, 0.1, 0.2, 0.2, 0.3))

        def transport(method: str, url: str, payload: object, timeout: float):
            if method == "GET":
                return {"models": [{"name": "qwen3:1.7b", "digest": DIGEST}]}
            assert isinstance(payload, dict)
            if "format" in payload:
                return {"message": {"content": "not-json"}, "done": True}
            return {"message": {"content": "OK"}, "done": True}

        result = diagnose_ollama_reconstruction(
            model_tag="qwen3:1.7b", model_digest=DIGEST, transport=transport, clock=lambda: next(ticks)
        )
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.stages[1].status, "pass")
        self.assertEqual(result.stages[2].name, "structured_chat")
        self.assertEqual(result.stages[2].status, "fail")

    def test_nonlocal_endpoint_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            diagnose_ollama_reconstruction(
                model_tag="qwen3:1.7b", model_digest=DIGEST, base_url="http://example.com:11434"
            )


if __name__ == "__main__":
    unittest.main()
