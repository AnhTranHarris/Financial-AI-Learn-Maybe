from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from dusty.fidelity import advance_fidelity, validate_fidelity_chain
from dusty.mt5lab import MT5TestRequest, MT5TickMode
from dusty.mt5worker import (
    MT5BarRequest,
    ReadOnlyMT5Worker,
    launch_tester,
    render_tester_ini,
    tester_command,
)
from dusty.operations import ReconciliationResult
from dusty.training_certification import TrainingGateInput, qualify_training_phase


T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
T1 = datetime(2026, 1, 3, tzinfo=timezone.utc)


class FakeMT5:
    TIMEFRAME_M5 = 5

    def __init__(self):
        self.initialized = []
        self.shutdown_count = 0
        self.calls = []

    def initialize(self, path):
        self.initialized.append(path)
        return True

    def shutdown(self):
        self.shutdown_count += 1

    def last_error(self):
        return (0, "ok")

    def copy_rates_range(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start, end))
        return [
            {"time": int(start.timestamp()), "open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "tick_volume": 10, "spread": 2, "real_volume": 0},
            {"time": int(end.timestamp()), "open": 1.05, "high": 1.2, "low": 1.0, "close": 1.1, "tick_volume": 12, "spread": 2, "real_volume": 0},
        ]


def request(mode: MT5TickMode = MT5TickMode.OPEN_PRICES) -> MT5TestRequest:
    return MT5TestRequest("r1", r"C:\MT5\terminal64.exe", "hash", "EURUSD", "M5", T0, T1, mode)


class MT5WorkerTests(unittest.TestCase):
    def test_m43_read_only_worker_streams_bounded_history_and_shutdowns(self):
        fake = FakeMT5()
        worker = ReadOnlyMT5Worker(fake)
        bars = tuple(worker.stream_bars(MT5BarRequest(r"C:\MT5\terminal64.exe", "EURUSD", "M5", T0, T1, chunk_days=1)))
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(len(bars), 3)
        self.assertEqual(fake.shutdown_count, 1)
        self.assertFalse(worker.broker_write_authorized)
        self.assertFalse(hasattr(worker, "order_send"))

    def test_m43_tester_ini_disables_live_trading_and_uses_official_model(self):
        ini = render_tester_ini(request(MT5TickMode.REAL_TICKS), expert=r"Dusty\ResearchEA", report=r"reports\r1")
        self.assertIn("AllowLiveTrading=0", ini)
        self.assertIn("AllowDllImport=0", ini)
        self.assertIn("Model=4", ini)
        self.assertIn("UseCloud=0", ini)
        self.assertIn("Optimization=0", ini)
        self.assertNotIn("Password=", ini)
        self.assertEqual(tester_command(request(), r"C:\Dusty\r1.ini")[0], r"C:\MT5\terminal64.exe")

    def test_m43_launcher_is_injectable_and_returns_process_code(self):
        seen = {}

        def runner(command, **kwargs):
            seen["command"] = command
            seen.update(kwargs)
            return SimpleNamespace(returncode=0)

        code = launch_tester(request(), r"C:\Dusty\r1.ini", timeout_seconds=60, runner=runner)
        self.assertEqual(code, 0)
        self.assertTrue(seen["command"][1].startswith("/config:"))
        self.assertFalse(seen["check"])


class FidelityTests(unittest.TestCase):
    def test_m44_escalates_only_after_reconciliation(self):
        passed = ReconciliationResult(True, 0.0, 0, ())
        failed = ReconciliationResult(False, 0.2, 10, ("return_gap_exceeded",))
        blocked = advance_fidelity(request(MT5TickMode.OPEN_PRICES), failed)
        self.assertFalse(blocked.advance)
        advanced = advance_fidelity(request(MT5TickMode.ONE_MINUTE_OHLC), passed)
        self.assertTrue(advanced.advance)
        self.assertIs(advanced.next_request.tick_mode, MT5TickMode.EVERY_TICK)
        complete = advance_fidelity(request(MT5TickMode.REAL_TICKS), passed)
        self.assertTrue(complete.completed)

    def test_m44_chain_rejects_skipped_fidelity(self):
        self.assertTrue(validate_fidelity_chain((MT5TickMode.OPEN_PRICES, MT5TickMode.ONE_MINUTE_OHLC, MT5TickMode.EVERY_TICK, MT5TickMode.REAL_TICKS)))
        self.assertFalse(validate_fidelity_chain((MT5TickMode.OPEN_PRICES, MT5TickMode.REAL_TICKS)))


class TrainingCertificationTests(unittest.TestCase):
    def test_m45_certification_never_grants_broker_write(self):
        values = dict(
            m35_ready=True,
            symbol_curriculum_certified=True,
            curriculum_point_in_time_clean=True,
            reasoning_bridge_certified=True,
            hypothesis_falsification_ready=True,
            adaptive_acquisition_certified=True,
            regime_context_certified=True,
            mt5_read_only_worker_certified=True,
            multi_fidelity_validation_complete=True,
        )
        passed = qualify_training_phase(TrainingGateInput(**values))
        self.assertTrue(passed.ready_for_demo_execution_development)
        self.assertFalse(passed.broker_write_authorized)
        values["regime_context_certified"] = False
        failed = qualify_training_phase(TrainingGateInput(**values))
        self.assertFalse(failed.ready_for_demo_execution_development)
        self.assertIn("regime_context_not_certified", failed.reasons)


if __name__ == "__main__":
    unittest.main()
