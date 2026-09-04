from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from dusty.features import FeatureBar
from dusty.provider_forecast_adapter import (
    CHRONOS2_PROVIDER_ID,
    PROTOCOL,
    TARGET,
    Chronos2ForecastAdapter,
    ProviderForecastStatus,
    _child_environment,
    build_chronos2_request,
    canonical_json,
    payload_sha256,
)
from dusty.provider_registry import ProviderRegistry
from dusty.provider_worker_chronos2 import _validate_request


START = datetime(2026, 8, 3, 0, 15, tzinfo=timezone.utc)


def bars(count: int = 64) -> tuple[FeatureBar, ...]:
    rows = []
    for index in range(count):
        close = 1.1000 + index * 0.0001
        rows.append(
            FeatureBar(
                at=START + timedelta(minutes=15 * index),
                open=close,
                high=close + 0.0002,
                low=close - 0.0002,
                close=close,
                spread_points=8.0,
                tick_volume=100 + index,
                execution_price=close + 0.00001,
                decision_spread_proxy_points=9.0,
            )
        )
    return tuple(rows)


def installed_registry(root: Path) -> ProviderRegistry:
    scripts = root / "Chronos2" / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").touch()
    return ProviderRegistry(root)


def response_for(request: dict[str, object]) -> dict[str, object]:
    origin = float(request["context"][-1]["close"])
    return {
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


class IsolatedChronosAdapterTests(unittest.TestCase):
    def test_request_contains_only_completed_time_and_close_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = installed_registry(Path(temporary))
            snapshot = registry.snapshot(CHRONOS2_PROVIDER_ID)
            request = build_chronos2_request(
                bars(), symbol="eurusd", timeframe="m15", horizon_steps=16, snapshot=snapshot
            )
            self.assertEqual(request["symbol"], "EURUSD")
            self.assertEqual(request["timeframe"], "M15")
            self.assertEqual(request["as_of"], bars()[-1].at.isoformat())
            self.assertEqual(set(request["context"][0]), {"at", "close"})
            rendered = canonical_json(request)
            self.assertNotIn("execution_price", rendered)
            self.assertNotIn("spread", rendered)
            self.assertNotIn("tick_volume", rendered)
            self.assertNotIn("account", rendered)
            self.assertNotIn("order", rendered)

    def test_adapter_returns_hashed_immutable_research_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            worker = root / "worker.py"
            worker.touch()
            calls = []

            def runner(command, **kwargs):
                request = json.loads(kwargs["input"])
                calls.append((command, kwargs, request))
                response = canonical_json(response_for(request))
                return subprocess.CompletedProcess(command, 0, response, "")

            result = Chronos2ForecastAdapter(registry, worker_path=worker, runner=runner).forecast(
                bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            self.assertIs(result.status, ProviderForecastStatus.AVAILABLE)
            self.assertTrue(result.available)
            evidence = result.evidence
            self.assertIsNotNone(evidence)
            self.assertEqual(evidence.provider_id, CHRONOS2_PROVIDER_ID)
            self.assertEqual(evidence.target, TARGET)
            self.assertEqual(evidence.context_sha256, calls[0][2]["context_sha256"])
            self.assertEqual(evidence.request_sha256, payload_sha256(calls[0][2]))
            self.assertEqual(len(evidence.response_sha256), 64)
            self.assertFalse(evidence.broker_write_authority)
            self.assertFalse(evidence.promotion_authority)
            self.assertFalse(evidence.entry_veto_authority)
            self.assertGreater(evidence.predicted_return_p50, 0)
            self.assertEqual(len(evidence.fingerprint), 64)
            self.assertEqual(calls[0][0][0], str(registry.snapshot(CHRONOS2_PROVIDER_ID).python_executable))
            self.assertEqual(calls[0][0][1], str(worker))
            self.assertEqual(calls[0][1]["timeout"], 180)
            self.assertEqual(calls[0][1]["encoding"], "utf-8")
            self.assertEqual(calls[0][1]["errors"], "replace")
            self.assertEqual(calls[0][1]["env"]["HF_HUB_OFFLINE"], "1")
            self.assertEqual(calls[0][1]["env"]["CUDA_VISIBLE_DEVICES"], "")

    def test_child_environment_does_not_forward_credentials(self):
        source = {
            "SystemRoot": "C:/Windows",
            "USERPROFILE": "C:/Users/test",
            "TEMP": "C:/Temp",
            "OPENAI_API_KEY": "secret",
            "HF_TOKEN": "secret",
            "MT5_PASSWORD": "secret",
            "BROKER_LOGIN": "secret",
        }
        environment = _child_environment(source)
        self.assertEqual(environment["SystemRoot"], "C:/Windows")
        self.assertEqual(environment["USERPROFILE"], "C:/Users/test")
        for key in ("OPENAI_API_KEY", "HF_TOKEN", "MT5_PASSWORD", "BROKER_LOGIN"):
            self.assertNotIn(key, environment)

    def test_timeout_is_unavailable_not_core_exception(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            worker = root / "worker.py"
            worker.touch()

            def runner(command, **kwargs):
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])

            result = Chronos2ForecastAdapter(
                registry, worker_path=worker, runner=runner, timeout_seconds=5
            ).forecast(bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16)
            self.assertIs(result.status, ProviderForecastStatus.UNAVAILABLE)
            self.assertIn("provider_timeout:5s", result.error)
            self.assertIsNone(result.evidence)

    def test_nonzero_or_malformed_response_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            worker = root / "worker.py"
            worker.touch()

            failed = Chronos2ForecastAdapter(
                registry,
                worker_path=worker,
                runner=lambda command, **kwargs: subprocess.CompletedProcess(
                    command, 2, "", "RuntimeError: model unavailable"
                ),
            ).forecast(bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16)
            self.assertFalse(failed.available)
            self.assertIn("provider_process_failed", failed.error)

            malformed = Chronos2ForecastAdapter(
                registry,
                worker_path=worker,
                runner=lambda command, **kwargs: subprocess.CompletedProcess(command, 0, "not-json", ""),
            ).forecast(bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16)
            self.assertFalse(malformed.available)
            self.assertIn("provider_response_not_json", malformed.error)

    def test_response_identity_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = installed_registry(root)
            worker = root / "worker.py"
            worker.touch()

            def runner(command, **kwargs):
                request = json.loads(kwargs["input"])
                response = response_for(request)
                response["model_revision"] = "0" * 40
                return subprocess.CompletedProcess(command, 0, canonical_json(response), "")

            result = Chronos2ForecastAdapter(registry, worker_path=worker, runner=runner).forecast(
                bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            self.assertFalse(result.available)
            self.assertIn("response_identity_mismatch:model_revision", result.error)

    def test_missing_provider_or_worker_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = Chronos2ForecastAdapter(ProviderRegistry(root)).forecast(
                bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            self.assertFalse(result.available)
            self.assertIn("provider_not_installed", result.error)

            registry = installed_registry(root)
            result = Chronos2ForecastAdapter(registry, worker_path=root / "missing.py").forecast(
                bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16
            )
            self.assertFalse(result.available)
            self.assertEqual(result.error, "provider_worker_missing")

    def test_worker_request_validator_rejects_tampering_before_model_import(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = installed_registry(Path(temporary))
            snapshot = registry.snapshot(CHRONOS2_PROVIDER_ID)

            unexpected = build_chronos2_request(
                bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16, snapshot=snapshot
            )
            unexpected["account"] = "must-never-cross-provider-boundary"
            with self.assertRaisesRegex(ValueError, "request_schema_has_missing_or_unexpected_fields"):
                _validate_request(unexpected)

            tampered = build_chronos2_request(
                bars(), symbol="EURUSD", timeframe="M15", horizon_steps=16, snapshot=snapshot
            )
            self.assertEqual(_validate_request(tampered)["provider_id"], CHRONOS2_PROVIDER_ID)
            tampered["context"][-1]["close"] = 999.0
            with self.assertRaisesRegex(ValueError, "request_context_sha256_mismatch"):
                _validate_request(tampered)

    def test_invalid_context_or_horizon_is_a_core_contract_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = installed_registry(Path(temporary)).snapshot(CHRONOS2_PROVIDER_ID)
            with self.assertRaisesRegex(ValueError, "forecast_context_requires"):
                build_chronos2_request(
                    bars(31), symbol="EURUSD", timeframe="M15", horizon_steps=16, snapshot=snapshot
                )
            with self.assertRaisesRegex(ValueError, "forecast_request_horizon_out_of_bounds"):
                build_chronos2_request(
                    bars(), symbol="EURUSD", timeframe="M15", horizon_steps=0, snapshot=snapshot
                )


if __name__ == "__main__":
    unittest.main()
