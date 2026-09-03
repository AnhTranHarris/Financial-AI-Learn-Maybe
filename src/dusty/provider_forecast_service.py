from __future__ import annotations

"""Persistent research-only Chronos-2 contractor service."""

import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Sequence

from .features import FeatureBar
from .provider_forecast_adapter import (
    CHRONOS2_PROVIDER_ID,
    PROTOCOL,
    TARGET,
    ForecastEvidence,
    ProviderForecastResult,
    ProviderForecastStatus,
    _child_environment,
    _smoke_bars,
    build_chronos2_request,
    canonical_json,
    payload_sha256,
)
from .provider_process import IsolatedJsonLineWorker, ProviderWorkerState
from .provider_registry import ProviderHealth, ProviderRegistry, ProviderSnapshot


DEFAULT_STARTUP_TIMEOUT_SECONDS = 300
DEFAULT_REQUEST_TIMEOUT_SECONDS = 180


def _parse_evidence(
    snapshot: ProviderSnapshot,
    request: dict[str, object],
    response: Any,
    response_text: str,
) -> ForecastEvidence:
    if not isinstance(response, dict):
        raise TypeError("response_must_be_object")
    request_sha = payload_sha256(request)
    expected = {
        "protocol": PROTOCOL,
        "provider_id": snapshot.spec.provider_id,
        "model_id": snapshot.spec.model_id,
        "model_revision": snapshot.spec.model_revision,
        "provider_version": snapshot.spec.runtime_version,
        "request_sha256": request_sha,
        "context_sha256": request["context_sha256"],
        "as_of": request["as_of"],
        "horizon_steps": request["horizon_steps"],
        "target": TARGET,
    }
    for key, expected_value in expected.items():
        if response.get(key) != expected_value:
            raise ValueError(f"response_identity_mismatch:{key}")

    origin = response.get("origin_value")
    quantiles = response.get("quantiles")
    if not isinstance(quantiles, dict):
        raise TypeError("response_quantiles_must_be_object")
    p10, p50, p90 = (quantiles.get(key) for key in ("p10", "p50", "p90"))
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (origin, p10, p50, p90)
    ):
        raise TypeError("response_prices_must_be_numbers")
    context = request.get("context")
    if not isinstance(context, list) or not context:
        raise TypeError("request_context_missing")

    return ForecastEvidence(
        protocol=PROTOCOL,
        provider_id=snapshot.spec.provider_id,
        model_id=snapshot.spec.model_id,
        model_revision=str(snapshot.spec.model_revision),
        provider_version=str(snapshot.spec.runtime_version),
        license_id=snapshot.spec.license_id,
        symbol=str(request["symbol"]),
        timeframe=str(request["timeframe"]),
        as_of=datetime.fromisoformat(str(request["as_of"])),
        origin_at=datetime.fromisoformat(str(context[-1]["at"])),
        horizon_steps=int(request["horizon_steps"]),
        origin_value=float(origin),
        p10=float(p10),
        p50=float(p50),
        p90=float(p90),
        context_sha256=str(request["context_sha256"]),
        request_sha256=request_sha,
        response_sha256=sha256(response_text.encode("utf-8")).hexdigest(),
    )


