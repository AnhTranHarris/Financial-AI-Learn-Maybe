from __future__ import annotations

"""Bounded local diagnostic for Vibe-Trading -> Ollama/Qwen.

This tool talks only to localhost Ollama and the local Vibe-Trading CLI. It does
not connect to MT5, brokers, accounts, or trading connectors and does not modify
Vibe configuration.
"""

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


OLLAMA_BASE_URL = "http://localhost:11434"


@dataclass
class StageResult:
    name: str
    status: str
    elapsed_seconds: float
    detail: str = ""


def _emit(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False), flush=True)


def _bounded(value: str, limit: int = 4000) -> str:
    clean = "\n".join(line.rstrip() for line in (value or "").splitlines() if line.strip())
    if len(clean) <= limit:
        return clean
    return clean[-limit:]


def _http_json(url: str, *, payload: object | None = None, timeout: int = 60) -> Any:
    data = None
    headers = {"Accept": "application/json"}
    method = "GET"
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        method = "POST"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - localhost only, validated by caller
        raw = response.read().decode("utf-8", errors="replace")
    return json.loads(raw)


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


def _tool_call_name(response: Any) -> str | None:
    try:
        calls = response["choices"][0]["message"].get("tool_calls") or []
        if not calls:
            return None
        function = calls[0].get("function") or {}
        name = function.get("name")
        return str(name) if name else None
    except (KeyError, IndexError, TypeError):
        return None


def _classify(stages: list[StageResult]) -> str:
    by_name = {stage.name: stage for stage in stages}
    for required in ("ollama", "model", "chat", "tool_call", "vibe_doctor", "vibe_agent"):
        stage = by_name.get(required)
        if stage is None or stage.status != "pass":
            if required == "tool_call" and by_name.get("chat", StageResult("", "fail", 0)).status == "pass":
                return "MODEL_TOOL_CALL_BLOCKED"
            if required == "vibe_agent" and by_name.get("vibe_doctor", StageResult("", "fail", 0)).status == "pass":
                return "VIBE_AGENT_LOOP_BLOCKED"
            return f"{required.upper()}_BLOCKED"
    return "PASS"


