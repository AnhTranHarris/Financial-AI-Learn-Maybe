from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from dusty.integrated_research_cycle import (
    IntegratedResearchCycleConfig,
    nominal_future_times,
    run_integrated_research_cycle,
    timeframe_delta,
)
from dusty.forecast_research import DisagreementState
from dusty.mt5worker import MT5Bar
from dusty.provider_forecast_adapter import (
    PROTOCOL,
    ForecastEvidence,
    ProviderForecastResult,
    ProviderForecastStatus,
)
from dusty.provider_multi_contract import ContractorForecastResult, ForecastProvenance
from dusty.provider_multi_service import ForecastSelectionMode
from dusty.provider_process import ProviderWorkerState
from dusty.research_runtime import ResearchStage, SQLiteResearchCycleStore


def _raw_bars(count: int = 96) -> tuple[MT5Bar, ...]:
    start = datetime(2026, 1, 5, 0, 0, tzinfo=timezone.utc)
    rows = []
    for index in range(count):
        base = 1.10000 + index * 0.00001
        rows.append(
            MT5Bar(
                at=start + timedelta(minutes=15 * index),
                open=base,
                high=base + 0.00020,
                low=base - 0.00020,
                close=base + 0.00005,
                tick_volume=100 + index,
                spread=12,
                real_volume=0,
            )
        )
    return tuple(rows)


def _digest(char: str) -> str:
    return char * 64


class _FakeManager:
    def __init__(self, *, fail_provider: str = "") -> None:
        self.fail_provider = fail_provider
        self.selected = None
        self.stopped = False

    def select(self, mode):
        self.selected = ForecastSelectionMode(mode)
        return self.selected

    def start_selected(self):
        return {
            "chronos2": ProviderWorkerState.READY,
            "kronos-small": ProviderWorkerState.READY,
            "timesfm-2.5": ProviderWorkerState.READY,
        }

    def forecast_selected(
        self,
        bars,
        *,
        symbol,
        timeframe,
        horizon_steps,
        future_times=None,
    ):
        as_of = bars[-1].at
        origin = bars[-1].close
        specs = (
            ("chronos2", "amazon/chronos-2", "a", origin * 1.001),
            ("kronos-small", "NeoQuasar/Kronos-small", "b", origin * 0.999),
            ("timesfm-2.5", "google/timesfm-2.5-200m-transformers", "c", origin * 0.998),
        )
        results = []
        for provider_id, model_id, char, p50 in specs:
            provenance = ForecastProvenance(provider_id, "test", 1)
            if provider_id == self.fail_provider:
                results.append(
                    ContractorForecastResult(
                        ProviderForecastResult(
                            provider_id=provider_id,
                            status=ProviderForecastStatus.UNAVAILABLE,
                            error="synthetic_failure",
                        ),
                        provenance,
                    )
                )
                continue
            evidence = ForecastEvidence(
                protocol=PROTOCOL,
                provider_id=provider_id,
                model_id=model_id,
                model_revision=char * 40,
                provider_version="test",
                license_id="test",
                symbol=symbol.upper(),
                timeframe=timeframe.upper(),
                as_of=as_of,
                origin_at=as_of,
                horizon_steps=horizon_steps,
                origin_value=origin,
                p10=min(origin * 0.997, p50),
                p50=p50,
                p90=max(origin * 1.003, p50),
                context_sha256=_digest(char),
                request_sha256=_digest(chr(ord(char) + 3)),
                response_sha256=_digest(chr(ord(char) + 6)),
            )
            results.append(
                ContractorForecastResult(
                    ProviderForecastResult(
                        provider_id=provider_id,
                        status=ProviderForecastStatus.AVAILABLE,
                        evidence=evidence,
                    ),
                    provenance,
                )
            )
        return tuple(results)

    def stop_all(self):
        self.stopped = True
        return {
            "chronos2": ProviderWorkerState.STOPPED,
            "kronos-small": ProviderWorkerState.STOPPED,
            "timesfm-2.5": ProviderWorkerState.STOPPED,
        }


class M135IntegratedResearchCycleTests(unittest.TestCase):
    def test_timeframe_schedule_is_bounded_and_timezone_aware(self):
        as_of = datetime(2026, 1, 5, tzinfo=timezone.utc)
        self.assertEqual(timeframe_delta("M15"), timedelta(minutes=15))
        self.assertEqual(timeframe_delta("H1"), timedelta(hours=1))
        schedule = nominal_future_times(as_of, timeframe="M15", horizon_steps=4)
        self.assertEqual(len(schedule), 4)
        self.assertEqual(schedule[0], as_of + timedelta(minutes=15))
        self.assertEqual(schedule[-1], as_of + timedelta(hours=1))

    def test_config_rejects_any_operational_authority(self):
        with self.assertRaisesRegex(ValueError, "cannot_receive_operational_authority"):
            IntegratedResearchCycleConfig(broker_write_authority=True)

    def test_cycle_binds_cross_schema_forecasts_to_one_pit_board(self):
        manager = _FakeManager()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "cycle.sqlite3"
            store = SQLiteResearchCycleStore(path)
            try:
                result = run_integrated_research_cycle(
                    _raw_bars(),
                    manager,
                    store,
                    config=IntegratedResearchCycleConfig(
                        context_observations=64,
                        horizon_steps=4,
                    ),
                    created_at=datetime(2026, 1, 6, tzinfo=timezone.utc),
                )
                self.assertEqual(result.disagreement.state, DisagreementState.TWO_DOWN_ONE_UP)
                self.assertFalse(result.skill_certification_eligible)
                self.assertFalse(result.broker_write_authority)
                self.assertFalse(result.entry_veto_authority)
                self.assertFalse(result.promotion_authority)
                self.assertFalse(result.risk_override_authority)
                self.assertEqual(result.checkpoint.stage, ResearchStage.CHECKPOINT)
                self.assertTrue(store.integrity_ok())
                self.assertEqual(
                    store.latest(result.cycle_id).fingerprint,
                    result.checkpoint.fingerprint,
                )
                hashes = {
                    row.result.evidence.context_sha256
                    for row in result.forecast_results
                    if row.result.evidence is not None
                }
                self.assertEqual(len(hashes), 3)
                self.assertEqual(len(result.blackboard.items), 5)
                self.assertEqual(manager.selected, ForecastSelectionMode.ALL_THREE)
                self.assertTrue(manager.stopped)
            finally:
                store.close()

    def test_incomplete_forecast_evidence_fails_closed_and_stops_workers(self):
        manager = _FakeManager(fail_provider="kronos-small")
        store = SQLiteResearchCycleStore()
        try:
            with self.assertRaisesRegex(RuntimeError, "forecast_evidence_incomplete"):
                run_integrated_research_cycle(
                    _raw_bars(),
                    manager,
                    store,
                    config=IntegratedResearchCycleConfig(
                        context_observations=64,
                        horizon_steps=4,
                    ),
                )
            self.assertTrue(manager.stopped)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
