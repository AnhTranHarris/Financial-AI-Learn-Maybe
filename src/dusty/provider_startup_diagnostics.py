from __future__ import annotations

"""Bounded local diagnostics for external forecast-provider cold starts.

This module deliberately bypasses Dusty's persistent JSON-lines owner. It starts
one provider environment at a time, loads only the pinned local model stack, and
records stage markers. No MT5 connection or broker/account data is involved.
"""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from time import perf_counter
from typing import Mapping

from .provider_forecast_adapter import _child_environment, canonical_json
from .provider_registry import ProviderHealth, ProviderRegistry, ProviderSnapshot


DEFAULT_TIMEOUT_SECONDS = 180


@dataclass(frozen=True, slots=True)
class StartupDiagnostic:
    provider_id: str
    status: str
    elapsed_seconds: float
    returncode: int | None
    stages: tuple[dict[str, object], ...]
    stderr: str
    error: str

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
            "returncode": self.returncode,
            "stages": list(self.stages),
            "stderr": self.stderr,
            "error": self.error,
        }


def _bounded(value: str, limit: int = 4000) -> str:
    rendered = "\n".join(line.rstrip() for line in value.splitlines() if line.strip())
    return rendered[-limit:]


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _parse_stages(stdout: str) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"stage": "non_json_stdout", "text": line[:500]})
            continue
        if isinstance(payload, dict):
            rows.append(payload)
        else:
            rows.append({"stage": "non_object_stdout", "text": str(payload)[:500]})
    return tuple(rows)


def _common_prelude(provider_id: str) -> str:
    return f'''\nimport json, os, time\nSTART = time.perf_counter()\ndef emit(stage, **extra):\n    payload = {{"provider_id": {provider_id!r}, "stage": stage, "elapsed_seconds": round(time.perf_counter() - START, 3)}}\n    payload.update(extra)\n    print(json.dumps(payload, sort_keys=True, separators=(",", ":")), flush=True)\nemit("python_started", pid=os.getpid(), cpu_count=os.cpu_count())\n'''


def _chronos_script(snapshot: ProviderSnapshot) -> str:
    spec = snapshot.spec
    return _common_prelude(spec.provider_id) + f'''\nfrom importlib import metadata\nemit("metadata_ready", runtime=metadata.version("chronos-forecasting"))\nemit("torch_import_start")\nimport torch\nemit("torch_imported", torch_version=torch.__version__)\nfrom chronos import BaseChronosPipeline\nemit("chronos_imported")\ntorch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))\nemit("model_load_start")\nmodel = BaseChronosPipeline.from_pretrained(\n    {spec.model_id!r},\n    revision={spec.model_revision!r},\n    device_map="cpu",\n    local_files_only=True,\n)\nemit("model_loaded", threads=torch.get_num_threads())\ndel model\nemit("startup_probe_passed")\n'''


def _kronos_script(snapshot: ProviderSnapshot) -> str:
    spec = snapshot.spec
    return _common_prelude(spec.provider_id) + f'''\nimport sys\nfrom pathlib import Path\nroot = Path(os.environ["DUSTY_PROVIDER_DIRECTORY"]).resolve()\nemit("provider_root_ready", root=str(root))\nsys.path.insert(0, str(root))\nemit("scientific_import_start")\nimport numpy as np\nimport pandas as pd\nimport torch\nfrom huggingface_hub import snapshot_download\nfrom model import Kronos, KronosPredictor, KronosTokenizer\nemit("scientific_imported", torch_version=torch.__version__)\ntorch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))\nemit("tokenizer_cache_resolve_start")\ntokenizer_path = snapshot_download(\n    repo_id={spec.tokenizer_id!r},\n    revision={spec.tokenizer_revision!r},\n    local_files_only=True,\n)\nemit("tokenizer_cache_resolved")\nemit("model_cache_resolve_start")\nmodel_path = snapshot_download(\n    repo_id={spec.model_id!r},\n    revision={spec.model_revision!r},\n    local_files_only=True,\n)\nemit("model_cache_resolved")\nemit("tokenizer_load_start")\ntokenizer = KronosTokenizer.from_pretrained(tokenizer_path).eval()\nemit("tokenizer_loaded")\nemit("model_load_start")\nmodel = Kronos.from_pretrained(model_path).eval()\nemit("model_loaded")\npredictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)\nemit("predictor_ready", threads=torch.get_num_threads())\ndel predictor, model, tokenizer\nemit("startup_probe_passed")\n'''