def _stage(name: str, fn) -> StageResult:
    before = perf_counter()
    try:
        detail = fn()
        result = StageResult(name, "pass", round(perf_counter() - before, 3), _bounded(str(detail or "")))
    except Exception as exc:  # diagnostic boundary intentionally captures all failures
        result = StageResult(
            name,
            "fail",
            round(perf_counter() - before, 3),
            _bounded(f"{type(exc).__name__}: {exc}"),
        )
    _emit({"event": "stage", **asdict(result)})
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose local Vibe-Trading -> Ollama/Qwen")
    parser.add_argument("--vibe-root", type=Path, required=True)
    parser.add_argument("--model", default="qwen3:1.7b")
    parser.add_argument("--http-timeout", type=int, default=90)
    parser.add_argument("--agent-timeout", type=int, default=180)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)

    vibe_root = args.vibe_root.resolve()
    report_path = args.report.resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)

    if os.name == "nt":
        vibe_python = vibe_root / ".venv" / "Scripts" / "python.exe"
        vibe_cli = vibe_root / ".venv" / "Scripts" / "vibe-trading.exe"
    else:
        vibe_python = vibe_root / ".venv" / "bin" / "python"
        vibe_cli = vibe_root / ".venv" / "bin" / "vibe-trading"

    stages: list[StageResult] = []
    _emit(
        {
            "event": "diagnostic_start",
            "vibe_root": str(vibe_root),
            "model": args.model,
            "ollama_base_url": OLLAMA_BASE_URL,
            "http_timeout": args.http_timeout,
            "agent_timeout": args.agent_timeout,
            "safety": {
                "mt5": False,
                "broker_credentials": False,
                "orders": False,
                "config_write": False,
            },
        }
    )

    stages.append(
        _stage(
            "paths",
            lambda: (
                f"python={vibe_python}; cli={vibe_cli}"
                if vibe_python.is_file() and vibe_cli.is_file()
                else (_ for _ in ()).throw(FileNotFoundError("vibe_virtualenv_or_cli_missing"))
            ),
        )
    )

    def check_ollama() -> str:
        payload = _http_json(f"{OLLAMA_BASE_URL}/api/tags", timeout=args.http_timeout)
        models = payload.get("models") if isinstance(payload, dict) else None
        if not isinstance(models, list):
            raise RuntimeError("ollama_tags_schema_invalid")
        return f"models={len(models)}"

    stages.append(_stage("ollama", check_ollama))

    def check_model() -> str:
        payload = _http_json(f"{OLLAMA_BASE_URL}/api/tags", timeout=args.http_timeout)
        names = {
            str(row.get("name", ""))
            for row in payload.get("models", [])
            if isinstance(row, dict)
        }
        if args.model not in names:
            raise RuntimeError(f"model_not_installed:{args.model}")
        return args.model

    stages.append(_stage("model", check_model))

    def check_chat() -> str:
        payload = {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": "/no_think\nReply with exactly VIBE_OLLAMA_OK and nothing else.",
                }
            ],
            "temperature": 0,
            "stream": False,
            "max_tokens": 32,
        }
        response = _http_json(
            f"{OLLAMA_BASE_URL}/v1/chat/completions",
            payload=payload,
            timeout=args.http_timeout,
        )
        try:
            content = str(response["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("ollama_openai_chat_schema_invalid") from exc
        if "VIBE_OLLAMA_OK" not in content:
            raise RuntimeError(f"unexpected_chat_response:{content[:200]}")
        return content

    stages.append(_stage("chat", check_chat))

    def check_tool_call() -> str:
        payload = {
            "model": args.model,
            "messages": [
                {
                    "role": "user",
                    "content": "/no_think\nCall dusty_echo exactly once with value DUSTY_TOOL_OK. Do not answer normally.",
                }
            ],
            "tools": [
                {
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
            ],
            "tool_choice": "auto",
            "temperature": 0,
            "stream": False,
            "max_tokens": 64,
        }
        response = _http_json(
            f"{OLLAMA_BASE_URL}/v1/chat/completions",
            payload=payload,
            timeout=args.http_timeout,
        )
        name = _tool_call_name(response)
        if name != "dusty_echo":
            message = ""
            try:
                message = str(response["choices"][0]["message"].get("content") or "")
            except (KeyError, IndexError, TypeError):
                pass
            raise RuntimeError(f"tool_call_missing_or_wrong:name={name}:content={message[:200]}")
        return name

    stages.append(_stage("tool_call", check_tool_call))

    child_env = os.environ.copy()
    child_env.update(
        {
            "LANGCHAIN_PROVIDER": "ollama",
            "LANGCHAIN_MODEL_NAME": args.model,
            "OLLAMA_BASE_URL": OLLAMA_BASE_URL,
            "TIMEOUT_SECONDS": str(args.agent_timeout),
            "LANGCHAIN_TEMPERATURE": "0",
        }
    )

    def check_version() -> str:
        code, stdout, stderr, _ = _run(
            [
                str(vibe_python),
                "-c",
                "import importlib.metadata as m; print(m.version('vibe-trading-ai'))",
            ],
            cwd=vibe_root,
            env=child_env,
            timeout=30,
        )
        if code != 0:
            raise RuntimeError(f"version_probe_failed:{_bounded(stderr)}")
        return stdout.strip()

    stages.append(_stage("vibe_version", check_version))

    def check_doctor() -> str:
        code, stdout, stderr, _ = _run(
            [str(vibe_cli), "provider", "doctor"],
            cwd=vibe_root,
            env=child_env,
            timeout=60,
        )
        if code != 0:
            raise RuntimeError(f"provider_doctor_exit={code}:{_bounded(stderr or stdout)}")
        combined = f"{stdout}\n{stderr}"
        lowered = combined.lower()
        if "ollama" not in lowered or args.model.lower() not in lowered:
            raise RuntimeError(f"provider_doctor_did_not_resolve_expected_config:{_bounded(combined)}")
        return combined

    stages.append(_stage("vibe_doctor", check_doctor))

    def check_agent() -> str:
        prompt = (
            "/no_think\n"
            "Reply with exactly VIBE_AGENT_OK and nothing else. "
            "Do not call tools, do not perform market research, do not access external data, "
            "and do not create files."
        )
        code, stdout, stderr, elapsed = _run(
            [str(vibe_cli), "run", "-p", prompt],
            cwd=vibe_root,
            env=child_env,
            timeout=args.agent_timeout,
        )
        combined = f"{stdout}\n{stderr}"
        if code == 124:
            raise TimeoutError(f"vibe_agent_timeout_after_{args.agent_timeout}s:{_bounded(combined)}")
        if code != 0:
            raise RuntimeError(f"vibe_agent_exit={code}:{_bounded(combined)}")
        if "VIBE_AGENT_OK" not in combined:
            raise RuntimeError(f"vibe_agent_completed_without_marker:{_bounded(combined)}")
        return f"elapsed={elapsed:.3f}s\n{combined}"

    stages.append(_stage("vibe_agent", check_agent))

    classification = _classify(stages)
    report = {
        "schema": "dusty-vibe-ollama-diagnostic-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "classification": classification,
        "model": args.model,
        "vibe_root": str(vibe_root),
        "stages": [asdict(stage) for stage in stages],
        "safety": {
            "mt5_connected": False,
            "broker_credentials_used": False,
            "orders_enabled": False,
            "vibe_config_modified": False,
        },
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _emit({"event": "diagnostic_complete", "classification": classification, "report": str(report_path)})
    return 0 if classification == "PASS" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (HTTPError, URLError) as exc:
        print(f"local_http_error:{exc}", file=sys.stderr)
        raise
