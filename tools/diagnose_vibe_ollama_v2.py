from __future__ import annotations

"""Read-only A/B diagnostic for Vibe-Trading -> Ollama/Qwen.

Compares Ollama's native chat API (with thinking explicitly disabled) against
its OpenAI-compatible endpoint, then runs Vibe's provider doctor and a bounded
minimal agent invocation. No MT5, broker, account, order, or config writes.
"""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from time import perf_counter
from typing import Any
from urllib.request import Request, urlopen


OLLAMA_BASE_URL = "http://localhost:11434"


@dataclass
class StageResult:
    name: str
    status: str
    elapsed_seconds: float
    detail: str = ""


def _emit(payload: object) -> None:
    # ASCII-only JSON is intentional: Windows PowerShell 5 commonly exposes a
    # cp1252 console and Vibe prints box-drawing characters in its UI.
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True), flush=True)


def _bounded(value: str, limit: int = 5000) -> str:
    clean = "\n".join(line.rstrip() for line in (value or "").splitlines() if line.strip())
    return clean if len(clean) <= limit else clean[-limit:]


def _http_json(url: str, *, payload: object | None = None, timeout: int = 60) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - localhost only
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _run(command: list[str], *, cwd: Path, env: dict[str, str], timeout: int) -> tuple[int, str, str, float]:
    before = perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return completed.returncode, completed.stdout or "", completed.stderr or "", perf_counter() - before
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return 124, stdout, stderr + f"\nTIMEOUT after {timeout}s", perf_counter() - before


def _stage(name: str, fn) -> StageResult:
    before = perf_counter()
    try:
        detail = fn()
        result = StageResult(name, "pass", round(perf_counter() - before, 3), _bounded(str(detail or "")))
    except Exception as exc:  # diagnostic boundary
        result = StageResult(name, "fail", round(perf_counter() - before, 3), _bounded(f"{type(exc).__name__}: {exc}"))
    _emit({"event": "stage", **asdict(result)})
    return result


def _native_message(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict) or not isinstance(response.get("message"), dict):
        raise RuntimeError("native_chat_schema_invalid")
    return response["message"]