def _timesfm_script(snapshot: ProviderSnapshot) -> str:
    spec = snapshot.spec
    return _common_prelude(spec.provider_id) + f'''\nfrom importlib import metadata\nemit("metadata_ready", runtime="transformers==" + metadata.version("transformers"))\nemit("torch_import_start")\nimport torch\nemit("torch_imported", torch_version=torch.__version__)\nemit("transformers_import_start")\nfrom transformers import TimesFm2_5ModelForPrediction\nemit("transformers_imported")\ntorch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))\nemit("model_load_start")\nmodel = TimesFm2_5ModelForPrediction.from_pretrained(\n    {spec.model_id!r},\n    revision={spec.model_revision!r},\n    local_files_only=True,\n)\nmodel = model.to(dtype=torch.float32, device="cpu").eval()\nemit("model_loaded", threads=torch.get_num_threads())\ndel model\nemit("startup_probe_passed")\n'''


def _script(snapshot: ProviderSnapshot) -> str:
    if snapshot.spec.provider_id == "chronos2":
        return _chronos_script(snapshot)
    if snapshot.spec.provider_id == "kronos-small":
        return _kronos_script(snapshot)
    if snapshot.spec.provider_id == "timesfm-2.5":
        return _timesfm_script(snapshot)
    raise KeyError(snapshot.spec.provider_id)


def diagnose_provider(
    snapshot: ProviderSnapshot,
    *,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    source_environment: Mapping[str, str] | None = None,
) -> StartupDiagnostic:
    if snapshot.health is not ProviderHealth.INSTALLED:
        return StartupDiagnostic(
            provider_id=snapshot.spec.provider_id,
            status="unavailable",
            elapsed_seconds=0.0,
            returncode=None,
            stages=(),
            stderr="",
            error=f"provider_not_installed:{snapshot.health.value}",
        )
    environment = _child_environment(source_environment)
    environment["DUSTY_PROVIDER_DIRECTORY"] = str(snapshot.root)
    started = perf_counter()
    try:
        completed = subprocess.run(
            [str(snapshot.python_executable), "-u", "-c", _script(snapshot)],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _text(exc.stdout)
        stderr = _text(exc.stderr)
        return StartupDiagnostic(
            provider_id=snapshot.spec.provider_id,
            status="timeout",
            elapsed_seconds=round(perf_counter() - started, 3),
            returncode=None,
            stages=_parse_stages(stdout),
            stderr=_bounded(stderr),
            error=f"startup_probe_timeout:{timeout_seconds}s",
        )
    except OSError as exc:
        return StartupDiagnostic(
            provider_id=snapshot.spec.provider_id,
            status="launch_failed",
            elapsed_seconds=round(perf_counter() - started, 3),
            returncode=None,
            stages=(),
            stderr="",
            error=f"startup_probe_launch_failed:{type(exc).__name__}:{exc}",
        )
    stages = _parse_stages(completed.stdout)
    passed = bool(stages and stages[-1].get("stage") == "startup_probe_passed")
    status = "passed" if completed.returncode == 0 and passed else "failed"
    error = "" if status == "passed" else f"startup_probe_exit:{completed.returncode}"
    return StartupDiagnostic(
        provider_id=snapshot.spec.provider_id,
        status=status,
        elapsed_seconds=round(perf_counter() - started, 3),
        returncode=completed.returncode,
        stages=stages,
        stderr=_bounded(completed.stderr),
        error=error,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M114.1 isolated provider cold-start diagnostics")
    parser.add_argument("--provider-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    if not 30 <= args.timeout <= 600:
        parser.error("--timeout must be 30 to 600 seconds")

    registry = ProviderRegistry(args.provider_root)
    results: list[StartupDiagnostic] = []
    print(canonical_json({"event": "diagnostic_start", "timeout_seconds": args.timeout}), flush=True)
    for snapshot in registry.discover():
        result = diagnose_provider(snapshot, timeout_seconds=args.timeout)
        results.append(result)
        print(canonical_json({"event": "provider_startup_probe", **result.as_dict()}), flush=True)

    passed = all(result.status == "passed" for result in results)
    report = {
        "milestone": "M114.1",
        "status": "PASS" if passed else "DIAGNOSTIC_FAILURE",
        "provider_root": str(args.provider_root),
        "results": [result.as_dict() for result in results],
        "no_mt5_connection": True,
        "no_broker_credentials": True,
        "no_orders": True,
    }
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(canonical_json({"event": "diagnostic_complete", "status": report["status"]}), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
