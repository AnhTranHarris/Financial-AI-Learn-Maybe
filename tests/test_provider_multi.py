from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

from dusty.provider_forecast_adapter import (
    CHRONOS2_PROVIDER_ID,
    PROTOCOL,
    TARGET,
    ProviderForecastResult,
    ProviderForecastStatus,
    _smoke_bars,
    canonical_json,
    payload_sha256,
)
from dusty.provider_multi_contract import (
    KRONOS_DISTRIBUTION,
    KRONOS_PROVIDER_ID,
    TIMESFM_DISTRIBUTION,
    TIMESFM25_PROVIDER_ID,
    ForecastProvenance,
    ContractorForecastResult,
    build_kronos_request,
    build_timesfm25_request,
)
from dusty.provider_multi_service import (
    ForecastContractorManager,
    ForecastSelectionMode,
    PersistentKronosSmallWorker,
    PersistentTimesFM25Worker,
    selection_provider_ids,
)
from dusty.provider_process import ProviderWorkerState
from dusty.provider_registry import ProviderRegistry


def installed_registry(root: Path) -> ProviderRegistry:
    for directory in ("Chronos2", "Kronos", "TimesFM25"):
        scripts = root / directory / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        (scripts / "python.exe").touch()
    return ProviderRegistry(root)


def future_times(count: int = 16):
    origin = _smoke_bars()[-1].at
    return tuple(origin + timedelta(minutes=15 * (index + 1)) for index in range(count))


def ready_event(registry: ProviderRegistry, provider_id: str) -> str:
    snapshot = registry.snapshot(provider_id)
    return canonical_json(
        {
            "event": "ready",
            "protocol": PROTOCOL,
            "provider_id": provider_id,
            "model_id": snapshot.spec.model_id,
            "model_revision": snapshot.spec.model_revision,
            "provider_version": snapshot.spec.runtime_version,
        }
    )


def response_for(request: dict[str, object]) -> str:
    origin = float(request["context"][-1]["close"])
    method = str(request["distribution_method"])
    samples = int(request.get("sample_count", 1))
    return canonical_json(
        {
            "protocol": PROTOCOL,
            "provider_id": request["provider_id"],
            "model_id": request["model_id"],
            "model_revision": request["model_revision"],
            "provider_version": request["runtime_version"],
            "request_sha256": payload_sha256(request),
            "context_sha256": request["context_sha256"],
            "as_of": request["as_of"],
            "horizon_steps": request["horizon_steps"],
            "target": TARGET,
            "distribution_method": method,
            "sample_count": samples,
            "origin_value": origin,
            "quantiles": {
                "p10": origin * 0.99,
                "p50": origin * 1.01,
                "p90": origin * 1.03,
            },
        }
    )


class FakeLineWorker:
    def __init__(self, command, *, environment, startup_timeout_seconds, request_timeout_seconds, ready_line):
        self.command = tuple(command)
        self.environment = dict(environment)
        self.startup_timeout_seconds = startup_timeout_seconds
        self.request_timeout_seconds = request_timeout_seconds
        self.ready_line = ready_line
        self.state = ProviderWorkerState.STOPPED
        self.pid = 5151
        self.stderr_excerpt = "no_provider_error_text"
        self.requests = []

    def start(self):
        self.state = ProviderWorkerState.READY
        return self.state, self.ready_line

    def transact(self, payload):
        import json

        self.requests.append(payload)
        return ProviderWorkerState.READY, response_for(json.loads(payload))

    def stop(self):
        self.state = ProviderWorkerState.STOPPED
        return self.state