def _openai_message(response: Any) -> dict[str, Any]:
    try:
        message = response["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("openai_chat_schema_invalid") from exc
    if not isinstance(message, dict):
        raise RuntimeError("openai_chat_message_invalid")
    return message


def _tool_name(message: dict[str, Any]) -> str | None:
    calls = message.get("tool_calls") or []
    if not isinstance(calls, list) or not calls:
        return None
    first = calls[0]
    if not isinstance(first, dict):
        return None
    fn = first.get("function") or {}
    return str(fn.get("name")) if isinstance(fn, dict) and fn.get("name") else None


def _message_debug(message: dict[str, Any]) -> str:
    useful = {
        "content": message.get("content"),
        "reasoning": message.get("reasoning"),
        "reasoning_content": message.get("reasoning_content"),
        "thinking": message.get("thinking"),
        "tool_calls": message.get("tool_calls"),
    }
    return json.dumps(useful, ensure_ascii=True, separators=(",", ":"))


def _classification(stages: list[StageResult]) -> str:
    s = {x.name: x for x in stages}
    def passed(name: str) -> bool:
        return name in s and s[name].status == "pass"

    if not passed("ollama"):
        return "OLLAMA_SERVER_BLOCKED"
    if not passed("model"):
        return "MODEL_MISSING_OR_INVALID"
    if not passed("native_chat"):
        return "MODEL_NATIVE_CHAT_BLOCKED"
    if not passed("native_tool_call"):
        return "MODEL_NATIVE_TOOL_CALL_BLOCKED"
    if not passed("openai_chat"):
        return "OLLAMA_OPENAI_CHAT_COMPAT_BLOCKED"
    if not passed("openai_tool_call"):
        return "OLLAMA_OPENAI_TOOL_COMPAT_BLOCKED"
    if not passed("vibe_doctor"):
        return "VIBE_PROVIDER_CONFIG_BLOCKED"
    if not passed("vibe_agent"):
        return "VIBE_AGENT_LOOP_BLOCKED"
    return "PASS"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vibe-root", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--http-timeout", type=int, default=90)
    parser.add_argument("--agent-timeout", type=int, default=180)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)

    vibe_root = args.vibe_root.resolve()
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    vibe_python = vibe_root / ".venv" / "Scripts" / "python.exe"
    vibe_cli = vibe_root / ".venv" / "Scripts" / "vibe-trading.exe"

    stages: list[StageResult] = []
    _emit({
        "event": "diagnostic_start_v2",
        "model": args.model,
        "vibe_root": str(vibe_root),
        "safety": {"config_write": False, "mt5": False, "broker_credentials": False, "orders": False},
    })

    stages.append(_stage("paths", lambda: (
        f"python={vibe_python};cli={vibe_cli}"
        if vibe_python.is_file() and vibe_cli.is_file()
        else (_ for _ in ()).throw(FileNotFoundError("vibe_virtualenv_or_cli_missing"))
    )))

    def check_ollama() -> str:
        payload = _http_json(f"{OLLAMA_BASE_URL}/api/tags", timeout=args.http_timeout)
        models = payload.get("models", []) if isinstance(payload, dict) else []
        if not isinstance(models, list):
            raise RuntimeError("ollama_tags_schema_invalid")
        names = [str(m.get("name", "")) for m in models if isinstance(m, dict)]
        return "models=" + ",".join(names)
    stages.append(_stage("ollama", check_ollama))

    def check_model() -> str:
        payload = _http_json(f"{OLLAMA_BASE_URL}/api/show", payload={"model": args.model}, timeout=args.http_timeout)
        if not isinstance(payload, dict):
            raise RuntimeError("ollama_show_schema_invalid")
        caps = payload.get("capabilities") or []
        details = payload.get("details") or {}
        return f"capabilities={caps};details={details}"
    stages.append(_stage("model", check_model))

    def native_chat() -> str:
        response = _http_json(
            f"{OLLAMA_BASE_URL}/api/chat",
            payload={
                "model": args.model,
                "messages": [{"role": "user", "content": "Reply with exactly VIBE_NATIVE_OK and nothing else."}],
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 64},
            },
            timeout=args.http_timeout,
        )
        message = _native_message(response)
        content = str(message.get("content") or "").strip()
        if "VIBE_NATIVE_OK" not in content:
            raise RuntimeError("native_chat_no_marker:" + _message_debug(message))
        return _message_debug(message)
    stages.append(_stage("native_chat", native_chat))

    tool = {
        "type": "function",
        "function": {
            "name": "dusty_echo",
            "description": "Return the supplied diagnostic value.",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
        },
    }

    def native_tool() -> str:
        response = _http_json(
            f"{OLLAMA_BASE_URL}/api/chat",
            payload={
                "model": args.model,
                "messages": [{"role": "user", "content": "Call dusty_echo exactly once with value DUSTY_NATIVE_TOOL_OK. Do not answer normally."}],
                "tools": [tool],
                "stream": False,
                "think": False,
                "options": {"temperature": 0, "num_predict": 128},
            },
            timeout=args.http_timeout,
        )
        message = _native_message(response)
        name = _tool_name(message)
        if name != "dusty_echo":
            raise RuntimeError("native_tool_missing:" + _message_debug(message))
        return _message_debug(message)
    stages.append(_stage("native_tool_call", native_tool))

    def openai_chat() -> str:
        response = _http_json(
            f"{OLLAMA_BASE_URL}/v1/chat/completions",
            payload={
                "model": args.model,
                "messages": [{"role": "user", "content": "/no_think\nReply with exactly VIBE_OPENAI_OK and nothing else."}],
                "stream": False,
                "temperature": 0,
                "max_tokens": 256,
            },
            timeout=args.http_timeout,
        )
        message = _openai_message(response)
        content = str(message.get("content") or "").strip()
        if "VIBE_OPENAI_OK" not in content:
            raise RuntimeError("openai_chat_no_marker:" + _message_debug(message))
        return _message_debug(message)
    stages.append(_stage("openai_chat", openai_chat))

    def openai_tool() -> str:
        response = _http_json(
            f"{OLLAMA_BASE_URL}/v1/chat/completions",
            payload={
                "model": args.model,
                "messages": [{"role": "user", "content": "/no_think\nCall dusty_echo exactly once with value DUSTY_OPENAI_TOOL_OK. Do not answer normally."}],
                "tools": [tool],
                "tool_choice": "auto",
                "stream": False,
                "temperature": 0,
                "max_tokens": 256,
            },
            timeout=args.http_timeout,
        )
        message = _openai_message(response)
        name = _tool_name(message)
        if name != "dusty_echo":
            raise RuntimeError("openai_tool_missing:" + _message_debug(message))
        return _message_debug(message)
    stages.append(_stage("openai_tool_call", openai_tool))

    child_env = os.environ.copy()
    child_env.update({
        "LANGCHAIN_PROVIDER": "ollama",
        "LANGCHAIN_MODEL_NAME": args.model,
        "OLLAMA_BASE_URL": OLLAMA_BASE_URL,
        "OPENAI_API_KEY": "ollama",
        "TIMEOUT_SECONDS": str(args.agent_timeout),
        "LANGCHAIN_TEMPERATURE": "0",
        "LANGCHAIN_REASONING_EFFORT": "none",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    })

    def vibe_version() -> str:
        code, stdout, stderr, _ = _run(
            [str(vibe_python), "-c", "import importlib.metadata as m; print(m.version('vibe-trading-ai'))"],
            cwd=vibe_root, env=child_env, timeout=30,
        )
        if code != 0:
            raise RuntimeError(f"version_probe_failed:{_bounded(stderr)}")
        return stdout.strip()
    stages.append(_stage("vibe_version", vibe_version))

    def vibe_doctor() -> str:
        code, stdout, stderr, _ = _run([str(vibe_cli), "provider", "doctor"], cwd=vibe_root, env=child_env, timeout=60)
        combined = f"{stdout}\n{stderr}"
        if code != 0:
            raise RuntimeError(f"provider_doctor_exit={code}:{_bounded(combined)}")
        low = combined.lower()
        if "ollama" not in low or args.model.lower() not in low:
            raise RuntimeError("provider_doctor_wrong_config:" + _bounded(combined))
        return combined
    stages.append(_stage("vibe_doctor", vibe_doctor))

    def vibe_agent() -> str:
        prompt = (
            "/no_think\nReply with exactly VIBE_AGENT_OK and nothing else. "
            "Do not call tools, do not perform market research, do not access external data, and do not create files."
        )
        code, stdout, stderr, elapsed = _run(
            [str(vibe_cli), "run", "-p", prompt],
            cwd=vibe_root, env=child_env, timeout=args.agent_timeout,
        )
        combined = f"{stdout}\n{stderr}"
        if code == 124:
            raise TimeoutError(f"vibe_agent_timeout_after_{args.agent_timeout}s:{_bounded(combined)}")
        if code != 0:
            raise RuntimeError(f"vibe_agent_exit={code}:{_bounded(combined)}")
        if "VIBE_AGENT_OK" not in combined:
            raise RuntimeError("vibe_agent_no_marker:" + _bounded(combined))
        return f"elapsed={elapsed:.3f}s\n{combined}"
    stages.append(_stage("vibe_agent", vibe_agent))

    classification = _classification(stages)
    report = {
        "schema": "dusty-vibe-ollama-diagnostic-v2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "classification": classification,
        "model": args.model,
        "vibe_root": str(vibe_root),
        "stages": [asdict(x) for x in stages],
        "safety": {"mt5_connected": False, "broker_credentials_used": False, "orders_enabled": False, "vibe_config_modified": False},
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    _emit({"event": "diagnostic_complete", "classification": classification, "report": str(report_path)})
    return 0 if classification == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
