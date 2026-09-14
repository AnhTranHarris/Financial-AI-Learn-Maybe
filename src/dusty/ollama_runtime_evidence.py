from __future__ import annotations

"""Read-only Ollama runtime evidence collector for M196.8.2.

This module does not invoke model generation. It records provider version,
resident model state, bounded Windows process evidence, and a bounded server-log
tail so generation timeouts can be classified before any canary retry.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from typing import Callable
from urllib.request import Request, urlopen

PROTOCOL = "dusty-m19682-ollama-runtime-evidence-v1"
LOCAL_ENDPOINTS = {"http://127.0.0.1:11434", "http://localhost:11434"}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _http_get(url: str, timeout: float) -> dict[str, object]:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost-only caller
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ollama runtime endpoint returned a non-object")
    return payload


def _run_command(argv: list[str], timeout: float) -> dict[str, object]:
    completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    return {
        "argv": argv,
        "returncode": int(completed.returncode),
        "stdout": completed.stdout[-12000:],
        "stderr": completed.stderr[-12000:],
    }


def _tail_text(path: Path, max_lines: int = 250) -> dict[str, object]:
    if not path.is_file():
        return {"path": str(path), "present": False, "tail": []}
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"path": str(path), "present": True, "tail": text.splitlines()[-max_lines:]}


def validate_prior_diagnostic(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("prior diagnostic must be an object")
    if payload.get("protocol") != "dusty-m19681-ollama-reconstruction-diagnostic-v1":
        raise ValueError("prior diagnostic protocol mismatch")
    if payload.get("status") != "fail" or payload.get("recovery_action") != "provider_health_blocked":
        raise ValueError("prior diagnostic is not the provider-health blocked state")
    stages = payload.get("stages")
    if not isinstance(stages, list) or len(stages) != 2:
        raise ValueError("prior diagnostic stage sequence invalid")
    identity, basic = stages
    if not isinstance(identity, dict) or not isinstance(basic, dict):
        raise ValueError("prior diagnostic stages invalid")
    if identity.get("name") != "model_identity" or identity.get("status") != "pass":
        raise ValueError("prior diagnostic did not prove exact model identity")
    if basic.get("name") != "basic_chat" or basic.get("status") != "fail":
        raise ValueError("prior diagnostic did not isolate basic chat failure")
    detail = str(basic.get("detail", ""))
    if "TimeoutError" not in detail:
        raise ValueError("prior diagnostic basic chat failure was not a timeout")
    authority = payload.get("authority")
    if not isinstance(authority, dict) or any(value is not False for value in authority.values()):
        raise ValueError("prior diagnostic authority must remain false")
    if payload.get("canary_retry_authorized") is not False:
        raise ValueError("prior diagnostic unexpectedly authorized retry")
    return payload


@dataclass(frozen=True, slots=True)
class OllamaRuntimeEvidence:
    model_tag: str
    model_digest: str
    api_version: dict[str, object]
    api_ps: dict[str, object]
    cli_version: dict[str, object]
    cli_ps: dict[str, object]
    tasklist: dict[str, object]
    server_log: dict[str, object]
    prior_diagnostic_fingerprint: str

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol": PROTOCOL,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "prior_diagnostic_fingerprint": self.prior_diagnostic_fingerprint,
            "api_version": self.api_version,
            "api_ps": self.api_ps,
            "cli_version": self.cli_version,
            "cli_ps": self.cli_ps,
            "tasklist": self.tasklist,
            "server_log": self.server_log,
            "generation_invoked": False,
            "canary_retry_authorized": False,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
                "guardian_override": False,
            },
        }
        if include_fingerprint:
            payload["evidence_fingerprint"] = _fingerprint(payload)
        return payload


def collect_ollama_runtime_evidence(
    *,
    prior_diagnostic: dict[str, object],
    base_url: str = "http://127.0.0.1:11434",
    http_get: Callable[[str, float], dict[str, object]] = _http_get,
    run_command: Callable[[list[str], float], dict[str, object]] = _run_command,
    log_reader: Callable[[Path, int], dict[str, object]] = _tail_text,
) -> OllamaRuntimeEvidence:
    prior = validate_prior_diagnostic(prior_diagnostic)
    root = base_url.rstrip("/")
    if root not in LOCAL_ENDPOINTS:
        raise ValueError("Ollama runtime evidence endpoint must be localhost:11434")
    model_tag = str(prior.get("model_tag", "")).strip()
    model_digest = str(prior.get("model_digest", "")).strip().lower()
    if not model_tag or len(model_digest) != 64:
        raise ValueError("prior diagnostic model identity missing")

    api_version = http_get(f"{root}/api/version", 15.0)
    api_ps = http_get(f"{root}/api/ps", 15.0)
    cli_version = run_command(["ollama", "--version"], 15.0)
    cli_ps = run_command(["ollama", "ps"], 15.0)
    tasklist = run_command(["tasklist", "/FI", "IMAGENAME eq ollama.exe", "/FO", "CSV"], 15.0)
    local = os.environ.get("LOCALAPPDATA", "").strip()
    log_path = Path(local) / "Ollama" / "server.log" if local else Path("server.log")
    server_log = log_reader(log_path, 250)

    return OllamaRuntimeEvidence(
        model_tag=model_tag,
        model_digest=model_digest,
        api_version=api_version,
        api_ps=api_ps,
        cli_version=cli_version,
        cli_ps=cli_ps,
        tasklist=tasklist,
        server_log=server_log,
        prior_diagnostic_fingerprint=str(prior.get("diagnostic_fingerprint", "")),
    )


broker_write_authority = False
live_write_authority = False
promotion_authority = False
retry_authority = False
risk_override_authority = False
