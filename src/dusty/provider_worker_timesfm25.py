from __future__ import annotations

"""External TimesFM 2.5 worker executed only by its isolated Python."""

import argparse
from datetime import datetime
from hashlib import sha256
from importlib import metadata
import json
from math import isfinite
import os
import sys
from typing import Any


PROTOCOL = "dusty-isolated-forecast-provider-v1"
PROVIDER_ID = "timesfm-2.5"
MODEL_ID = "google/timesfm-2.5-200m-transformers"
MODEL_REVISION = "5a9806b9b291fad9233b5249d88263f1846304d3"
RUNTIME_VERSION = "transformers==5.16.1"
TARGET = "completed_close_after_horizon_observations"
DISTRIBUTION_METHOD = "native_quantiles"
SAMPLE_COUNT = 1
QUANTILE_LEVELS = (0.1, 0.5, 0.9)
MIN_CONTEXT_OBSERVATIONS = 32
MAX_CONTEXT_OBSERVATIONS = 2048
MAX_HORIZON_STEPS = 64
REQUEST_FIELDS = frozenset(
    {
        "protocol",
        "provider_id",
        "model_id",
        "model_revision",
        "runtime_version",
        "symbol",
        "timeframe",
        "as_of",
        "horizon_steps",
        "target",
        "context_sha256",
        "context",
        "quantile_levels",
        "distribution_method",
    }
)


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _payload_sha256(payload: object) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label}_invalid")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label}_must_be_timezone_aware")
    return parsed


def _validate_request(request: Any) -> dict[str, Any]:
    if not isinstance(request, dict):
        raise TypeError("request_must_be_object")
    if set(request) != REQUEST_FIELDS:
        raise ValueError("request_schema_has_missing_or_unexpected_fields")
    expected = {
        "protocol": PROTOCOL,
        "provider_id": PROVIDER_ID,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "runtime_version": RUNTIME_VERSION,
        "target": TARGET,
        "quantile_levels": list(QUANTILE_LEVELS),
        "distribution_method": DISTRIBUTION_METHOD,
    }
    for key, value in expected.items():
        if request.get(key) != value:
            raise ValueError(f"request_identity_mismatch:{key}")
    if not isinstance(request.get("symbol"), str) or not request["symbol"].strip():
        raise ValueError("request_symbol_invalid")
    if not isinstance(request.get("timeframe"), str) or not request["timeframe"].strip():
        raise ValueError("request_timeframe_invalid")
    horizon = request.get("horizon_steps")
    if type(horizon) is not int or not 1 <= horizon <= MAX_HORIZON_STEPS:
        raise ValueError("request_horizon_out_of_bounds")
    context = request.get("context")
    if (
        not isinstance(context, list)
        or not MIN_CONTEXT_OBSERVATIONS <= len(context) <= MAX_CONTEXT_OBSERVATIONS
    ):
        raise ValueError("request_context_length_out_of_bounds")
    if request.get("context_sha256") != _payload_sha256(tuple(context)):
        raise ValueError("request_context_sha256_mismatch")
    previous_at: datetime | None = None
    for row in context:
        if not isinstance(row, dict) or set(row) != {"at", "close"}:
            raise ValueError("request_context_row_schema_invalid")
        at = _aware_timestamp(row["at"], "request_context_timestamp")
        close = row["close"]
        if previous_at is not None and at <= previous_at:
            raise ValueError("request_context_timestamps_not_strictly_increasing")
        if (
            isinstance(close, bool)
            or not isinstance(close, (int, float))
            or not isfinite(close)
            or close <= 0
        ):
            raise ValueError("request_context_close_invalid")
        previous_at = at
    if _aware_timestamp(request.get("as_of"), "request_as_of") != previous_at:
        raise ValueError("request_as_of_must_equal_last_completed_observation")
    return dict(request)


def _load_runtime() -> tuple[Any, Any, str]:
    installed = metadata.version("transformers")
    if installed != "5.16.1":
        raise RuntimeError(
            f"timesfm_runtime_version_mismatch:expected=5.16.1:actual={installed}"
        )
    import torch
    from transformers import TimesFm2_5ModelForPrediction

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    model = TimesFm2_5ModelForPrediction.from_pretrained(
        MODEL_ID,
        revision=MODEL_REVISION,
        local_files_only=True,
    )
    model = model.to(dtype=torch.float32, device="cpu").eval()
    if tuple(float(value) for value in model.config.quantiles) != tuple(
        index / 10 for index in range(1, 10)
    ):
        raise RuntimeError("timesfm_quantile_configuration_drift")
    if int(model.config.decode_index) != 5:
        raise RuntimeError("timesfm_decode_index_drift")
    return torch, model, f"transformers=={installed}"


def _run_loaded(
    request: dict[str, Any], *, torch: Any, model: Any, installed_version: str
) -> dict[str, object]:
    closes = [float(row["close"]) for row in request["context"]]
    past = torch.tensor(closes, dtype=torch.float32, device="cpu")
    with torch.no_grad():
        outputs = model(past_values=[past], return_dict=True)
    full = outputs.full_predictions.detach().cpu()
    horizon = int(request["horizon_steps"])
    if full.ndim != 3 or full.shape[0] != 1 or full.shape[1] < horizon or full.shape[2] < 10:
        raise ValueError(f"unexpected_timesfm_quantile_shape:{tuple(full.shape)}")
    terminal = full[0, horizon - 1]
    p10 = float(terminal[1])
    p50 = float(terminal[5])
    p90 = float(terminal[9])
    if any(not isfinite(value) or value <= 0 for value in (p10, p50, p90)):
        raise ValueError("timesfm_returned_nonfinite_or_nonpositive_price")
    if not p10 <= p50 <= p90:
        raise ValueError("timesfm_returned_crossed_quantiles")
    return {
        "protocol": PROTOCOL,
        "provider_id": PROVIDER_ID,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "provider_version": installed_version,
        "request_sha256": _payload_sha256(request),
        "context_sha256": request["context_sha256"],
        "as_of": request["as_of"],
        "horizon_steps": horizon,
        "target": TARGET,
        "distribution_method": DISTRIBUTION_METHOD,
        "sample_count": SAMPLE_COUNT,
        "origin_value": closes[-1],
        "quantiles": {"p10": p10, "p50": p50, "p90": p90},
    }


def _ready_event(installed_version: str) -> dict[str, object]:
    return {
        "event": "ready",
        "protocol": PROTOCOL,
        "provider_id": PROVIDER_ID,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "provider_version": installed_version,
    }


def _write_stdout(payload: object) -> None:
    sys.stdout.write(_canonical_json(payload) + "\n")
    sys.stdout.flush()


def _write_error(exc: Exception) -> None:
    sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
    sys.stderr.flush()


def _run_one_shot() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            raise ValueError("empty_provider_request")
        request = _validate_request(json.loads(raw))
        torch, model, version = _load_runtime()
        _write_stdout(_run_loaded(request, torch=torch, model=model, installed_version=version))
        return 0
    except Exception as exc:
        _write_error(exc)
        return 2


def _run_persistent() -> int:
    try:
        torch, model, version = _load_runtime()
        _write_stdout(_ready_event(version))
    except Exception as exc:
        _write_error(exc)
        return 2
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            request = _validate_request(json.loads(raw))
            _write_stdout(_run_loaded(request, torch=torch, model=model, installed_version=version))
        except Exception as exc:
            _write_error(exc)
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--persistent", action="store_true")
    args = parser.parse_args(argv)
    return _run_persistent() if args.persistent else _run_one_shot()


if __name__ == "__main__":
    raise SystemExit(main())
