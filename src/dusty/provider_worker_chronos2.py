from __future__ import annotations

"""External Chronos-2 worker.

This file is executed by the provider's isolated Python interpreter, not by
Dusty's core interpreter. Keep imports at module scope standard-library only so
Dusty's own test environment never needs torch/chronos installed.
"""

from datetime import datetime
from hashlib import sha256
from importlib import metadata
import json
from math import isfinite
import os
import sys
from typing import Any


PROTOCOL = "dusty-isolated-forecast-provider-v1"
PROVIDER_ID = "chronos2"
MODEL_ID = "amazon/chronos-2"
MODEL_REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"
RUNTIME_VERSION = "2.3.1"
TARGET = "completed_close_after_horizon_observations"
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
        "quantile_levels",
        "context_sha256",
        "context",
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
    }
    for key, value in expected.items():
        if request.get(key) != value:
            raise ValueError(f"request_identity_mismatch:{key}")
    symbol = request.get("symbol")
    timeframe = request.get("timeframe")
    if not isinstance(symbol, str) or not symbol.strip() or not isinstance(timeframe, str) or not timeframe.strip():
        raise ValueError("request_symbol_or_timeframe_invalid")
    horizon = request.get("horizon_steps")
    if type(horizon) is not int or not 1 <= horizon <= MAX_HORIZON_STEPS:
        raise ValueError("request_horizon_out_of_bounds")
    context = request.get("context")
    if not isinstance(context, list) or not MIN_CONTEXT_OBSERVATIONS <= len(context) <= MAX_CONTEXT_OBSERVATIONS:
        raise ValueError("request_context_length_out_of_bounds")
    context_sha = request.get("context_sha256")
    if context_sha != _payload_sha256(tuple(context)):
        raise ValueError("request_context_sha256_mismatch")

    previous_at: datetime | None = None
    for row in context:
        if not isinstance(row, dict) or set(row) != {"at", "close"}:
            raise ValueError("request_context_row_schema_invalid")
        at = _aware_timestamp(row["at"], "request_context_timestamp")
        close = row["close"]
        if previous_at is not None and at <= previous_at:
            raise ValueError("request_context_timestamps_not_strictly_increasing")
        if isinstance(close, bool) or not isinstance(close, (int, float)) or not isfinite(close) or close <= 0:
            raise ValueError("request_context_close_invalid")
        previous_at = at

    as_of = _aware_timestamp(request.get("as_of"), "request_as_of")
    if as_of != previous_at:
        raise ValueError("request_as_of_must_equal_last_completed_observation")
    # Return the validated request unchanged so its SHA-256 identity is stable.
    return dict(request)


def _extract_last_horizon_quantiles(tensor: Any, horizon_steps: int) -> tuple[float, float, float]:
    values = tensor.detach().cpu()
    if values.ndim == 3 and values.shape[0] == 1:
        values = values[0]
    if values.ndim != 2 or tuple(values.shape) != (horizon_steps, len(QUANTILE_LEVELS)):
        raise ValueError(f"unexpected_chronos_quantile_shape:{tuple(values.shape)}")
    p10, p50, p90 = (float(value) for value in values[-1].tolist())
    if any(not isfinite(value) or value <= 0 for value in (p10, p50, p90)):
        raise ValueError("chronos_returned_nonfinite_or_nonpositive_price")
    if not p10 <= p50 <= p90:
        raise ValueError("chronos_returned_crossed_quantiles")
    return p10, p50, p90


def _run(request: dict[str, Any]) -> dict[str, object]:
    installed_version = metadata.version("chronos-forecasting")
    if installed_version != RUNTIME_VERSION:
        raise RuntimeError(
            f"chronos_runtime_version_mismatch:expected={RUNTIME_VERSION}:actual={installed_version}"
        )

    # Heavy provider imports intentionally happen only inside the provider process.
    import torch
    from chronos import BaseChronosPipeline

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    closes = [float(row["close"]) for row in request["context"]]
    pipeline = BaseChronosPipeline.from_pretrained(
        MODEL_ID,
        revision=MODEL_REVISION,
        device_map="cpu",
        local_files_only=True,
    )
    quantiles, _point = pipeline.predict_quantiles(
        [torch.tensor(closes, dtype=torch.float32)],
        prediction_length=int(request["horizon_steps"]),
        quantile_levels=list(QUANTILE_LEVELS),
        cross_learning=False,
    )
    if len(quantiles) != 1:
        raise ValueError(f"unexpected_chronos_result_count:{len(quantiles)}")
    p10, p50, p90 = _extract_last_horizon_quantiles(
        quantiles[0], int(request["horizon_steps"])
    )
    request_sha = _payload_sha256(request)
    return {
        "protocol": PROTOCOL,
        "provider_id": PROVIDER_ID,
        "model_id": MODEL_ID,
        "model_revision": MODEL_REVISION,
        "provider_version": installed_version,
        "request_sha256": request_sha,
        "context_sha256": request["context_sha256"],
        "as_of": request["as_of"],
        "horizon_steps": request["horizon_steps"],
        "target": TARGET,
        "origin_value": closes[-1],
        "quantiles": {"p10": p10, "p50": p50, "p90": p90},
    }


def main() -> int:
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            raise ValueError("empty_provider_request")
        request = _validate_request(json.loads(raw))
        response = _run(request)
        sys.stdout.write(_canonical_json(response))
        sys.stdout.write("\n")
        sys.stdout.flush()
        return 0
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        sys.stderr.flush()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
