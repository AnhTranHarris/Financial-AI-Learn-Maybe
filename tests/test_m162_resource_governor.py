from __future__ import annotations

from hashlib import sha256
import unittest

from dusty.experiment_queue import ExperimentResource
from dusty.resource_governor import (
    AdaptiveParallelComputeGovernor,
    HardwareTelemetry,
    ParallelFactoryPolicy,
    ResourceDeferralReason,
    ResourceWorkRequest,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _request(
    label: str,
    resource: ExperimentResource,
    *,
    priority: int = 0,
    cpu: int = 1,
    ram: int = 256,
    gpu: int = 0,
    exclusive: str | None = None,
    provider: str | None = None,
) -> ResourceWorkRequest:
    return ResourceWorkRequest(
        job_fingerprint=_fp(label),
        resource=resource,
        priority=priority,
        estimated_cpu_cores=cpu,
        estimated_ram_mb=ram,
        estimated_gpu_mb=gpu,
        exclusive_key=exclusive,
        provider=provider,
    )


class M162ResourceGovernorTests(unittest.TestCase):
    def test_plan_is_deterministic_independent_of_input_order(self) -> None:
        policy = ParallelFactoryPolicy(
            reserve_cpu_cores=2,
            reserve_ram_mb=1024,
            reserve_gpu_mb=512,
            max_cpu_workers=4,
            max_forecast_workers=2,
        )
        telemetry = HardwareTelemetry(8, 20.0, 8192, gpu_free_mb=6144, gpu_total_mb=8192)
        rows = (
            _request("cpu-low", ExperimentResource.CPU_RESEARCH, priority=1, ram=512),
            _request("forecast", ExperimentResource.FORECAST, priority=5, ram=1024, gpu=2048, provider="kronos"),
            _request("cpu-high", ExperimentResource.CPU_RESEARCH, priority=10, ram=512),
        )
        governor = AdaptiveParallelComputeGovernor(policy)
        first = governor.plan(rows, telemetry=telemetry)
        second = governor.plan(reversed(rows), telemetry=telemetry)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            [row.job_fingerprint for row in first.admitted],
            [row.job_fingerprint for row in second.admitted],
        )
        self.assertFalse(first.broker_write_authority)
        self.assertFalse(governor.broker_write_authorized)
        self.assertFalse(governor.promotion_authorized)

    def test_mt5_terminal_identity_is_exclusive_and_ollama_is_serial(self) -> None:
        policy = ParallelFactoryPolicy(
            reserve_cpu_cores=1,
            reserve_ram_mb=512,
            max_cpu_workers=8,
            max_mt5_workers=2,
            max_ollama_workers=2,
        )
        telemetry = HardwareTelemetry(12, 10.0, 16000)
        rows = (
            _request("mt5-a", ExperimentResource.MT5_TESTER, priority=10, exclusive="coinexx-terminal"),
            _request("mt5-b", ExperimentResource.MT5_TESTER, priority=9, exclusive="coinexx-terminal"),
            _request("ollama-a", ExperimentResource.OLLAMA, priority=8),
            _request("ollama-b", ExperimentResource.OLLAMA, priority=7),
        )
        plan = AdaptiveParallelComputeGovernor(policy).plan(rows, telemetry=telemetry)
        self.assertEqual(len(plan.admitted), 2)
        reasons = {row.request.job_fingerprint: row.reason for row in plan.deferred}
        self.assertEqual(reasons[_fp("mt5-b")], ResourceDeferralReason.EXCLUSIVE_CONFLICT)
        self.assertEqual(reasons[_fp("ollama-b")], ResourceDeferralReason.EXCLUSIVE_CONFLICT)

    def test_cpu_and_ram_reserves_are_hard_admission_limits(self) -> None:
        policy = ParallelFactoryPolicy(
            reserve_cpu_cores=2,
            reserve_ram_mb=4096,
            max_cpu_workers=8,
        )
        telemetry = HardwareTelemetry(8, 25.0, 6144)
        rows = (
            _request("first", ExperimentResource.CPU_RESEARCH, priority=10, cpu=3, ram=1536),
            _request("second", ExperimentResource.CPU_RESEARCH, priority=9, cpu=3, ram=1024),
        )
        plan = AdaptiveParallelComputeGovernor(policy).plan(rows, telemetry=telemetry)
        self.assertEqual(plan.cpu_budget_cores, 6)
        self.assertEqual(plan.ram_budget_mb, 2048)
        self.assertEqual([row.job_fingerprint for row in plan.admitted], [_fp("first")])
        self.assertEqual(plan.deferred[0].reason, ResourceDeferralReason.RAM_CAPACITY)

    def test_high_cpu_pressure_sheds_all_new_work_without_touching_queue(self) -> None:
        telemetry = HardwareTelemetry(16, 95.0, 32000, gpu_free_mb=12000, gpu_total_mb=16000)
        rows = (
            _request("cpu", ExperimentResource.CPU_RESEARCH),
            _request("forecast", ExperimentResource.FORECAST, gpu=1000, provider="chronos2"),
        )
        plan = AdaptiveParallelComputeGovernor().plan(rows, telemetry=telemetry)
        self.assertEqual(plan.cpu_budget_cores, 0)
        self.assertEqual(plan.admitted, ())
        self.assertTrue(all(row.reason is ResourceDeferralReason.CPU_PRESSURE for row in plan.deferred))

    def test_gpu_work_fails_closed_when_free_memory_is_unverifiable(self) -> None:
        telemetry = HardwareTelemetry(8, 10.0, 12000)
        request = _request(
            "moirai",
            ExperimentResource.FORECAST,
            gpu=2048,
            provider="moirai",
        )
        plan = AdaptiveParallelComputeGovernor().plan((request,), telemetry=telemetry)
        self.assertEqual(plan.admitted, ())
        self.assertEqual(plan.deferred[0].reason, ResourceDeferralReason.GPU_UNVERIFIABLE)

    def test_soft_pressure_reduces_parallel_budget_but_keeps_priority_order(self) -> None:
        policy = ParallelFactoryPolicy(
            reserve_cpu_cores=2,
            reserve_ram_mb=512,
            max_cpu_workers=6,
            soft_pressure_fraction=0.5,
        )
        telemetry = HardwareTelemetry(10, 80.0, 16000)
        rows = tuple(
            _request(f"cpu-{i}", ExperimentResource.CPU_RESEARCH, priority=10 - i)
            for i in range(5)
        )
        plan = AdaptiveParallelComputeGovernor(policy).plan(rows, telemetry=telemetry)
        self.assertEqual(plan.cpu_budget_cores, 3)
        self.assertEqual(len(plan.admitted), 3)
        self.assertEqual([row.priority for row in plan.admitted], [10, 9, 8])

    def test_request_contract_requires_terminal_and_forecast_provider_identity(self) -> None:
        with self.assertRaises(ValueError):
            _request("mt5", ExperimentResource.MT5_TESTER)
        with self.assertRaises(ValueError):
            _request("forecast", ExperimentResource.FORECAST)


if __name__ == "__main__":
    unittest.main()
