from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "run_m166_m173_provisional_quant_guarded.py"
SPEC = importlib.util.spec_from_file_location("m166_guarded_runner_test_target", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load guarded provisional runner")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class GuardedProvisionalQuantRunnerTests(unittest.TestCase):
    def test_success_does_not_invent_failure_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = subprocess.CompletedProcess(["python"], 0, stdout="ok\n", stderr="")
            with patch.object(MODULE.subprocess, "run", return_value=fake), patch.object(
                sys, "argv", [str(MODULE_PATH), "--output-root", str(root), "--repo", "x"]
            ):
                self.assertEqual(MODULE.main(), 0)
            self.assertFalse((root / "provisional-quant-failure.json").exists())

    def test_failure_persists_bounded_authority_free_receipt_without_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fake = subprocess.CompletedProcess(
                ["python"],
                7,
                stdout="before failure\n",
                stderr="session evidence unavailable\n",
            )
            with patch.object(MODULE.subprocess, "run", return_value=fake) as runner, patch.object(
                sys, "argv", [str(MODULE_PATH), "--output-root", str(root), "--repo", "x"]
            ):
                self.assertEqual(MODULE.main(), 7)
            self.assertEqual(runner.call_count, 1)
            payload = json.loads((root / "provisional-quant-failure.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "failed")
            self.assertEqual(payload["child_returncode"], 7)
            self.assertIn("session evidence unavailable", payload["stderr_tail"])
            self.assertTrue(all(value is False for value in payload["authority"].values()))

    def test_source_has_no_broker_or_retry_surface(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("order_send(", source)
        self.assertNotIn("MetaTrader5", source)
        self.assertNotIn("while True", source)
        self.assertNotIn("for attempt", source)


if __name__ == "__main__":
    unittest.main()
