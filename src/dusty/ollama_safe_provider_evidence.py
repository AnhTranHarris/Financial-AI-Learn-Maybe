from __future__ import annotations

"""Fail-closed, read-only Ollama provider evidence for M196.8.3.

No model generation and no Ollama CLI invocation are permitted here.  The
collector uses only bounded localhost HTTP GET requests and bounded log reads so
a wedged native Ollama subprocess cannot trap the caller's PowerShell console.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Callable
from urllib.error import URLError
from urllib.request import Request, urlopen

PROTOCOL = "dusty-m19683-safe-provider-evidence-v1"
LOCAL_ENDPOINTS = {"http://127.0.0.1:11434", "http://localhost:11434"}
EXPECTED_PRIOR_PROTOCOL = "dusty-m19681-ollama-reconstruction-diagnostic-v1"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _http_get(url: str, timeout: float) -> dict[str, object]:
    request = Request(url, method="GET", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost-only caller
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Ollama endpoint returned non-object JSON")
    return payload


def _bounded_tail(path: Path, *, max_bytes: int = 256_000, max_lines: int = 300) -> dict[str, object]:
    if not path.is_file():
        return {"path": str(path), "present": False, "tail": []}
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > max_bytes:
            handle.seek(size - max_bytes)
        data = handle.read(max_bytes)
    text = data.decode("utf-8", errors="replace")
    return {
        "path": str(path),
        "present": True,
        "size_bytes": size,
        "tail": text.splitlines()[-max_lines:],
    }


def validate_prior_diagnostic(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise ValueError("prior diagnostic must be an object")
    if payload.get("protocol") != EXPECTED_PRIOR_PROTOCOL:
        raise ValueError("prior diagnostic protocol mismatch")
    if payload.get("status") != "fail" or payload.get("recovery_action") != "provider_health_blocked":
        raise ValueError("prior diagnostic must be the provider-health blocked state")
    if payload.get("canary_retry_authorized") is not False:
        raise ValueError("prior diagnostic unexpectedly authorized retry")
    authority = payload.get("authority")
    if not isinstance(authority, dict) or any(value is not False for value in authority.values()):
        raise ValueError("prior diagnostic authority must remain false")
    stages = payload.get("stages")
    if not isinstance(stages, list) or len(stages) != 2:
        raise ValueError("prior diagnostic stage sequence invalid")
    identity, basic = stages
    if not isinstance(identity, dict) or not isinstance(basic, dict):
        raise ValueError("prior diagnostic stages invalid")
    if identity.get("name") != "model_identity" or identity.get("status") != "pass":
        raise ValueError("exact model identity was not proven")
    if basic.get("name") != "basic_chat" or basic.get("status") != "fail":
        raise ValueError("basic chat failure was not isolated")
    if "TimeoutError" not in str(basic.get("detail", "")):
        raise ValueError("basic chat failure was not a timeout")
    return payload


def _safe_get(
    *,
    url: str,
    timeout: float,
    http_get: Callable[[str, float], dict[str, object]],
) -> dict[str, object]:
    try:
        return {"status": "pass", "payload": http_get(url, timeout)}
    except (TimeoutError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "fail", "error": f"{type(exc).__name__}:{exc}"}


def classify_runtime(api_version: dict[str, object], api_ps: dict[str, object], server_log: dict[str, object]) -> dict[str, object]:
    if api_version.get("status") != "pass":
        return {"state": "api_unavailable", "canary_retry_authorized": False}
    if api_ps.get("status") != "pass":
        return {"state": "runtime_inventory_unavailable", "canary_retry_authorized": False}

    ps_payload = api_ps.get("payload")
    models = ps_payload.get("models", []) if isinstance(ps_payload, dict) else []
    if not isinstance(models, list):
        return {"state": "runtime_inventory_invalid", "canary_retry_authorized": False}

    resident: list[dict[str, object]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        size = int(item.get("size", 0) or 0)
        size_vram = int(item.get("size_vram", 0) or 0)
        placement = "unknown"
        if size > 0 and size_vram <= 0:
            placement = "cpu"
        elif size > 0 and size_vram >= size:
            placement = "gpu"
        elif size > 0 and 0 < size_vram < size:
            placement = "split_cpu_gpu"
        resident.append(
            {
                "name": item.get("name") or item.get("model"),
                "digest": item.get("digest"),
                "size": size,
                "size_vram": size_vram,
                "placement": placement,
                "context_length": item.get("context_length"),
                "expires_at": item.get("expires_at"),
            }
        )

    log_lines = server_log.get("tail", []) if isinstance(server_log, dict) else []
    log_text = "\n".join(str(line) for line in log_lines).lower()
    markers = {
        "gpu_fallback": any(token in log_text for token in ("fall back to cpu", "fallback to cpu", "gpu discovery", "no compatible gpu")),
        "runner_error": any(token in log_text for token in ("runner process", "llama runner", "error loading model", "failed to load model")),
        "timeout": "timeout" in log_text or "timed out" in log_text,
        "out_of_memory": "out of memory" in log_text or "cuda out of memory" in log_text,
    }

    state = "runtime_idle" if not resident else "runtime_resident"
    return {
        "state": state,
        "resident_models": resident,
        "log_markers": markers,
        "canary_retry_authorized": False,
    }


@dataclass(frozen=True, slots=True)
class SafeProviderEvidence:
    model_tag: str
    model_digest: str
    prior_diagnostic_fingerprint: str
    api_version: dict[str, object]
    api_ps: dict[str, object]
    server_log: dict[str, object]
    classification: dict[str, object]

    def to_payload(self, *, include_fingerprint: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "protocol": PROTOCOL,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "prior_diagnostic_fingerprint": self.prior_diagnostic_fingerprint,
            "api_version": self.api_version,
            "api_ps": self.api_ps,
            "server_log": self.server_log,
            "classification": self.classification,
            "ollama_cli_invoked": False,
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


def collect_safe_provider_evidence(
    *,
    prior_diagnostic: dict[str, object],
    base_url: str = "http://127.0.0.1:11434",
    http_get: Callable[[str, float], dict[str, object]] = _http_get,
    log_reader: Callable[[Path], dict[str, object]] = _bounded_tail,
) -> SafeProviderEvidence:
    prior = validate_prior_diagnostic(prior_diagnostic)
    root = base_url.rstrip("/")
    if root not in LOCAL_ENDPOINTS:
        raise ValueError("Ollama endpoint must be localhost:11434")

    version = _safe_get(url=f"{root}/api/version", timeout=10.0, http_get=http_get)
    ps = _safe_get(url=f"{root}/api/ps", timeout=10.0, http_get=http_get)

    local = os.environ.get("LOCALAPPDATA", "").strip()
    log_path = Path(local) / "Ollama" / "server.log" if local else Path("server.log")
    server_log = log_reader(log_path)
    classification = classify_runtime(version, ps, server_log)

    return SafeProviderEvidence(
        model_tag=str(prior.get("model_tag", "")),
        model_digest=str(prior.get("model_digest", "")).lower(),
        prior_diagnostic_fingerprint=str(prior.get("diagnostic_fingerprint", "")),
        api_version=version,
        api_ps=ps,
        server_log=server_log,
        classification=classification,
    )


broker_write_authority = False
live_write_authority = False
promotion_authority = False
retry_authority = False
risk_override_authority = False
