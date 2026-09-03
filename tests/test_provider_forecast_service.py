from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from dusty.provider_forecast_adapter import (
    CHRONOS2_PROVIDER_ID,
    PROTOCOL,
    TARGET,
    _smoke_bars,
    canonical_json,
    payload_sha256,
)
from dusty.provider_forecast_service import (
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    PersistentChronos2Worker,
)
from dusty.provider_process import ProviderWorkerState
from dusty.provider_registry import ProviderRegistry


def installed_registry(root: Path) -> ProviderRegistry:
    scripts = root / "Chronos2" / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").touch()
    return ProviderRegistry(root)


def ready_event(registry: ProviderRegistry) -> str:
    snapshot = registry.snapshot(CHRONOS2_PROVIDER_ID)
    return canonical_json(
        {
            "event": "ready",
            "protocol": PROTOCOL,
            "provider_id": snapshot.spec.provider_id,
            "model_id": snapshot.spec.model_id,
            "model_revision": snapshot.spec.model_revision,
            "provider_version": snapshot.spec.runtime_version,
        }
    )


def response_for(request: dict[str, object]) -> str:
    origin = float(request["context"][-1]["close"])
    return canonical_json(
        {
            "protocol": PROTOCOL,
            "provider_id": CHRONOS2_PROVIDER_ID,
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "provider_version": request["runtime_version"],
            "request_sha256": payload_sha256(request),
            "context_sha256": request["context_sha256"],
            "as_of": request["as_of"],
            "horizon_steps": request["horizon_steps"],
            "target": TARGET,
            "origin_value": origin,
            "quantiles": {
                "p10": origin * 0.99,
                "p50": origin * 1.01,
                "p90": origin * 1.03,
            },
        }
    )


class FakeLineWorker:
    def __init__(
        self,
        command,
        *,
        environment,
        startup_timeout_seconds,
        request_timeout_seconds,
        ready_line,
        start_state=ProviderWorkerState.READY,
    ):
        self.command = tuple(command)
        self.environment = dict(environment)
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.ready_line = ready_line
        self.start_state = start_state
        self.state = ProviderWorkerState.STOPPED
        self.pid = 4242
        self.stderr_excerpt = "no_provider_error_text"
        self.requests = []

    def start(self):
        self.state = self.start_state
        if self.state is ProviderWorkerState.READY:
            return self.state, self.ready_line
        return self.state, None

    def transact(self, payload):
        self.requests.append(payload)
        if self.state is not ProviderWorkerState.READY:
            return self.state, None
        import json

        request = json.loads(payload)
        return ProviderWorkerState.READY, response_for(request)

    def stop(self):
        self.state = ProviderWorkerState.STOPPED
        return self.state


class PersistentChronosServiceTests(unittest.TestCase):
    def test_two_forecasts_reuse_one_worker_and_keep_zero_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            worker_path = root / "worker.py"
            worker_path.touch()
            created = []

            def factory(command, **kwargs):
                instance = FakeLineWorker(
                    command,
                    **kwargs,
                    ready_line=ready_event(registry),
                )
                created.append(instance)
                return instance

            service = PersistentChronos2Worker(
                registry,
                worker_path=worker_path,
                line_worker_factory=factory,
            )
            self.assertIs(service.start(), ProviderWorkerState.READY)
            self.assertEqual(service.pid, 4242)
            first = service.forecast(
                _smoke_bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            second = service.forecast(
                _smoke_bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            self.assertTrue(first.available)
            self.assertTrue(second.available)
            self.assertEqual(len(created), 1)
            self.assertEqual(len(created[0].requests), 2)
            self.assertEqual(
                created[0].startup_timeout_seconds,
                DEFAULT_STARTUP_TIMEOUT_SECONDS,
            )
            self.assertEqual(
                created[0].request_timeout_seconds,
                DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
            self.assertEqual(created[0].command[-1], "--persistent")
            self.assertEqual(created[0].environment["HF_HUB_OFFLINE"], "1")
            self.assertEqual(created[0].environment["CUDA_VISIBLE_DEVICES"], "")
            for result in (first, second):
                evidence = result.evidence
                self.assertIsNotNone(evidence)
                self.assertFalse(evidence.broker_write_authority)
                self.assertFalse(evidence.promotion_authority)
                self.assertFalse(evidence.entry_veto_authority)
            self.assertIs(service.stop(), ProviderWorkerState.STOPPED)

    def test_default_startup_budget_reflects_windows_cold_start_measurement(self):
        self.assertEqual(DEFAULT_STARTUP_TIMEOUT_SECONDS, 300)
        self.assertEqual(DEFAULT_REQUEST_TIMEOUT_SECONDS, 180)

    def test_ready_identity_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            worker_path = root / "worker.py"
            worker_path.touch()
            bad = ready_event(registry).replace(
                "amazon/chronos-2", "unexpected/model"
            )

            def factory(command, **kwargs):
                return FakeLineWorker(command, **kwargs, ready_line=bad)

            service = PersistentChronos2Worker(
                registry,
                worker_path=worker_path,
                line_worker_factory=factory,
            )
            self.assertIs(service.start(), ProviderWorkerState.FAILED)
            self.assertIn("provider_ready_invalid", service.error)
            self.assertIs(service.stop(), ProviderWorkerState.STOPPED)

    def test_provider_identity_drift_stops_worker_before_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            worker_path = root / "worker.py"
            worker_path.touch()
            created = []

            def factory(command, **kwargs):
                instance = FakeLineWorker(
                    command,
                    **kwargs,
                    ready_line=ready_event(registry),
                )
                created.append(instance)
                return instance

            service = PersistentChronos2Worker(
                registry,
                worker_path=worker_path,
                line_worker_factory=factory,
            )
            self.assertIs(service.start(), ProviderWorkerState.READY)
            registry.snapshot(CHRONOS2_PROVIDER_ID).python_executable.unlink()
            result = service.forecast(
                _smoke_bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            self.assertFalse(result.available)
            self.assertIn("provider_worker_identity_drift", result.error)
            self.assertEqual(len(created[0].requests), 0)
            self.assertIs(service.state, ProviderWorkerState.FAILED)

    def test_resource_blocked_start_is_visible_and_not_retried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            worker_path = root / "worker.py"
            worker_path.touch()
            created = []

            def factory(command, **kwargs):
                instance = FakeLineWorker(
                    command,
                    **kwargs,
                    ready_line=None,
                    start_state=ProviderWorkerState.RESOURCE_BLOCKED,
                )
                instance.stderr_excerpt = "MemoryError: out of memory"
                created.append(instance)
                return instance

            service = PersistentChronos2Worker(
                registry,
                worker_path=worker_path,
                line_worker_factory=factory,
            )
            self.assertIs(service.start(), ProviderWorkerState.RESOURCE_BLOCKED)
            self.assertEqual(len(created), 1)
            self.assertIn("out of memory", service.error)
            result = service.forecast(
                _smoke_bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            self.assertFalse(result.available)
            self.assertIn("provider_worker_not_ready", result.error)
            self.assertEqual(len(created), 1)
            service.stop()


if __name__ == "__main__":
    unittest.main()
