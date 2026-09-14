from __future__ import annotations

from pathlib import Path
import unittest

from dusty.ollama_safe_provider_evidence import (
    classify_runtime,
    collect_safe_provider_evidence,
    validate_prior_diagnostic,
)

DIGEST = "a" * 64


def prior() -> dict[str, object]:
    return {
        "protocol": "dusty-m19681-ollama-reconstruction-diagnostic-v1",
        "status": "fail",
        "recovery_action": "provider_health_blocked",
        "model_tag": "qwen3:1.7b",
        "model_digest": DIGEST,
        "diagnostic_fingerprint": "b" * 64,
        "canary_retry_authorized": False,
        "authority": {"broker_write": False, "retry": False},
        "stages": [
            {"name": "model_identity", "status": "pass", "detail": "exact model digest present"},
            {"name": "basic_chat", "status": "fail", "detail": "TimeoutError:timed out"},
        ],
    }


class M19683SafeProviderEvidenceTests(unittest.TestCase):
    def test_collects_only_bounded_http_and_log_evidence(self) -> None:
        urls: list[tuple[str, float]] = []

        def http_get(url: str, timeout: float) -> dict[str, object]:
            urls.append((url, timeout))
            if url.endswith("/api/version"):
                return {"version": "0.20.6"}
            return {
                "models": [
                    {
                        "name": "qwen3:1.7b",
                        "digest": DIGEST,
                        "size": 1_000,
                        "size_vram": 0,
                        "context_length": 4096,
                    }
                ]
            }

        result = collect_safe_provider_evidence(
            prior_diagnostic=prior(),
            http_get=http_get,
            log_reader=lambda path: {"path": str(path), "present": True, "tail": []},
        ).to_payload()

        self.assertEqual(
            urls,
            [
                ("http://127.0.0.1:11434/api/version", 10.0),
                ("http://127.0.0.1:11434/api/ps", 10.0),
            ],
        )
        self.assertFalse(result["ollama_cli_invoked"])
        self.assertFalse(result["generation_invoked"])
        self.assertFalse(result["canary_retry_authorized"])
        self.assertTrue(all(value is False for value in result["authority"].values()))
        resident = result["classification"]["resident_models"]
        self.assertEqual(resident[0]["placement"], "cpu")

    def test_http_timeout_is_evidence_not_exception(self) -> None:
        def http_get(url: str, timeout: float) -> dict[str, object]:
            raise TimeoutError("blocked")

        payload = collect_safe_provider_evidence(
            prior_diagnostic=prior(),
            http_get=http_get,
            log_reader=lambda path: {"path": str(path), "present": False, "tail": []},
        ).to_payload()
        self.assertEqual(payload["api_version"]["status"], "fail")
        self.assertEqual(payload["classification"]["state"], "api_unavailable")
        self.assertFalse(payload["classification"]["canary_retry_authorized"])

    def test_runtime_inventory_timeout_is_distinct(self) -> None:
        def http_get(url: str, timeout: float) -> dict[str, object]:
            if url.endswith("/api/version"):
                return {"version": "0.20.6"}
            raise TimeoutError("ps blocked")

        payload = collect_safe_provider_evidence(
            prior_diagnostic=prior(),
            http_get=http_get,
            log_reader=lambda path: {"path": str(path), "present": False, "tail": []},
        ).to_payload()
        self.assertEqual(payload["classification"]["state"], "runtime_inventory_unavailable")

    def test_vram_placement_is_derived_from_api_ps(self) -> None:
        classification = classify_runtime(
            {"status": "pass", "payload": {"version": "x"}},
            {
                "status": "pass",
                "payload": {
                    "models": [
                        {"name": "gpu", "size": 100, "size_vram": 100},
                        {"name": "split", "size": 100, "size_vram": 40},
                    ]
                },
            },
            {"tail": []},
        )
        placements = [item["placement"] for item in classification["resident_models"]]
        self.assertEqual(placements, ["gpu", "split_cpu_gpu"])

    def test_log_markers_are_classified_without_authorizing_retry(self) -> None:
        classification = classify_runtime(
            {"status": "pass", "payload": {"version": "x"}},
            {"status": "pass", "payload": {"models": []}},
            {"tail": ["GPU discovery timed out; fallback to CPU", "runner process error loading model"]},
        )
        self.assertTrue(classification["log_markers"]["gpu_fallback"])
        self.assertTrue(classification["log_markers"]["runner_error"])
        self.assertFalse(classification["canary_retry_authorized"])

    def test_nonlocal_endpoint_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            collect_safe_provider_evidence(prior_diagnostic=prior(), base_url="http://example.com:11434")

    def test_prior_authority_escalation_fails_closed(self) -> None:
        bad = prior()
        bad["canary_retry_authorized"] = True
        with self.assertRaises(ValueError):
            validate_prior_diagnostic(bad)

    def test_bounded_log_reader_contract_can_be_injected(self) -> None:
        seen: list[Path] = []

        def reader(path: Path) -> dict[str, object]:
            seen.append(path)
            return {"path": str(path), "present": False, "tail": []}

        collect_safe_provider_evidence(
            prior_diagnostic=prior(),
            http_get=lambda url, timeout: {"version": "x"} if url.endswith("version") else {"models": []},
            log_reader=reader,
        )
        self.assertEqual(len(seen), 1)


if __name__ == "__main__":
    unittest.main()
