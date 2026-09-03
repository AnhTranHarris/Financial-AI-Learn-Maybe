from __future__ import annotations

"""External Kronos-small worker executed only by its isolated Python."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import sys
from typing import Any


PROTOCOL = "dusty-isolated-forecast-provider-v1"
PROVIDER_ID = "kronos-small"
MODEL_ID = "NeoQuasar/Kronos-small"
MODEL_REVISION = "901c26c1332695a2a8f243eb2f37243a37bea320"
TOKENIZER_ID = "NeoQuasar/Kronos-Tokenizer-base"
TOKENIZER_REVISION = "0e0117387f39004a9016484a186a908917e22426"
SOURCE_REVISION = "67b630e67f6a18c9e9be918d9b4337c960db1e9a"
RUNTIME_VERSION = f"source@{SOURCE_REVISION}"
TARGET = "completed_close_after_horizon_observations"
DISTRIBUTION_METHOD = "empirical_5_seed_paths"
SAMPLE_COUNT = 5
MIN_CONTEXT_OBSERVATIONS = 32
MAX_CONTEXT_OBSERVATIONS = 512
MAX_HORIZON_STEPS = 64
REQUEST_FIELDS = frozenset(
    {
        "protocol",
        "provider_id",
        "model_id",
        "model_revision",
        "runtime_version",
        "source_revision",
        "tokenizer_id",
        "tokenizer_revision",
        "symbol",
        "timeframe",
        "as_of",
        "horizon_steps",
        "target",
        "context_sha256",
        "context",
        "future_times",
        "distribution_method",
        "sample_count",
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


def _git_directory(root: Path) -> Path:
    marker = root / ".git"
    if marker.is_dir():
        return marker
    if marker.is_file():
        text = marker.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            target = Path(text.split(":", 1)[1].strip())
            return target if target.is_absolute() else (root / target).resolve()
    raise RuntimeError("kronos_source_git_metadata_missing")


def _repository_head(root: Path) -> str:
    git_dir = _git_directory(root)
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    if not head.startswith("ref:"):
        return head
    ref = head.split(":", 1)[1].strip()
    loose = git_dir / ref
    if loose.is_file():
        return loose.read_text(encoding="utf-8").strip()
    packed = git_dir / "packed-refs"
    if packed.is_file():
        for line in packed.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith(("#", "^")):
                continue
            sha, name = line.split(" ", 1)
            if name == ref:
                return sha
    raise RuntimeError("kronos_source_head_unresolved")


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
        "source_revision": SOURCE_REVISION,
        "tokenizer_id": TOKENIZER_ID,
        "tokenizer_revision": TOKENIZER_REVISION,
        "target": TARGET,
        "distribution_method": DISTRIBUTION_METHOD,
        "sample_count": SAMPLE_COUNT,
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
        if not isinstance(row, dict) or set(row) != {
            "at",
            "open",
            "high",
            "low",
            "close",
            "volume",
        }:
            raise ValueError("request_context_row_schema_invalid")
        at = _aware_timestamp(row["at"], "request_context_timestamp")
        if previous_at is not None and at <= previous_at:
            raise ValueError("request_context_timestamps_not_strictly_increasing")
        prices = tuple(row[name] for name in ("open", "high", "low", "close"))
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or value <= 0
            for value in prices
        ):
            raise ValueError("request_context_price_invalid")
        if row["high"] < max(row["open"], row["low"], row["close"]):
            raise ValueError("request_context_ohlc_geometry_invalid")
        if row["low"] > min(row["open"], row["high"], row["close"]):
            raise ValueError("request_context_ohlc_geometry_invalid")
        volume = row["volume"]
        if (
            isinstance(volume, bool)
            or not isinstance(volume, (int, float))
            or not isfinite(volume)
            or volume < 0
        ):
            raise ValueError("request_context_volume_invalid")
        previous_at = at
    as_of = _aware_timestamp(request.get("as_of"), "request_as_of")
    if as_of != previous_at:
        raise ValueError("request_as_of_must_equal_last_completed_observation")
    future_times = request.get("future_times")
    if not isinstance(future_times, list) or len(future_times) != horizon:
        raise ValueError("request_future_schedule_length_invalid")
    previous = as_of
    for raw in future_times:
        at = _aware_timestamp(raw, "request_future_timestamp")
        if at <= previous:
            raise ValueError("request_future_schedule_not_strictly_increasing")
        previous = at
    return dict(request)


def _load_runtime() -> tuple[Any, Any, Any, Any, str]:
    provider_root_raw = os.environ.get("DUSTY_PROVIDER_DIRECTORY", "").strip()
    if not provider_root_raw:
        raise RuntimeError("kronos_provider_directory_missing")
    provider_root = Path(provider_root_raw).resolve()
    actual_source = _repository_head(provider_root)
    if actual_source != SOURCE_REVISION:
        raise RuntimeError(
            f"kronos_source_revision_mismatch:expected={SOURCE_REVISION}:actual={actual_source}"
        )
    sys.path.insert(0, str(provider_root))

    import numpy as np
    import pandas as pd
    import torch
    from huggingface_hub import snapshot_download
    from model import Kronos, KronosPredictor, KronosTokenizer

    torch.set_num_threads(max(1, min(4, os.cpu_count() or 1)))
    tokenizer_path = snapshot_download(
        repo_id=TOKENIZER_ID,
        revision=TOKENIZER_REVISION,
        local_files_only=True,
    )
    model_path = snapshot_download(
        repo_id=MODEL_ID,
        revision=MODEL_REVISION,
        local_files_only=True,
    )
    tokenizer = KronosTokenizer.from_pretrained(tokenizer_path).eval()
    model = Kronos.from_pretrained(model_path).eval()
    predictor = KronosPredictor(model, tokenizer, device="cpu", max_context=512)
    return np, pd, torch, predictor, RUNTIME_VERSION


def _run_loaded(
    request: dict[str, Any],
    *,
    np: Any,
    pd: Any,
    torch: Any,
    predictor: Any,
    installed_version: str,
) -> dict[str, object]:
    context = request["context"]
    frame = pd.DataFrame(
        [
            {
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            for row in context
        ]
    )
    x_timestamp = pd.Series(pd.to_datetime([row["at"] for row in context], utc=True))
    y_timestamp = pd.Series(pd.to_datetime(request["future_times"], utc=True))
    request_sha = _payload_sha256(request)
    base_seed = int(request_sha[:8], 16)
    terminal_closes: list[float] = []
    for index in range(SAMPLE_COUNT):
        seed = (base_seed + index * 104729) % (2**31 - 1)
        torch.manual_seed(seed)
        np.random.seed(seed)
        prediction = predictor.predict(
            df=frame,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=int(request["horizon_steps"]),
            T=1.0,
            top_k=0,
            top_p=0.9,
            sample_count=1,
            verbose=False,
        )
        close = float(prediction["close"].iloc[-1])
        if not isfinite(close) or close <= 0:
            raise ValueError("kronos_returned_nonfinite_or_nonpositive_price")
        terminal_closes.append(close)
    p10, p50, p90 = (
        float(value)
        for value in np.quantile(np.asarray(terminal_closes), [0.1, 0.5, 0.9])
    )
    if not p10 <= p50 <= p90:
        raise ValueError("kronos_returned_crossed_empirical_quantiles")
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
        "distribution_method": DISTRIBUTION_METHOD,
        "sample_count": SAMPLE_COUNT,
        "origin_value": float(context[-1]["close"]),
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
        np, pd, torch, predictor, version = _load_runtime()
        _write_stdout(
            _run_loaded(
                request,
                np=np,
                pd=pd,
                torch=torch,
                predictor=predictor,
                installed_version=version,
            )
        )
        return 0
    except Exception as exc:
        _write_error(exc)
        return 2


def _run_persistent() -> int:
    try:
        np, pd, torch, predictor, version = _load_runtime()
        _write_stdout(_ready_event(version))
    except Exception as exc:
        _write_error(exc)
        return 2
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            request = _validate_request(json.loads(raw))
            _write_stdout(
                _run_loaded(
                    request,
                    np=np,
                    pd=pd,
                    torch=torch,
                    predictor=predictor,
                    installed_version=version,
                )
            )
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