class MultiProviderContractTests(unittest.TestCase):
    def test_timesfm_request_is_close_only_and_pinned(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = installed_registry(Path(temporary))
            request = build_timesfm25_request(
                _smoke_bars(),
                symbol="eurusd",
                timeframe="m15",
                horizon_steps=16,
                snapshot=registry.snapshot(TIMESFM25_PROVIDER_ID),
            )
            self.assertEqual(request["provider_id"], TIMESFM25_PROVIDER_ID)
            self.assertEqual(request["distribution_method"], TIMESFM_DISTRIBUTION)
            self.assertEqual(set(request["context"][0]), {"at", "close"})
            self.assertEqual(len(request["model_revision"]), 40)

    def test_kronos_request_uses_completed_ohlcv_and_explicit_future_schedule(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = installed_registry(Path(temporary))
            snapshot = registry.snapshot(KRONOS_PROVIDER_ID)
            request = build_kronos_request(
                _smoke_bars(),
                symbol="EURUSD",
                timeframe="M15",
                horizon_steps=16,
                snapshot=snapshot,
                future_times=future_times(),
            )
            self.assertEqual(request["distribution_method"], KRONOS_DISTRIBUTION)
            self.assertEqual(request["sample_count"], 5)
            self.assertEqual(
                set(request["context"][0]),
                {"at", "open", "high", "low", "close", "volume"},
            )
            self.assertEqual(len(request["future_times"]), 16)
            self.assertNotIn("execution_price", request["context"][0])
            self.assertNotIn("spread_points", request["context"][0])

    def test_kronos_rejects_missing_or_nonfuture_schedule(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = installed_registry(Path(temporary))
            snapshot = registry.snapshot(KRONOS_PROVIDER_ID)
            with self.assertRaisesRegex(ValueError, "must_match_horizon"):
                build_kronos_request(
                    _smoke_bars(),
                    symbol="EURUSD",
                    timeframe="M15",
                    horizon_steps=16,
                    snapshot=snapshot,
                    future_times=(),
                )
            invalid = list(future_times())
            invalid[0] = _smoke_bars()[-1].at
            with self.assertRaisesRegex(ValueError, "strictly_after_context"):
                build_kronos_request(
                    _smoke_bars(),
                    symbol="EURUSD",
                    timeframe="M15",
                    horizon_steps=16,
                    snapshot=snapshot,
                    future_times=invalid,
                )


class PersistentExternalProviderTests(unittest.TestCase):
    def _service(self, provider_id: str):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        registry = installed_registry(root)
        worker_path = root / "worker.py"
        worker_path.touch()
        created = []

        def factory(command, **kwargs):
            instance = FakeLineWorker(
                command,
                **kwargs,
                ready_line=ready_event(registry, provider_id),
            )
            created.append(instance)
            return instance

        cls = PersistentTimesFM25Worker if provider_id == TIMESFM25_PROVIDER_ID else PersistentKronosSmallWorker
        service = cls(
            registry,
            worker_path=worker_path,
            line_worker_factory=factory,
        )
        return temporary, service, created

    def test_timesfm_reuses_worker_and_preserves_zero_authority(self):
        temporary, service, created = self._service(TIMESFM25_PROVIDER_ID)
        with temporary:
            self.assertIs(service.start(), ProviderWorkerState.READY)
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
            self.assertIn("DUSTY_PROVIDER_DIRECTORY", created[0].environment)
            evidence = first.result.evidence
            self.assertIsNotNone(evidence)
            self.assertFalse(evidence.broker_write_authority)
            self.assertFalse(evidence.entry_veto_authority)
            self.assertFalse(evidence.promotion_authority)
            self.assertEqual(first.provenance.distribution_method, TIMESFM_DISTRIBUTION)

    def test_kronos_requires_future_schedule_before_request(self):
        temporary, service, created = self._service(KRONOS_PROVIDER_ID)
        with temporary:
            self.assertIs(service.start(), ProviderWorkerState.READY)
            missing = service.forecast(
                _smoke_bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            self.assertFalse(missing.available)
            self.assertIn("future_schedule_required", missing.result.error)
            self.assertEqual(len(created[0].requests), 0)
            available = service.forecast(
                _smoke_bars(),
                symbol="EURUSD",
                timeframe="M15",
                horizon_steps=16,
                future_times=future_times(),
            )
            self.assertTrue(available.available)
            self.assertEqual(available.provenance.sample_count, 5)
            self.assertEqual(len(created[0].requests), 1)


class StubWorker:
    def __init__(self, provider_id: str):
        self.provider_id = provider_id
        self.state = ProviderWorkerState.STOPPED
        self.pid = None
        self.forecast_count = 0

    def start(self):
        self.state = ProviderWorkerState.READY
        self.pid = hash(self.provider_id) & 0xFFFF
        return self.state

    def stop(self):
        self.state = ProviderWorkerState.STOPPED
        self.pid = None
        return self.state

    def forecast(self, *args, **kwargs):
        self.forecast_count += 1
        if self.provider_id == CHRONOS2_PROVIDER_ID:
            return ProviderForecastResult(
                provider_id=self.provider_id,
                status=ProviderForecastStatus.UNAVAILABLE,
                error="fixture_no_model",
            )
        method = KRONOS_DISTRIBUTION if self.provider_id == KRONOS_PROVIDER_ID else TIMESFM_DISTRIBUTION
        return ContractorForecastResult(
            ProviderForecastResult(
                provider_id=self.provider_id,
                status=ProviderForecastStatus.UNAVAILABLE,
                error="fixture_no_model",
            ),
            ForecastProvenance(self.provider_id, method, 5 if self.provider_id == KRONOS_PROVIDER_ID else 1),
        )


class ForecastContractorManagerTests(unittest.TestCase):
    def test_single_or_all_three_selection_is_explicit_and_no_ensemble_is_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = installed_registry(Path(temporary))
            workers = {provider_id: StubWorker(provider_id) for provider_id in (
                CHRONOS2_PROVIDER_ID,
                KRONOS_PROVIDER_ID,
                TIMESFM25_PROVIDER_ID,
            )}
            manager = ForecastContractorManager(
                registry,
                chronos=workers[CHRONOS2_PROVIDER_ID],
                kronos=workers[KRONOS_PROVIDER_ID],
                timesfm=workers[TIMESFM25_PROVIDER_ID],
            )
            manager.select(ForecastSelectionMode.TIMESFM25)
            self.assertEqual(manager.selected_provider_ids, (TIMESFM25_PROVIDER_ID,))
            manager.select(ForecastSelectionMode.ALL_THREE)
            self.assertEqual(
                manager.selected_provider_ids,
                (CHRONOS2_PROVIDER_ID, KRONOS_PROVIDER_ID, TIMESFM25_PROVIDER_ID),
            )
            states = manager.start_selected()
            self.assertTrue(all(state is ProviderWorkerState.READY for state in states.values()))
            results = manager.forecast_selected(
                _smoke_bars(),
                symbol="EURUSD",
                timeframe="M15",
                horizon_steps=16,
                future_times=future_times(),
            )
            self.assertEqual(tuple(row.result.provider_id for row in results), manager.selected_provider_ids)
            self.assertEqual(len(results), 3)
            self.assertTrue(all(worker.forecast_count == 1 for worker in workers.values()))
            manager.stop_all()

    def test_all_three_selection_fails_closed_if_any_selected_provider_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            (root / "TimesFM25" / ".venv" / "Scripts" / "python.exe").unlink()
            manager = ForecastContractorManager(
                registry,
                chronos=StubWorker(CHRONOS2_PROVIDER_ID),
                kronos=StubWorker(KRONOS_PROVIDER_ID),
                timesfm=StubWorker(TIMESFM25_PROVIDER_ID),
            )
            with self.assertRaisesRegex(ValueError, "timesfm-2.5"):
                manager.select(ForecastSelectionMode.ALL_THREE)

    def test_selection_mapping_has_no_duplicate_or_hidden_provider(self):
        all_ids = selection_provider_ids(ForecastSelectionMode.ALL_THREE)
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertEqual(set(all_ids), {
            CHRONOS2_PROVIDER_ID,
            KRONOS_PROVIDER_ID,
            TIMESFM25_PROVIDER_ID,
        })


if __name__ == "__main__":
    unittest.main()
