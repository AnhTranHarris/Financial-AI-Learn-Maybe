from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from dusty.vibe_research_contract import (
    ALLOWED_TOOLS,
    PROTOCOL,
    PROVIDER_ID,
    VibeResearchStatus,
    build_request,
)
from dusty.vibe_research_service import VibeResearchContractor
from dusty import vibe_research_worker as worker


class _CaptureRunner:
    def __init__(self, response_factory):
        self.response_factory = response_factory
        self.calls: list[dict[str, object]] = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": command, **kwargs})
        request = json.loads(kwargs["input"])
        response = self.response_factory(request)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(response), stderr="")


class VibeResearchContractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.vibe_root = root / "VibeTrading"
        self.work_root = root / "work"
        (self.vibe_root / ".venv" / "Scripts").mkdir(parents=True)
        (self.vibe_root / ".venv" / "Scripts" / "python.exe").write_text("", encoding="utf-8")
        (self.vibe_root / "agent").mkdir(parents=True)
        (self.vibe_root / "agent" / "mcp_server.py").write_text("# fake\n", encoding="utf-8")
        self.worker_path = root / "worker.py"
        self.worker_path.write_text("# fake\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def _ok_response(request: dict[str, object]) -> dict[str, object]:
        return {
            "protocol": PROTOCOL,
            "provider_id": PROVIDER_ID,
            "vibe_version": "0.1.14",
            "tool": request["tool"],
            "status": "ok",
            "surface_sha256": "a" * 64,
            "result_text": '{"status":"ok","result":{"count":3}}',
        }

    def _contractor(self, runner) -> VibeResearchContractor:
        return VibeResearchContractor(
            self.vibe_root,
            self.work_root,
            worker_path=self.worker_path,
            runner=runner,
        )

    def test_allowlist_is_research_only(self) -> None:
        forbidden = {
            "write_file",
            "bash",
            "background_run",
            "trading_orders",
            "trading_positions",
            "order_send",
            "place_order",
            "cancel_order",
        }
        self.assertTrue({"backtest", "alpha_zoo", "get_market_data"} <= ALLOWED_TOOLS)
        self.assertFalse(forbidden & ALLOWED_TOOLS)

    def test_request_rejects_non_allowlisted_tool(self) -> None:
        with self.assertRaisesRegex(ValueError, "not_allowlisted"):
            build_request("trading_orders", {})

    def test_available_result_is_immutable_evidence_with_zero_authority(self) -> None:
        runner = _CaptureRunner(self._ok_response)
        result = self._contractor(runner).invoke("alpha_zoo", {"action": "health"})
        self.assertEqual(result.status, VibeResearchStatus.AVAILABLE)
        self.assertIsNotNone(result.evidence)
        evidence = result.evidence
        assert evidence is not None
        self.assertFalse(evidence.broker_write_authority)
        self.assertFalse(evidence.entry_veto_authority)
        self.assertFalse(evidence.promotion_authority)
        self.assertEqual(len(evidence.fingerprint), 64)

    def test_source_checkout_mcp_file_is_not_required_for_packaged_install(self) -> None:
        (self.vibe_root / "agent" / "mcp_server.py").unlink()
        runner = _CaptureRunner(self._ok_response)
        result = self._contractor(runner).invoke("alpha_zoo", {"action": "health"})
        self.assertTrue(result.available)
        self.assertEqual(len(runner.calls), 1)

    def test_child_environment_isolated_from_llm_and_trading_authority(self) -> None:
        runner = _CaptureRunner(self._ok_response)
        result = self._contractor(runner).invoke("alpha_zoo", {"action": "health"})
        self.assertTrue(result.available)
        env = runner.calls[0]["env"]
        self.assertIsInstance(env, dict)
        env = dict(env)
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("LANGCHAIN_PROVIDER", env)
        self.assertNotIn("OLLAMA_BASE_URL", env)
        self.assertNotIn("MT5_LOGIN", env)
        self.assertEqual(env["VIBE_TRADING_ENABLE_SHELL_TOOLS"], "0")
        resolved_work_root = self.work_root.resolve()
        Path(str(env["HOME"])).resolve().relative_to(resolved_work_root)
        Path(str(env["USERPROFILE"])).resolve().relative_to(resolved_work_root)

    def test_backtest_path_outside_work_root_fails_before_launch(self) -> None:
        runner = _CaptureRunner(self._ok_response)
        outside = self.vibe_root / "not-allowed"
        result = self._contractor(runner).invoke("backtest", {"run_dir": str(outside)})
        self.assertFalse(result.available)
        self.assertIn("outside_work_root", result.error)
        self.assertEqual(runner.calls, [])

    def test_relative_backtest_path_is_normalized_under_work_root(self) -> None:
        runner = _CaptureRunner(self._ok_response)
        result = self._contractor(runner).invoke("backtest", {"run_dir": "case-001"})
        self.assertTrue(result.available)
        request = json.loads(runner.calls[0]["input"])
        run_dir = Path(request["arguments"]["run_dir"])
        run_dir.relative_to(self.work_root.resolve())

    def test_worker_error_becomes_unavailable(self) -> None:
        def response(request):
            return {
                "protocol": PROTOCOL,
                "provider_id": PROVIDER_ID,
                "vibe_version": "0.1.14",
                "status": "worker_error",
                "error": "simulated",
            }

        result = self._contractor(_CaptureRunner(response)).invoke("alpha_zoo", {"action": "health"})
        self.assertFalse(result.available)
        self.assertIn("worker_error", result.error)

    def test_worker_distribution_surface_resolves_top_level_packaged_module(self) -> None:
        packaged_surface = Path(self.temp.name) / "site-packages" / "mcp_server.py"
        packaged_surface.parent.mkdir(parents=True)
        packaged_surface.write_text("# packaged\n", encoding="utf-8")

        class FakeDistribution:
            files = ("mcp_server.py",)

            @staticmethod
            def locate_file(item):
                self.assertEqual(str(item), "mcp_server.py")
                return packaged_surface

        with mock.patch.object(worker.importlib.metadata, "distribution", return_value=FakeDistribution()):
            self.assertEqual(worker._distribution_surface(), packaged_surface.resolve())

    def test_worker_unwrap_supports_fastmcp_v2_style_fn(self) -> None:
        def demo(value):
            return value

        class Wrapper:
            fn = staticmethod(demo)

        self.assertIs(worker._unwrap(Wrapper()), demo)

    def test_worker_path_guard_rejects_escape(self) -> None:
        arguments: dict[str, object] = {"run_dir": str(self.vibe_root)}
        with self.assertRaisesRegex(ValueError, "outside_work_root"):
            worker._validate_paths("backtest", arguments, self.work_root)


if __name__ == "__main__":
    unittest.main()
