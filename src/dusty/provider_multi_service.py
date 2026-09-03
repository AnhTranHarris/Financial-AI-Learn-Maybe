from __future__ import annotations

"""Persistent multi-provider research manager; no provider receives trade authority."""

from datetime import datetime
from enum import StrEnum
import json
from pathlib import Path
from typing import Any, Callable, Sequence

from .features import FeatureBar
from .provider_forecast_adapter import (
    CHRONOS2_PROVIDER_ID,
    PROTOCOL,
    ProviderForecastResult,
    _child_environment,
    canonical_json,
)
from .provider_forecast_service import PersistentChronos2Worker
from .provider_multi_contract import (
    CHRONOS_DISTRIBUTION,
    KRONOS_DISTRIBUTION,
    KRONOS_PROVIDER_ID,
    TIMESFM_DISTRIBUTION,
    TIMESFM25_PROVIDER_ID,
    ContractorForecastResult,
    ForecastProvenance,
    build_kronos_request,
    build_timesfm25_request,
    parse_provider_response,
    unavailable_result,
)
from .provider_process import IsolatedJsonLineWorker, ProviderWorkerState
from .provider_registry import ProviderHealth, ProviderRegistry, ProviderSnapshot


DEFAULT_STARTUP_TIMEOUT_SECONDS = 300
DEFAULT_REQUEST_TIMEOUT_SECONDS = 240


class ForecastSelectionMode(StrEnum):
    CHRONOS2 = CHRONOS2_PROVIDER_ID
    KRONOS_SMALL = KRONOS_PROVIDER_ID
    TIMESFM25 = TIMESFM25_PROVIDER_ID
    ALL_THREE = "all-three"


_SELECTION_IDS = {
    ForecastSelectionMode.CHRONOS2: (CHRONOS2_PROVIDER_ID,),
    ForecastSelectionMode.KRONOS_SMALL: (KRONOS_PROVIDER_ID,),
    ForecastSelectionMode.TIMESFM25: (TIMESFM25_PROVIDER_ID,),
    ForecastSelectionMode.ALL_THREE: (
        CHRONOS2_PROVIDER_ID,
        KRONOS_PROVIDER_ID,
        TIMESFM25_PROVIDER_ID,
    ),
}


def selection_provider_ids(mode: ForecastSelectionMode) -> tuple[str, ...]:
    return _SELECTION_IDS[mode]


