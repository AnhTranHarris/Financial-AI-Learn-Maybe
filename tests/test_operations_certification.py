from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.certification import DemoGateInput, qualify_demo_candidate
from dusty.mt5lab import MT5TestRequest, MT5TestResult, MT5TickMode
from dusty.operations import ReconciliationGate, plan_mt5_tests, reconcile_fast_with_mt5
from dusty.research import ExperimentResult
from dusty.resource import ResourceBudget, ResourceSnapshot


T0 = datetime(2026, 8, 31, 7, 0, tzinfo=timezone.utc)


def request(request_id: str) -> MT5TestRequest:
    return MT5TestRequest(
        request_id=request_id,
        terminal_path=rf"C:\\MT5-{request_id}\\terminal64.exe",
        strategy_hash="hash-1",
        symbol="EURUSD",
        timeframe="M5",
        start=T0,
        end=T0 + timedelta(days=30),
        tick_mode=MT5TickMode.EVERY_TICK,
    )


class ReconciliationTests(unittest.TestCase):
    def test_m33_mt5_must_reconcile_with_fast_lab_at_required_fidelity(self):
        fast = ExperimentResult("hash-1", 100, 0.001, 0.10, 0.55, -0.02, "fingerprint")
        mt5 = MT5TestResult(
            "r1", "hash-1", "terminal-a", MT5TickMode.REAL_TICKS, 98, 0.08, 0.04, "broker-a:real-ticks"
        )
        passed = reconcile_fast_with_mt5(
            fast,
            mt5,
            ReconciliationGate(max_total_return_gap=0.03, max_trade_count_gap=5, required_tick_mode=MT5TickMode.EVERY_TICK),
        )
        failed = reconcile_fast_with_mt5(
            fast,
            MT5TestResult("r1", "hash-1", "terminal-a", MT5TickMode.ONE_MINUTE_OHLC, 80, -0.10, 0.10, "broker-a:ohlc"),
            ReconciliationGate(max_total_return_gap=0.03, max_trade_count_gap=5, required_tick_mode=MT5TickMode.EVERY_TICK),
        )
        self.assertTrue(passed.passed)
        self.assertFalse(failed.passed)
        self.assertIn("mt5_fidelity_too_low", failed.reasons)


class SchedulerTests(unittest.TestCase):
    def test_m34_one_terminal_gets_at_most_one_test_and_pressure_defers_all(self):
        budget = ResourceBudget(min_free_disk_bytes=10_000)
        green = ResourceSnapshot(1_000_000, 500_000, 100_000, cpu_percent=20, active_backtests=0)
        plan = plan_mt5_tests(
            (request("b"), request("a"), request("c")),
            ("terminal-2", "terminal-1"),
            green,
            budget,
            max_concurrent=2,
        )
        self.assertEqual(
            plan.assignments,
            (
                type(plan.assignments[0])("a", "terminal-1"),
                type(plan.assignments[0])("b", "terminal-2"),
            ),
        )
        self.assertEqual(plan.deferred_request_ids, ("c",))

        orange = ResourceSnapshot(1_000_000, 100_000, 100_000, cpu_percent=20)
        throttled = plan_mt5_tests((request("a"),), ("terminal-1",), orange, budget, max_concurrent=1)
        self.assertEqual(throttled.assignments, ())
        self.assertEqual(throttled.deferred_request_ids, ("a",))


class DemoQualificationTests(unittest.TestCase):
    def test_m35_gate_can_certify_demo_integration_but_never_broker_write(self):
        ready = DemoGateInput(True, True, True, True, True, True, True, True, True)
        qualification = qualify_demo_candidate(ready)
        self.assertTrue(qualification.ready_for_demo_integration)
        self.assertFalse(qualification.broker_write_authorized)

        missing_real_ticks = DemoGateInput(True, True, True, True, True, True, True, True, False)
        failed = qualify_demo_candidate(missing_real_ticks)
        self.assertFalse(failed.ready_for_demo_integration)
        self.assertIn("real_tick_validation_missing", failed.reasons)
        self.assertFalse(failed.broker_write_authorized)


if __name__ == "__main__":
    unittest.main()
