from __future__ import annotations

from pathlib import Path
import unittest

from dusty.ollama_runtime_evidence import collect_ollama_runtime_evidence, validate_prior_diagnostic


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


class M19682OllamaRuntimeEvidenceTests(unittest.TestCase):
    def test_collects_read_only_runtime_surfaces_without_generation(self) -> None:
        gets: list[str] = []
        commands: list[list[str]] = []

        def http_get(url: str, timeout: float) -> dict[str, object]:
            gets.append(url)
            return {"version": "0.20.6"} if url.endswith("/api/version") else {"models": []}

        def run(argv: list[str], timeout: float) -> dict[str, object]:
            commands.append(argv)
            return {"argv": argv, "returncode": 0, "stdout": "ok", "stderr": ""}

        result = collect_ollama_runtime_evidence(
            prior_diagnostic=prior(),
            http_get=http_get,
            run_command=run,
            log_reader=lambda path, lines: {"path": str(path), "present": True, "tail": ["x"]},
        )
        payload = result.to_payload()
        self.assertEqual(gets, ["http://127.0.0.1:11434/api/version", "http://127.0.0.1:11434/api/ps"])
        self.assertEqual(commands[0], ["ollama", "--version"])
        self.assertEqual(commands[1], ["ollama", "ps"])
        self.assertEqual(commands[2][0], "tasklist")
        self.assertEqual(payload["cli_ps"]["argv"], ["ollama", "ps"])
        self.assertFalse(payload["generation_invoked"])
        self.assertFalse(payload["canary_retry_authorized"])
        self.assertTrue(all(value is False for value in payload["authority"].values()))

    def test_nonlocal_endpoint_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            collect_ollama_runtime_evidence(prior_diagnostic=prior(), base_url="http://example.com:11434")

    def test_prior_must_be_exact_timeout_state(self) -> None:
        bad = prior()
        bad["stages"] = [{"name": "model_identity", "status": "fail", "detail": "drift"}]
        with self.assertRaises(ValueError):
            validate_prior_diagnostic(bad)

    def test_authority_or_retry_escalation_is_rejected(self) -> None:
        bad = prior()
        bad["canary_retry_authorized"] = True
        with self.assertRaises(ValueError):
            validate_prior_diagnostic(bad)

    def test_server_log_reader_is_bounded_by_injected_contract(self) -> None:
        seen: list[tuple[Path, int]] = []

        def reader(path: Path, lines: int) -> dict[str, object]:
            seen.append((path, lines))
            return {"path": str(path), "present": False, "tail": []}

        collect_ollama_runtime_evidence(
            prior_diagnostic=prior(),
            http_get=lambda url, timeout: {},
            run_command=lambda argv, timeout: {"argv": argv, "returncode": 0, "stdout": "", "stderr": ""},
            log_reader=reader,
        )
        self.assertEqual(seen[0][1], 250)


if __name__ == "__main__":
    unittest.main()
