from __future__ import annotations

"""Dusty-side adapter for Vibe-Trading's bounded research-tool surface."""

import json
from pathlib import Path
import subprocess
from typing import Callable, Mapping

from .provider_forecast_adapter import _child_environment
from .vibe_research_contract import (
    ALLOWED_TOOLS,
    EXPECTED_VIBE_VERSION,
    PATH_ARGUMENTS,
    PROTOCOL,
    PROVIDER_ID,
    VibeResearchEvidence,
    VibeResearchResult,
    VibeResearchStatus,
    build_request,
    canonical_json,
    sha256_text,
)


DEFAULT_TIMEOUT_SECONDS = 180


def _bounded_error(value: str | None, limit: int = 1000) -> str:
    rendered = " ".join((value or "").strip().split())
    return rendered[:limit] if rendered else "vibe_research_unknown_error"


def _inside(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _vibe_python(vibe_root: Path) -> Path:
    windows = vibe_root / ".venv" / "Scripts" / "python.exe"
    if windows.is_file():
        return windows
    posix = vibe_root / ".venv" / "bin" / "python"
    return posix


class VibeResearchContractor:
    """Runs allowlisted Vibe research tools without invoking Vibe's LLM agent.

    The child receives no LLM provider variables, broker credentials or MT5
    state. File-bearing calls are confined to ``work_root``. Tool faults return
    UNAVAILABLE and can never disable Dusty's deterministic lane.
    """

    def __init__(
        self,
        vibe_root: Path,
        work_root: Path,
        *,
        worker_path: Path | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if type(timeout_seconds) is not int or not 5 <= timeout_seconds <= 600:
            raise ValueError("vibe_research_timeout_must_be_5_to_600_seconds")
        self.vibe_root = vibe_root.resolve()
        self.work_root = work_root.resolve()
        self.worker_path = worker_path or Path(__file__).with_name("vibe_research_worker.py")
        self.timeout_seconds = timeout_seconds
        self._runner = runner

    @property
    def allowed_tools(self) -> tuple[str, ...]:
        return tuple(sorted(ALLOWED_TOOLS))

    def probe(self) -> VibeResearchResult:
        return self.invoke("alpha_zoo", {"action": "health"})

    def invoke(self, tool: str, arguments: Mapping[str, object]) -> VibeResearchResult:
        try:
            request = build_request(tool, arguments)
            normalized_arguments = dict(request["arguments"])
            self._validate_paths(tool, normalized_arguments)
            request["arguments"] = normalized_arguments
        except (TypeError, ValueError) as exc:
            return self._unavailable(f"vibe_request_invalid:{type(exc).__name__}:{exc}")

        python_executable = _vibe_python(self.vibe_root)
        if not python_executable.is_file():
            return self._unavailable("vibe_python_missing")
        if not (self.vibe_root / "agent" / "mcp_server.py").is_file():
            return self._unavailable("vibe_mcp_surface_missing")
        if not self.worker_path.is_file():
            return self._unavailable("vibe_worker_missing")

        self.work_root.mkdir(parents=True, exist_ok=True)
        isolated_home = self.work_root / ".contractor-home"
        isolated_home.mkdir(parents=True, exist_ok=True)
        environment = _child_environment()
        environment.update(
            {
                "DUSTY_VIBE_ROOT": str(self.vibe_root),
                "DUSTY_VIBE_WORK_ROOT": str(self.work_root),
                "HOME": str(isolated_home),
                "USERPROFILE": str(isolated_home),
                "VIBE_TRADING_HOME": str(isolated_home / ".vibe-trading"),
                "VIBE_TRADING_ENABLE_SHELL_TOOLS": "0",
            }
        )
        # Explicitly remove every known LLM/trading authority route even if a
        # future change broadens the shared child-environment helper.
        for key in tuple(environment):
            upper = key.upper()
            if (
                upper.startswith("LANGCHAIN_")
                or upper.startswith("OPENAI_")
                or upper.startswith("ANTHROPIC_")
                or upper.startswith("OLLAMA_")
                or upper.startswith("MT5_")
                or "BROKER" in upper
                or "TRADING_PASSWORD" in upper
            ):
                environment.pop(key, None)

        request_text = canonical_json(request)
        request_sha = sha256_text(request_text)
        try:
            completed = self._runner(
                [str(python_executable), str(self.worker_path)],
                input=request_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                env=environment,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._unavailable(f"vibe_research_timeout:{self.timeout_seconds}s")
        except OSError as exc:
            return self._unavailable(f"vibe_research_launch_failed:{type(exc).__name__}:{exc}")

        if completed.returncode != 0:
            return self._unavailable(
                f"vibe_research_process_failed:{completed.returncode}:{_bounded_error(completed.stderr)}"
            )
        response_text = (completed.stdout or "").strip()
        if not response_text:
            return self._unavailable("vibe_research_empty_response")
        try:
            response = json.loads(response_text)
        except json.JSONDecodeError as exc:
            return self._unavailable(f"vibe_research_response_not_json:{exc.msg}")
        try:
            return self._parse_response(tool, request_sha, response_text, response)
        except (KeyError, TypeError, ValueError) as exc:
            return self._unavailable(f"vibe_research_response_invalid:{type(exc).__name__}:{exc}")

    def _validate_paths(self, tool: str, arguments: dict[str, object]) -> None:
        for field in PATH_ARGUMENTS.get(tool, ()):
            value = arguments.get(field)
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"vibe_path_argument_invalid:{field}")
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = self.work_root / candidate
            if not _inside(self.work_root, candidate):
                raise ValueError(f"vibe_path_outside_work_root:{field}")
            arguments[field] = str(candidate.resolve())

    @staticmethod
    def _parse_response(
        tool: str,
        request_sha: str,
        response_text: str,
        response: object,
    ) -> VibeResearchResult:
        if not isinstance(response, dict):
            raise TypeError("vibe_response_must_be_object")
        if response.get("protocol") != PROTOCOL or response.get("provider_id") != PROVIDER_ID:
            raise ValueError("vibe_response_identity_mismatch")
        if response.get("vibe_version") != EXPECTED_VIBE_VERSION:
            raise ValueError("vibe_response_version_mismatch")
        status = response.get("status")
        if status != "ok":
            error = response.get("error")
            if not isinstance(error, str) or not error.strip():
                raise ValueError("vibe_error_response_missing_error")
            return VibeResearchResult(
                status=VibeResearchStatus.UNAVAILABLE,
                error=f"vibe_{status}:{_bounded_error(error)}",
            )
        if response.get("tool") != tool:
            raise ValueError("vibe_response_tool_mismatch")
        surface_sha = response.get("surface_sha256")
        result_text = response.get("result_text")
        if not isinstance(surface_sha, str) or not isinstance(result_text, str):
            raise TypeError("vibe_response_payload_invalid")
        evidence = VibeResearchEvidence(
            protocol=PROTOCOL,
            provider_id=PROVIDER_ID,
            tool=tool,
            vibe_version=EXPECTED_VIBE_VERSION,
            surface_sha256=surface_sha,
            request_sha256=request_sha,
            response_sha256=sha256_text(response_text),
            result_text=result_text,
        )
        return VibeResearchResult(status=VibeResearchStatus.AVAILABLE, evidence=evidence)

    @staticmethod
    def _unavailable(error: str) -> VibeResearchResult:
        return VibeResearchResult(status=VibeResearchStatus.UNAVAILABLE, error=_bounded_error(error))
