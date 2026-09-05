from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
import unittest

from dusty.controlled_evolution import ExperimentOutcomeType, InfrastructureFailureKind
from dusty.experiment_manifest import (
    BrokerAssumptions,
    ComputeRequest,
    EvaluationPlan,
    EvaluationStage,
    ExperimentManifest,
    ExperimentWindow,
    FeatureRef,
    ManifestOrigin,
)
from dusty.experiment_queue import ExperimentResource
from dusty.mt5lab import MT5TickMode
from dusty.native_mt5_executor import (
    NativeMT5ExperimentExecutor,
    NativeMT5FailureKind,
    NativeMT5ProcessResult,
    compile_native_mt5_job,
    render_native_set,
    render_native_tester_ini,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _file_fp(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _manifest() -> ExperimentManifest:
    return ExperimentManifest(
        experiment_id="DD-EXP-M161-0001",
        hypothesis_id="HYP-M161-NATIVE",
        hypothesis="Native MT5 execution preserves the immutable research trade plan.",
        origin=ManifestOrigin.USER_CARSON,
        proposal_fingerprint=_fp("proposal"),
        strategy_fingerprint=_fp("strategy"),
        variant_fingerprint=_fp("variant"),
        context_fingerprint=_fp("context"),
        strategy_ancestry_fingerprints=(),
        source_provenance_fingerprints=(_fp("source"),),
        parent_manifest_fingerprints=(),
        software_commit="a" * 40,
        dataset_fingerprint=_fp("dataset"),
        features=(FeatureRef("close", "v1", _fp("close")),),
        broker=BrokerAssumptions(
            profile_fingerprint=_fp("broker"),
            cost_model_fingerprint=_fp("costs"),
            account_currency="USD",
            initial_balance=10_000.0,
            leverage=100,
            execution_model="native_mt5_research",
        ),
        seed=161,
        windows=(
            ExperimentWindow(
                "holdout",
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 2, 1, tzinfo=timezone.utc),
            ),
        ),
        symbols=("EURUSD",),
        timeframes=("M15",),
        research_school="edge_discovery",
        fidelity="native_mt5_research",
        evaluation=EvaluationPlan(
            stage=EvaluationStage.A1,
            policy_fingerprint=_fp("policy"),
            required_metrics=("expectancy", "drawdown"),
            minimum_trades=1,
            walk_forward_required=False,
            cost_stress_required=False,
        ),
        risk_policy_fingerprint=_fp("risk"),
        risk_assumptions=(("risk_mode", "research_only"),),
        compute=ComputeRequest(
            resource=ExperimentResource.CPU_RESEARCH,
            max_wall_seconds=60,
            max_ram_mb=1024,
            max_workers=1,
            gpu_allowed=False,
        ),
        expected_outputs=("native_deals.csv", "tester_report.htm"),
        created_at=datetime(2026, 9, 5, 2, 30, tzinfo=timezone.utc),
    )


MANIFEST_CSV = (
    "trade_id,entry_time,exit_time,side,volume,stop_price,target_price\n"
    "t1,2025.01.02 10:00:00,2025.01.02 11:00:00,long,0.1,1.09,1.12\n"
)


class _Isolation:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.calls = 0

    def terminal_path_available(self, terminal_path: Path) -> bool:
        self.calls += 1
        return self.available


class _Runner:
    def __init__(self, result: NativeMT5ProcessResult, callback=None) -> None:
        self.result = result
        self.callback = callback
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []

    def run(self, command, *, cwd: Path, timeout_seconds: float) -> NativeMT5ProcessResult:
        self.calls.append((tuple(command), cwd, timeout_seconds))
        if self.callback is not None:
            self.callback()
        return self.result


class M161NativeMT5ExecutorTests(unittest.TestCase):
    def _fixture(self, temp: str):
        root = Path(temp)
        terminal = root / "terminal" / "terminal64.exe"
        terminal.parent.mkdir(parents=True, exist_ok=True)
        terminal.write_bytes(b"terminal-binary-v1")
        data_root = root / "data"
        expert = data_root / "MQL5" / "Experts" / "DustyResearchEA.ex5"
        expert.parent.mkdir(parents=True, exist_ok=True)
        expert.write_bytes(b"expert-binary-v1")
        manifest = _manifest()
        package = compile_native_mt5_job(
            manifest,
            terminal_path=terminal,
            terminal_data_root=data_root,
            terminal_binary_sha256=_file_fp(terminal),
            expert_relative_path="DustyResearchEA.ex5",
            expert_binary_sha256=_file_fp(expert),
            symbol="eurusd",
            timeframe="m15",
            window_label="holdout",
            tick_mode=MT5TickMode.REAL_TICKS,
        )
        common = root / "common"
        work = root / "work"
        deals = common.joinpath(*PurePosixPath(package.deals_relative_path).parts)
        report = data_root.joinpath(*PurePosixPath(package.report_relative_path).parts)
        return manifest, package, common, work, deals, report

    @staticmethod
    def _deal_csv(strategy_hash: str) -> str:
        entry = int(datetime(2025, 1, 2, 10, 0, tzinfo=timezone.utc).timestamp() * 1000)
        exit_at = int(datetime(2025, 1, 2, 11, 0, tzinfo=timezone.utc).timestamp() * 1000)
        header = (
            "terminal_build,symbol,period,strategy_hash,position_id,deal_id,time_msc,"
            "deal_type,deal_type_name,entry_type,entry_type_name,volume,price,commission,"
            "swap,profit,fee,reason,reason_name,sl,tp,comment\n"
        )
        return header + (
            f"5000,EURUSD,PERIOD_M15,{strategy_hash},10,20,{entry},0,buy,0,in,0.1,1.1,-0.5,0,0,0,3,expert,1.09,1.12,DDT:t1\n"
            f"5000,EURUSD,PERIOD_M15,{strategy_hash},10,21,{exit_at},1,sell,1,out,0.1,1.11,-0.5,0,100,0,3,expert,0,0,DDT:t1\n"
        )

    def test_package_is_deterministic_and_tester_config_is_fail_closed(self) -> None:
        with TemporaryDirectory() as temp:
            _, first, _, _, _, _ = self._fixture(temp)
            manifest, _, _, _, _, _ = self._fixture(temp)
            terminal = Path(first.terminal_path)
            expert = Path(first.terminal_data_root) / "MQL5" / "Experts" / "DustyResearchEA.ex5"
            second = compile_native_mt5_job(
                manifest,
                terminal_path=terminal,
                terminal_data_root=first.terminal_data_root,
                terminal_binary_sha256=_file_fp(terminal),
                expert_relative_path="DustyResearchEA.ex5",
                expert_binary_sha256=_file_fp(expert),
                symbol="EURUSD",
                timeframe="M15",
                window_label="holdout",
                tick_mode=MT5TickMode.REAL_TICKS,
            )
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertFalse(first.broker_write_authorized)
            ini = render_native_tester_ini(first)
            self.assertIn("AllowLiveTrading=0", ini)
            self.assertIn("AllowDllImport=0", ini)
            self.assertIn("Optimization=0", ini)
            self.assertIn("UseLocal=1", ini)
            self.assertIn("UseRemote=0", ini)
            self.assertIn("UseCloud=0", ini)
            self.assertIn("ShutdownTerminal=1", ini)
            self.assertIn(f"ExpertParameters={first.set_file_name}", ini)
            set_text = render_native_set(first)
            self.assertIn("DustyDragon\\M161", set_text)
            self.assertIn(first.strategy_fingerprint, set_text)

    def test_compile_rejects_undeclared_binding_and_unsafe_artifact_paths(self) -> None:
        with TemporaryDirectory() as temp:
            manifest, package, _, _, _, _ = self._fixture(temp)
            with self.assertRaises(ValueError):
                compile_native_mt5_job(
                    manifest,
                    terminal_path=package.terminal_path,
                    terminal_data_root=package.terminal_data_root,
                    terminal_binary_sha256=package.terminal_binary_sha256,
                    expert_relative_path="DustyResearchEA.ex5",
                    expert_binary_sha256=package.expert_binary_sha256,
                    symbol="GBPUSD",
                    timeframe="M15",
                    window_label="holdout",
                    tick_mode=MT5TickMode.REAL_TICKS,
                )
            with self.assertRaises(ValueError):
                replace(package, report_relative_path="../escape.htm")

    def test_terminal_conflict_fails_before_launch(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, _, _ = self._fixture(temp)
            runner = _Runner(NativeMT5ProcessResult(0, False))
            executor = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=runner,
                isolation_verifier=_Isolation(False),
            )
            result = executor.execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertEqual(result.failure_kind, NativeMT5FailureKind.TERMINAL_FAIL)
            self.assertTrue(result.infrastructure_failure)
            self.assertEqual(runner.calls, [])

    def test_binary_hash_drift_fails_before_launch(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, _, _ = self._fixture(temp)
            Path(package.terminal_path).write_bytes(b"tampered")
            runner = _Runner(NativeMT5ProcessResult(0, False))
            executor = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=runner,
                isolation_verifier=_Isolation(True),
            )
            result = executor.execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertEqual(result.failure_kind, NativeMT5FailureKind.CONFIG_FAIL)
            self.assertEqual(runner.calls, [])

    def test_timeout_and_data_failure_hints_are_infrastructure_only(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, _, _ = self._fixture(temp)
            timeout = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(NativeMT5ProcessResult(None, True)),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertEqual(timeout.failure_kind, NativeMT5FailureKind.TIMEOUT)
            outcome = timeout.to_experiment_outcome(subject_fingerprint=package.strategy_fingerprint)
            self.assertEqual(outcome.outcome, ExperimentOutcomeType.INFRASTRUCTURE_FAILED)
            self.assertEqual(outcome.infrastructure_kind, InfrastructureFailureKind.PROCESS)

            hinted = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(
                    NativeMT5ProcessResult(
                        2,
                        False,
                        failure_hint=NativeMT5FailureKind.DATA_FAIL,
                    )
                ),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertEqual(hinted.failure_kind, NativeMT5FailureKind.DATA_FAIL)
            self.assertTrue(hinted.infrastructure_failure)

    def test_completed_native_artifacts_recover_shutdown_timeout(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, deals, report = self._fixture(temp)

            def completed() -> None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    "<html><body>complete native tester report</body></html>",
                    encoding="utf-8",
                )
                deals.parent.mkdir(parents=True, exist_ok=True)
                deals.write_text(
                    self._deal_csv(package.strategy_fingerprint),
                    encoding="utf-8",
                )

            result = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(
                    NativeMT5ProcessResult(None, True),
                    completed,
                ),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)

            self.assertIsNone(result.failure_kind)
            self.assertIsNotNone(result.evidence)
            self.assertTrue(result.strategy_evidence_usable)
            self.assertEqual(
                result.reason,
                "native_mt5_execution_completed_after_terminal_shutdown_timeout",
            )
            assert result.evidence is not None
            self.assertEqual(result.evidence.trade_count, 1)

    def test_shutdown_timeout_with_incomplete_report_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, deals, report = self._fixture(temp)

            def incomplete() -> None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text(
                    "<html><body>unfinished",
                    encoding="utf-8",
                )
                deals.parent.mkdir(parents=True, exist_ok=True)
                deals.write_text(
                    self._deal_csv(package.strategy_fingerprint),
                    encoding="utf-8",
                )

            result = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(
                    NativeMT5ProcessResult(None, True),
                    incomplete,
                ),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)

            self.assertEqual(
                result.failure_kind,
                NativeMT5FailureKind.TIMEOUT,
            )
            self.assertEqual(
                result.reason,
                "strategy_tester_timeout_with_incomplete_report",
            )
            self.assertIsNone(result.evidence)

    def test_missing_or_malformed_native_artifacts_are_tester_failures(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, deals, report = self._fixture(temp)
            missing = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(NativeMT5ProcessResult(0, False)),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertEqual(missing.failure_kind, NativeMT5FailureKind.TESTER_FAIL)

            def malformed() -> None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("report", encoding="utf-8")
                deals.parent.mkdir(parents=True, exist_ok=True)
                deals.write_text("bad,columns\n1,2\n", encoding="utf-8")

            broken = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(NativeMT5ProcessResult(0, False), malformed),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertEqual(broken.failure_kind, NativeMT5FailureKind.TESTER_FAIL)
            self.assertIsNone(broken.evidence)

    def test_completed_native_evidence_requires_separate_strategy_verdict(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, deals, report = self._fixture(temp)

            def write_artifacts() -> None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("native tester report", encoding="utf-8")
                deals.parent.mkdir(parents=True, exist_ok=True)
                deals.write_text(self._deal_csv(package.strategy_fingerprint), encoding="utf-8")

            executed = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(NativeMT5ProcessResult(0, False), write_artifacts),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertIsNone(executed.failure_kind)
            self.assertIsNotNone(executed.evidence)
            self.assertIsNone(executed.strategy_passed)
            with self.assertRaises(ValueError):
                executed.to_experiment_outcome(subject_fingerprint=package.strategy_fingerprint)

            failed = executed.with_strategy_verdict(passed=False, reason="A1 expectancy failed")
            self.assertEqual(failed.failure_kind, NativeMT5FailureKind.STRATEGY_FAIL)
            self.assertFalse(failed.infrastructure_failure)
            self.assertTrue(failed.strategy_evidence_usable)
            outcome = failed.to_experiment_outcome(subject_fingerprint=package.strategy_fingerprint)
            self.assertEqual(outcome.outcome, ExperimentOutcomeType.RESEARCH_FAILED)
            self.assertIsNone(outcome.infrastructure_kind)

            passed = executed.with_strategy_verdict(passed=True, reason="A1 native gate passed")
            self.assertEqual(
                passed.to_experiment_outcome(subject_fingerprint=package.strategy_fingerprint).outcome,
                ExperimentOutcomeType.PASSED,
            )

    def test_planned_trade_missing_from_native_output_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, deals, report = self._fixture(temp)

            def write_empty_deals() -> None:
                report.parent.mkdir(parents=True, exist_ok=True)
                report.write_text("native tester report", encoding="utf-8")
                deals.parent.mkdir(parents=True, exist_ok=True)
                deals.write_text(
                    "terminal_build,symbol,period,strategy_hash,position_id,deal_id,time_msc,"
                    "deal_type,deal_type_name,entry_type,entry_type_name,volume,price,commission,"
                    "swap,profit,fee,reason,reason_name,sl,tp,comment\n",
                    encoding="utf-8",
                )

            result = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(NativeMT5ProcessResult(0, False), write_empty_deals),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertEqual(result.failure_kind, NativeMT5FailureKind.TESTER_FAIL)
            self.assertFalse(result.strategy_evidence_usable)

    def test_stale_outputs_are_removed_before_exact_retry(self) -> None:
        with TemporaryDirectory() as temp:
            _, package, common, work, deals, report = self._fixture(temp)
            deals.parent.mkdir(parents=True, exist_ok=True)
            deals.write_text("stale deals", encoding="utf-8")
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("stale report", encoding="utf-8")

            def assert_clean_then_write() -> None:
                self.assertFalse(deals.exists())
                self.assertFalse(report.exists())
                report.write_text("new report", encoding="utf-8")
                deals.write_text(self._deal_csv(package.strategy_fingerprint), encoding="utf-8")

            result = NativeMT5ExperimentExecutor(
                common_files_root=common,
                work_root=work,
                runner=_Runner(NativeMT5ProcessResult(0, False), assert_clean_then_write),
                isolation_verifier=_Isolation(True),
            ).execute(package, research_manifest_csv=MANIFEST_CSV)
            self.assertIsNone(result.failure_kind)

    def test_process_runner_cannot_invent_strategy_failure(self) -> None:
        with self.assertRaises(ValueError):
            NativeMT5ProcessResult(
                0,
                False,
                failure_hint=NativeMT5FailureKind.STRATEGY_FAIL,
            )

    def test_source_never_uses_broad_terminal_image_kill(self) -> None:
        source = Path("src/dusty/native_mt5_executor.py").read_text(encoding="utf-8")
        self.assertIn('("taskkill", "/PID", str(process.pid), "/T", "/F")', source)
        self.assertNotIn('taskkill", "/IM"', source)
        self.assertIn("Get-CimInstance Win32_Process", source)


if __name__ == "__main__":
    unittest.main()