from __future__ import annotations

"""One-shot isolated worker for Vibe-Trading's research-only tool surface.

This worker is executed with Vibe's own Python environment. It never runs
Vibe's LLM/ReAct agent. Dusty supplies exactly one allowlisted research tool
request on stdin and receives exactly one JSON response on stdout.
"""

import contextlib
from hashlib import sha256
import importlib.metadata
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable


PROTOCOL = "dusty-vibe-research-v1"
PROVIDER_ID = "vibe-trading"
EXPECTED_VIBE_VERSION = "0.1.14"
ALLOWED_TOOLS = frozenset(
    {
        "alpha_zoo",
        "list_strategies",
        "query_strategies",
        "get_strategy_evidence",
        "get_market_data",
        "technical_indicators",
        "pattern_recognition",
        "factor_analysis",
        "backtest",
        "web_search",
        "read_url",
    }
)
PATH_ARGUMENTS = {
    "pattern_recognition": ("run_dir",),
    "factor_analysis": ("factor_csv", "return_csv", "output_dir"),
    "backtest": ("run_dir",),
}


def _emit(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True), flush=True)


def _bounded(value: object, limit: int = 1000) -> str:
    rendered = " ".join(str(value or "").strip().split())
    return rendered[:limit] if rendered else "vibe_research_unknown_error"


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _unwrap(candidate: Any) -> Callable[..., Any]:
    fn = getattr(candidate, "fn", None) or getattr(candidate, "__wrapped__", candidate)
    if not callable(fn):
        raise TypeError("vibe_tool_not_callable")
    return fn


def _load_surface(vibe_root: Path) -> tuple[Any, str]:
    agent_dir = vibe_root / "agent"
    surface = agent_dir / "mcp_server.py"
    if not surface.is_file():
        raise FileNotFoundError("vibe_mcp_surface_missing")
    digest = sha256(surface.read_bytes()).hexdigest()
    if str(agent_dir) not in sys.path:
        sys.path.insert(0, str(agent_dir))
    # Third-party libraries sometimes print banners during import. Keep stdout
    # reserved for Dusty's protocol and route any such chatter to stderr.
    with contextlib.redirect_stdout(sys.stderr):
        import mcp_server  # type: ignore[import-not-found]
    return mcp_server, digest


def _validate_paths(tool: str, arguments: dict[str, object], work_root: Path) -> None:
    for field in PATH_ARGUMENTS.get(tool, ()):
        value = arguments.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"vibe_path_argument_invalid:{field}")
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = work_root / candidate
        if not _inside(work_root, candidate):
            raise ValueError(f"vibe_path_outside_work_root:{field}")
        arguments[field] = str(candidate.resolve())


def _tool_result_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _looks_like_tool_error(result_text: str) -> str | None:
    try:
        payload = json.loads(result_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if str(payload.get("status", "")).lower() != "error":
        return None
    return _bounded(payload.get("error") or payload)


def main() -> int:
    try:
        raw = sys.stdin.read()
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise TypeError("vibe_request_must_be_object")
        expected_keys = {"protocol", "provider_id", "vibe_version", "tool", "arguments"}
        if set(request) != expected_keys:
            raise ValueError("vibe_request_schema_mismatch")
        if request.get("protocol") != PROTOCOL or request.get("provider_id") != PROVIDER_ID:
            raise ValueError("vibe_request_identity_mismatch")
        if request.get("vibe_version") != EXPECTED_VIBE_VERSION:
            raise ValueError("vibe_request_version_mismatch")
        tool = request.get("tool")
        if not isinstance(tool, str) or tool not in ALLOWED_TOOLS:
            raise ValueError("vibe_research_tool_not_allowlisted")
        arguments = request.get("arguments")
        if not isinstance(arguments, dict) or any(not isinstance(key, str) for key in arguments):
            raise TypeError("vibe_arguments_must_be_object")

        vibe_root_raw = os.environ.get("DUSTY_VIBE_ROOT", "")
        work_root_raw = os.environ.get("DUSTY_VIBE_WORK_ROOT", "")
        if not vibe_root_raw or not work_root_raw:
            raise RuntimeError("vibe_worker_paths_missing")
        vibe_root = Path(vibe_root_raw).resolve()
        work_root = Path(work_root_raw).resolve()
        work_root.mkdir(parents=True, exist_ok=True)
        _validate_paths(tool, arguments, work_root)

        installed_version = importlib.metadata.version("vibe-trading-ai")
        if installed_version != EXPECTED_VIBE_VERSION:
            raise RuntimeError(f"vibe_version_drift:{installed_version}")
        surface, surface_sha = _load_surface(vibe_root)
        candidate = getattr(surface, tool, None)
        if candidate is None:
            raise RuntimeError(f"vibe_tool_missing:{tool}")
        fn = _unwrap(candidate)
        with contextlib.redirect_stdout(sys.stderr):
            value = fn(**arguments)
        result_text = _tool_result_text(value)
        if not result_text:
            raise RuntimeError("vibe_tool_empty_result")
        tool_error = _looks_like_tool_error(result_text)
        if tool_error:
            _emit(
                {
                    "protocol": PROTOCOL,
                    "provider_id": PROVIDER_ID,
                    "vibe_version": installed_version,
                    "tool": tool,
                    "status": "tool_error",
                    "surface_sha256": surface_sha,
                    "error": tool_error,
                }
            )
            return 0
        _emit(
            {
                "protocol": PROTOCOL,
                "provider_id": PROVIDER_ID,
                "vibe_version": installed_version,
                "tool": tool,
                "status": "ok",
                "surface_sha256": surface_sha,
                "result_text": result_text,
            }
        )
        return 0
    except Exception as exc:  # isolated worker boundary
        _emit(
            {
                "protocol": PROTOCOL,
                "provider_id": PROVIDER_ID,
                "vibe_version": EXPECTED_VIBE_VERSION,
                "status": "worker_error",
                "error": _bounded(f"{type(exc).__name__}:{exc}"),
            }
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