class _PersistentExternalForecastWorker:
    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        provider_id: str,
        worker_filename: str,
        distribution_method: str,
        sample_count: int,
        request_builder: Callable[..., dict[str, object]],
        worker_path: Path | None = None,
        startup_timeout_seconds: int = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        request_timeout_seconds: int = DEFAULT_REQUEST_TIMEOUT_SECONDS,
        line_worker_factory: Callable[..., IsolatedJsonLineWorker] = IsolatedJsonLineWorker,
    ) -> None:
        self.registry = registry
        self.provider_id = provider_id
        self.worker_path = worker_path or Path(__file__).with_name(worker_filename)
        self.distribution_method = distribution_method
        self.sample_count = sample_count
        self.request_builder = request_builder
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self._line_worker_factory = line_worker_factory
        self._channel: IsolatedJsonLineWorker | None = None
        self._snapshot: ProviderSnapshot | None = None
        self._state = ProviderWorkerState.STOPPED
        self._last_error = ""

    @property
    def state(self) -> ProviderWorkerState:
        if self._channel is not None and self._state in {
            ProviderWorkerState.STARTING,
            ProviderWorkerState.READY,
            ProviderWorkerState.BUSY,
        }:
            self._state = self._channel.state
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
        snapshot = self.registry.snapshot(self.provider_id)
        if snapshot.health is not ProviderHealth.INSTALLED:
            self._state = ProviderWorkerState.FAILED
            self._last_error = f"provider_not_installed:{snapshot.health.value}"
            return self._state
        if not self.worker_path.is_file():
            self._state = ProviderWorkerState.FAILED
            self._last_error = "provider_worker_missing"
            return self._state
        environment = _child_environment()
        environment["DUSTY_PROVIDER_DIRECTORY"] = str(snapshot.root)
        channel = self._line_worker_factory(
            (
                str(snapshot.python_executable),
                str(self.worker_path),
                "--persistent",
            ),
            environment=environment,
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

    def forecast(
        self,
        bars: Sequence[FeatureBar],
        *,
        symbol: str,
        timeframe: str,
        horizon_steps: int,
        future_times: Sequence[datetime] | None = None,
    ) -> ContractorForecastResult:
        channel = self._channel
        snapshot = self._snapshot
        if channel is None or snapshot is None or self.state is not ProviderWorkerState.READY:
            return unavailable_result(
                self.provider_id,
                f"provider_worker_not_ready:{self.state.value}",
                distribution_method=self.distribution_method,
                sample_count=self.sample_count,
            )
        current = self.registry.snapshot(self.provider_id)
        if (
            current.health is not ProviderHealth.INSTALLED
            or current.python_executable != snapshot.python_executable
            or current.spec != snapshot.spec
        ):
            self._last_error = "provider_worker_identity_drift"
            channel.stop()
            self._state = ProviderWorkerState.FAILED
            return unavailable_result(
                self.provider_id,
                self._last_error,
                distribution_method=self.distribution_method,
                sample_count=self.sample_count,
            )
        kwargs: dict[str, Any] = {
            "symbol": symbol,
            "timeframe": timeframe,
            "horizon_steps": horizon_steps,
            "snapshot": snapshot,
        }
        if self.provider_id == KRONOS_PROVIDER_ID:
            if future_times is None:
                return unavailable_result(
                    self.provider_id,
                    "kronos_future_schedule_required",
                    distribution_method=self.distribution_method,
                    sample_count=self.sample_count,
                )
            kwargs["future_times"] = future_times
        request = self.request_builder(bars, **kwargs)
        self._state = ProviderWorkerState.BUSY
        state, response_line = channel.transact(canonical_json(request))
        self._state = state
        if state is not ProviderWorkerState.READY or response_line is None:
            self._last_error = (
                f"provider_request_failed:{state.value}:{channel.stderr_excerpt}"
            )
            return unavailable_result(
                self.provider_id,
                self._last_error,
                distribution_method=self.distribution_method,
                sample_count=self.sample_count,
            )
        try:
            return parse_provider_response(
                snapshot,
                request,
                response_line,
                distribution_method=self.distribution_method,
                sample_count=self.sample_count,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            self._last_error = f"provider_response_invalid:{type(exc).__name__}:{exc}"
            channel.stop()
            self._state = ProviderWorkerState.FAILED
            return unavailable_result(
                self.provider_id,
                self._last_error,
                distribution_method=self.distribution_method,
                sample_count=self.sample_count,
            )

    def stop(self) -> ProviderWorkerState:
        if self._channel is not None:
            self._channel.stop()
        self._channel = None
        self._snapshot = None
        self._state = ProviderWorkerState.STOPPED
        return self._state

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
                raise ValueError(f"provider_ready_event_identity_mismatch:{key}")


class PersistentKronosSmallWorker(_PersistentExternalForecastWorker):
    def __init__(self, registry: ProviderRegistry, **kwargs: Any) -> None:
        super().__init__(
            registry,
            provider_id=KRONOS_PROVIDER_ID,
            worker_filename="provider_worker_kronos.py",
            distribution_method=KRONOS_DISTRIBUTION,
            sample_count=5,
            request_builder=build_kronos_request,
            **kwargs,
        )


class PersistentTimesFM25Worker(_PersistentExternalForecastWorker):
    def __init__(self, registry: ProviderRegistry, **kwargs: Any) -> None:
        super().__init__(
            registry,
            provider_id=TIMESFM25_PROVIDER_ID,
            worker_filename="provider_worker_timesfm25.py",
            distribution_method=TIMESFM_DISTRIBUTION,
            sample_count=1,
            request_builder=build_timesfm25_request,
            **kwargs,
        )


class ForecastContractorManager:
    """Owns optional forecast workers and preserves separate evidence streams."""

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        chronos: PersistentChronos2Worker | None = None,
        kronos: PersistentKronosSmallWorker | None = None,
        timesfm: PersistentTimesFM25Worker | None = None,
    ) -> None:
        self.registry = registry
        self._workers: dict[str, Any] = {
            CHRONOS2_PROVIDER_ID: chronos or PersistentChronos2Worker(registry),
            KRONOS_PROVIDER_ID: kronos or PersistentKronosSmallWorker(registry),
            TIMESFM25_PROVIDER_ID: timesfm or PersistentTimesFM25Worker(registry),
        }
        self._selection = ForecastSelectionMode.CHRONOS2

    @property
    def selection(self) -> ForecastSelectionMode:
        return self._selection

    @property
    def selected_provider_ids(self) -> tuple[str, ...]:
        return selection_provider_ids(self._selection)

    def select(self, mode: ForecastSelectionMode | str) -> ForecastSelectionMode:
        selected = ForecastSelectionMode(mode)
        ids = selection_provider_ids(selected)
        snapshots = {row.spec.provider_id: row for row in self.registry.discover()}
        for provider_id in ids:
            snapshot = snapshots.get(provider_id)
            if snapshot is None or not snapshot.selectable:
                raise ValueError(f"forecast_provider_not_installed:{provider_id}")
        self._selection = selected
        return selected

    def states(self) -> dict[str, ProviderWorkerState]:
        return {provider_id: worker.state for provider_id, worker in self._workers.items()}

    def pids(self) -> dict[str, int | None]:
        return {provider_id: worker.pid for provider_id, worker in self._workers.items()}

    def start_selected(self) -> dict[str, ProviderWorkerState]:
        result: dict[str, ProviderWorkerState] = {}
        for provider_id in self.selected_provider_ids:
            worker = self._workers[provider_id]
            if worker.state is ProviderWorkerState.STOPPED:
                result[provider_id] = worker.start()
            else:
                result[provider_id] = worker.state
        return result

    def forecast_selected(
        self,
        bars: Sequence[FeatureBar],
        *,
        symbol: str,
        timeframe: str,
        horizon_steps: int,
        future_times: Sequence[datetime] | None = None,
    ) -> tuple[ContractorForecastResult, ...]:
        results: list[ContractorForecastResult] = []
        for provider_id in self.selected_provider_ids:
            worker = self._workers[provider_id]
            if provider_id == CHRONOS2_PROVIDER_ID:
                raw: ProviderForecastResult = worker.forecast(
                    bars,
                    symbol=symbol,
                    timeframe=timeframe,
                    horizon_steps=horizon_steps,
                )
                results.append(
                    ContractorForecastResult(
                        raw,
                        ForecastProvenance(
                            provider_id=CHRONOS2_PROVIDER_ID,
                            distribution_method=CHRONOS_DISTRIBUTION,
                            sample_count=1,
                        ),
                    )
                )
            else:
                results.append(
                    worker.forecast(
                        bars,
                        symbol=symbol,
                        timeframe=timeframe,
                        horizon_steps=horizon_steps,
                        future_times=future_times,
                    )
                )
        return tuple(results)

    def stop_all(self) -> dict[str, ProviderWorkerState]:
        return {
            provider_id: worker.stop()
            for provider_id, worker in self._workers.items()
        }
