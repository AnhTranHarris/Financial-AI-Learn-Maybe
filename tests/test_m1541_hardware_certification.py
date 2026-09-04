from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import unittest

from dusty.features import completed_feature_bars_from_mt5
from dusty.hardware_certification import (
    EXPECTED_PROVIDERS,
    HardwareCertificationConfig,
    render_hardware_report,
    run_hardware_certification,
)
from dusty.mt5worker import MT5Bar
from dusty.ollama_quant_reviewer import (
    LocalQuantReviewResult,
    QuantReviewerAvailability,
)
from dusty.provider_forecast_adapter import (
    PROTOCOL,
    ForecastEvidence,
    ProviderForecastResult,
    ProviderForecastStatus,
)
from dusty.provider_multi_contract import ContractorForecastResult, ForecastProvenance
from dusty.provider_multi_service import ForecastSelectionMode
from dusty.provider_process import ProviderWorkerState
from dusty.quant_reviewer import parse_quant_review
from dusty.research_organism import ResearchBarBatch


def _sha(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _raw_bars(count: int = 96) -> tuple[MT5Bar, ...]:
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        base = 1.1 + index * 0.00001
        rows.append(
            MT5Bar(
                at=start + timedelta(minutes=15 * index),
                open=base,
                high=base + 0.0002,
                low=base - 0.0002,
                close=base + 0.00005,
                tick_volume=100 + index,
                spread=12,
                real_volume=0,
            )
        )
    return tuple(rows)


class _FakeDataService:
    broker_write_authorized = False

    def load(self, request):
        raw = _raw_bars()
        return ResearchBarBatch(
            symbol=request.symbol.upper(),
            native_symbol=request.symbol,
            timeframe=request.timeframe.upper(),
            start=request.start,
            end=request.end,
            terminal_path_sha256=_sha(request.terminal_path),
            raw_bars=raw,
            completed_bars=completed_feature_bars_from_mt5(raw),
        )


class _FakeManager:
    def __init__(self):
        self.selected = None
        self.stopped = False

    def select(self, mode):
        self.selected = ForecastSelectionMode(mode)
        return self.selected

    def start_selected(self):
        return {provider: ProviderWorkerState.READY for provider in EXPECTED_PROVIDERS}

    def forecast_selected(self, bars, *, symbol, timeframe, horizon_steps, future_times=None):
        origin = bars[-1].close
        specs = (
            ("chronos2", "amazon/chronos-2", origin * 1.001),
            ("kronos-small", "NeoQuasar/Kronos-small", origin * 0.999),
            ("timesfm-2.5", "google/timesfm-2.5-200m-transformers", origin * 1.0005),
        )
        results = []
        for provider, model, p50 in specs:
            evidence = ForecastEvidence(
                protocol=PROTOCOL,
                provider_id=provider,
                model_id=model,
                model_revision=_sha(provider)[:40],
                provider_version="test",
                license_id="test",
                symbol=symbol.upper(),
                timeframe=timeframe.upper(),
                as_of=bars[-1].at,
                origin_at=bars[-1].at,
                horizon_steps=horizon_steps,
                origin_value=origin,
                p10=min(origin * 0.997, p50),
                p50=p50,
                p90=max(origin * 1.003, p50),
                context_sha256=_sha(provider + ":context"),
                request_sha256=_sha(provider + ":request"),
                response_sha256=_sha(provider + ":response"),
            )
            results.append(
                ContractorForecastResult(
                    ProviderForecastResult(
                        provider_id=provider,
                        status=ProviderForecastStatus.AVAILABLE,
                        evidence=evidence,
                    ),
                    ForecastProvenance(provider, "test", 1),
                )
            )
        return tuple(results)

    def stop_all(self):
        self.stopped = True
        return {provider: ProviderWorkerState.STOPPED for provider in EXPECTED_PROVIDERS}


class _FakeReviewer:
    broker_write_authorized = False

    def _model_digest(self, model_tag):
        return _sha(model_tag)

    def review(self, request):
        response = json.dumps(
            {
                "state": "wait",
                "rationale_codes": ["hardware_integration_only"],
                "cited_fingerprints": [request.forecast_fingerprints[0]],
                "proposed_research": [],
            },
            separators=(",", ":"),
        )
        evidence = parse_quant_review(request, response)
        return LocalQuantReviewResult(
            QuantReviewerAvailability.AVAILABLE,
            evidence=evidence,
        )


class _UnavailableReviewer(_FakeReviewer):
    def review(self, request):
        return LocalQuantReviewResult(
            QuantReviewerAvailability.UNAVAILABLE,
            error="synthetic_unavailable",
        )


class M1541HardwareCertificationTests(unittest.TestCase):
    def test_full_fake_hardware_path_is_research_only_and_durable(self):
        manager = _FakeManager()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_hardware_certification(
                HardwareCertificationConfig(
                    terminal_path="C:/Program Files/MetaTrader 5/terminal64.exe",
                    provider_root=root / "providers",
                    work_root=root / "work",
                    history_days=2,
                    context_observations=64,
                    horizon_steps=4,
                ),
                now=datetime(2026, 1, 6, tzinfo=timezone.utc),
                data_service=_FakeDataService(),
                manager=manager,
                reviewer=_FakeReviewer(),
            )
            self.assertEqual(result.provider_ids, EXPECTED_PROVIDERS)
            self.assertTrue(result.organism_integrity_ok)
            self.assertTrue(result.quant_review.available)
            self.assertFalse(result.integrated_cycle.skill_certification_eligible)
            self.assertFalse(result.broker_write_authority)
            self.assertFalse(result.entry_veto_authority)
            self.assertFalse(result.promotion_authority)
            self.assertFalse(result.risk_override_authority)
            self.assertTrue(manager.stopped)
            report = render_hardware_report(result)
            self.assertEqual(report["status"], "pass")
            self.assertFalse(report["forecast_contractors"]["forecast_skill_claimed"])
            self.assertTrue(all(value is False for value in report["safety"].values()))
            self.assertTrue((root / "work" / "m1541-research-organism.sqlite").is_file())

    def test_qwen_unavailable_fails_certification_without_granting_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(RuntimeError, "Qwen unavailable"):
                run_hardware_certification(
                    HardwareCertificationConfig(
                        terminal_path="C:/MetaTrader/terminal64.exe",
                        provider_root=root / "providers",
                        work_root=root / "work",
                        history_days=2,
                        context_observations=64,
                    ),
                    now=datetime(2026, 1, 6, tzinfo=timezone.utc),
                    data_service=_FakeDataService(),
                    manager=_FakeManager(),
                    reviewer=_UnavailableReviewer(),
                )

    def test_config_rejects_empty_terminal_and_too_short_history(self):
        with self.assertRaises(ValueError):
            HardwareCertificationConfig(
                terminal_path="",
                provider_root=Path("."),
                work_root=Path("."),
            )
        with self.assertRaises(ValueError):
            HardwareCertificationConfig(
                terminal_path="terminal64.exe",
                provider_root=Path("."),
                work_root=Path("."),
                history_days=1,
            )


if __name__ == "__main__":
    unittest.main()