class PersistentChronos2Worker:
    """Keeps one isolated Chronos model loaded across bounded forecast requests."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        worker_path: Path | None = None,
        startup_timeout_seconds: int = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        line_worker_factory: Callable[..., IsolatedJsonLineWorker] = IsolatedJsonLineWorker,
    ) -> None:
        self.registry = registry
        self.worker_path = worker_path or Path(__file__).with_name(
            "provider_worker_chronos2.py"
        )
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._line_worker_factory = line_worker_factory
        self._channel: IsolatedJsonLineWorker | None = None
        self._snapshot: ProviderSnapshot | None = None
        self._state = ProviderWorkerState.STOPPED
        self._last_error = ""

    @property
    def state(self) -> ProviderWorkerState:
        channel = self._channel
        if channel is not None and self._state in {
            ProviderWorkerState.STARTING,
            ProviderWorkerState.READY,
            ProviderWorkerState.BUSY,
        }:
            self._state = channel.state
        return self._state

    @property
    def pid(self) -> int | None:
        return None if self._channel is None else self._channel.pid

    @property
    def error(self) -> str:
        if self._last_error:
            return self._last_error
        return "" if self._channel is None else self._channel.stderr_excerpt

    def start(self) -> ProviderWorkerState:
        if self.state is not ProviderWorkerState.STOPPED:
            raise RuntimeError(
                f"provider_worker_start_requires_stopped:{self.state.value}"
            )
        snapshot = self.registry.snapshot(CHRONOS2_PROVIDER_ID)
        if snapshot.health is not ProviderHealth.INSTALLED:
            self._state = ProviderWorkerState.FAILED
            self._last_error = f"provider_not_installed:{snapshot.health.value}"
            return self._state
        if not self.worker_path.is_file():
            self._state = ProviderWorkerState.FAILED
            self._last_error = "provider_worker_missing"
            return self._state

        channel = self._line_worker_factory(
            (
                str(snapshot.python_executable),
                str(self.worker_path),
                "--persistent",
            ),
            environment=_child_environment(),
            startup_timeout_seconds=self.startup_timeout_seconds,
            request_timeout_seconds=self.request_timeout_seconds,
        )
        self._channel = channel
        self._snapshot = snapshot
        self._last_error = ""
        self._state = ProviderWorkerState.STARTING
        state, ready_line = channel.start()
        self._state = state
        if state is not ProviderWorkerState.READY or ready_line is None:
            self._last_error = channel.stderr_excerpt
            return self._state
        try:
            self._validate_ready_event(snapshot, json.loads(ready_line))
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._last_error = f"provider_ready_invalid:{type(exc).__name__}:{exc}"
            channel.stop()
            self._state = ProviderWorkerState.FAILED
            return self._state
        return self._state

    def forecast(
        self,
        bars: Sequence[FeatureBar],
        *,
        symbol: str,
        timeframe: str,
        horizon_steps: int,
    ) -> ProviderForecastResult:
        channel = self._channel
        snapshot = self._snapshot
        if (
            channel is None
            or snapshot is None
            or self.state is not ProviderWorkerState.READY
        ):
            return self._unavailable(f"provider_worker_not_ready:{self.state.value}")

        current = self.registry.snapshot(CHRONOS2_PROVIDER_ID)
        if (
            current.health is not ProviderHealth.INSTALLED
            or current.python_executable != snapshot.python_executable
            or current.spec != snapshot.spec
        ):
            self._last_error = "provider_worker_identity_drift"
            channel.stop()
            self._state = ProviderWorkerState.FAILED
            return self._unavailable(self._last_error)

        request = build_chronos2_request(
            bars,
            symbol=symbol,
            timeframe=timeframe,
            horizon_steps=horizon_steps,
            snapshot=snapshot,
        )
        self._state = ProviderWorkerState.BUSY
        state, response_line = channel.transact(canonical_json(request))
        self._state = state
        if state is not ProviderWorkerState.READY or response_line is None:
            self._last_error = (
                f"provider_request_failed:{state.value}:{channel.stderr_excerpt}"
            )
            return self._unavailable(self._last_error)
        try:
            response = json.loads(response_line)
            evidence = _parse_evidence(snapshot, request, response, response_line)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._last_error = f"provider_response_invalid:{type(exc).__name__}:{exc}"
            channel.stop()
            self._state = ProviderWorkerState.FAILED
            return self._unavailable(self._last_error)
        return ProviderForecastResult(
            provider_id=CHRONOS2_PROVIDER_ID,
            status=ProviderForecastStatus.AVAILABLE,
            evidence=evidence,
        )

    def stop(self) -> ProviderWorkerState:
        if self._channel is not None:
            self._channel.stop()
        self._channel = None
        self._snapshot = None
        self._state = ProviderWorkerState.STOPPED
        return self._state

    def restart(self) -> ProviderWorkerState:
        self.stop()
        return self.start()

    @staticmethod
    def _validate_ready_event(snapshot: ProviderSnapshot, event: Any) -> None:
        if not isinstance(event, dict):
            raise TypeError("provider_ready_event_must_be_object")
        expected = {
            "event": "ready",
            "protocol": PROTOCOL,
            "provider_id": snapshot.spec.provider_id,
            "model_id": snapshot.spec.model_id,
            "model_revision": snapshot.spec.model_revision,
            "provider_version": snapshot.spec.runtime_version,
        }
        if set(event) != set(expected):
            raise ValueError("provider_ready_event_schema_mismatch")
        for key, expected_value in expected.items():
            if event.get(key) != expected_value:
                raise ValueError(
                    f"provider_ready_event_identity_mismatch:{key}"
                )

    @staticmethod
    def _unavailable(error: str) -> ProviderForecastResult:
        return ProviderForecastResult(
            provider_id=CHRONOS2_PROVIDER_ID,
            status=ProviderForecastStatus.UNAVAILABLE,
            error=error,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dusty persistent isolated Chronos-2 smoke test"
    )
    parser.add_argument("--provider-root", type=Path, required=True)
    parser.add_argument("--smoke-test", action="store_true")
    parser.add_argument("--count", type=int, default=2)
    parser.add_argument(
        "--startup-timeout",
        type=int,
        default=DEFAULT_STARTUP_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--request-timeout",
        type=int,
        default=DEFAULT_REQUEST_TIMEOUT_SECONDS,
    )
    args = parser.parse_args(argv)
    if not args.smoke_test:
        parser.error("M113 exposes only --smoke-test from the command line")
    if not 1 <= args.count <= 5:
        parser.error("--count must be 1 to 5")

    worker = PersistentChronos2Worker(
        ProviderRegistry(args.provider_root),
        startup_timeout_seconds=args.startup_timeout,
        request_timeout_seconds=args.request_timeout,
    )
    started = perf_counter()
    state = worker.start()
    print(
        canonical_json(
            {
                "event": "startup",
                "provider_id": CHRONOS2_PROVIDER_ID,
                "state": state.value,
                "startup_seconds": round(perf_counter() - started, 3),
                "pid": worker.pid,
            }
        ),
        flush=True,
    )
    if state is not ProviderWorkerState.READY:
        print(
            canonical_json(
                {"event": "failure", "state": state.value, "error": worker.error}
            ),
            flush=True,
        )
        worker.stop()
        return 2

    exit_code = 0
    try:
        for iteration in range(1, args.count + 1):
            started = perf_counter()
            result = worker.forecast(
                _smoke_bars(),
                symbol="EURUSD",
                timeframe="M15",
                horizon_steps=16,
            )
            print(
                canonical_json(
                    {
                        "event": "forecast",
                        "iteration": iteration,
                        "elapsed_seconds": round(perf_counter() - started, 3),
                        "state": worker.state.value,
                        "result": result.as_dict(),
                    }
                ),
                flush=True,
            )
            if not result.available:
                exit_code = 2
                break
    finally:
        print(
            canonical_json(
                {
                    "event": "shutdown",
                    "provider_id": CHRONOS2_PROVIDER_ID,
                    "state": worker.stop().value,
                }
            ),
            flush=True,
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
