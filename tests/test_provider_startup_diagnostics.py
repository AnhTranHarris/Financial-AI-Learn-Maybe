from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dusty.provider_startup_diagnostics import _script, diagnose_provider
from dusty.provider_registry import ProviderRegistry


class ProviderStartupDiagnosticTests(unittest.TestCase):
    def _installed_chronos(self, root: Path):
        python = root / "Chronos2" / ".venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.touch()
        return ProviderRegistry(root).snapshot("chronos2")

    def test_probe_uses_exact_provider_python_offline_environment_and_pinned_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = self._installed_chronos(Path(temporary))
            captured = {}

            def fake_run(command, **kwargs):
                captured["command"] = command
                captured.update(kwargs)
                stdout = (
                    '{"provider_id":"chronos2","stage":"python_started"}\n'
                    '{"provider_id":"chronos2","stage":"startup_probe_passed"}\n'
                )
                return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

            with patch("dusty.provider_startup_diagnostics.subprocess.run", fake_run):
                result = diagnose_provider(
                    snapshot,
                    source_environment={
                        "SystemRoot": "C:\\Windows",
                        "OPENAI_API_KEY": "must-not-leak",
                        "MT5_PASSWORD": "must-not-leak",
                    },
                )

            self.assertEqual(result.status, "passed")
            self.assertEqual(captured["command"][0], str(snapshot.python_executable))
            self.assertEqual(captured["command"][1:3], ["-u", "-c"])
            self.assertTrue(captured["text"])
            self.assertTrue(captured["capture_output"])
            self.assertFalse(captured["check"])
            environment = captured["env"]
            self.assertEqual(environment["HF_HUB_OFFLINE"], "1")
            self.assertEqual(environment["TRANSFORMERS_OFFLINE"], "1")
            self.assertEqual(environment["CUDA_VISIBLE_DEVICES"], "")
            self.assertNotIn("OPENAI_API_KEY", environment)
            self.assertNotIn("MT5_PASSWORD", environment)
            script = _script(snapshot)
            self.assertIn(snapshot.spec.model_id, script)
            self.assertIn(str(snapshot.spec.model_revision), script)
            self.assertIn("local_files_only=True", script)

    def test_timeout_preserves_partial_stage_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = self._installed_chronos(Path(temporary))
            timeout = subprocess.TimeoutExpired(
                cmd=["python"],
                timeout=180,
                output=(
                    b'{"provider_id":"chronos2","stage":"python_started"}\n'
                    b'{"provider_id":"chronos2","stage":"torch_import_start"}\n'
                ),
                stderr=b"diagnostic warning\n",
            )
            with patch(
                "dusty.provider_startup_diagnostics.subprocess.run",
                side_effect=timeout,
            ):
                result = diagnose_provider(snapshot, timeout_seconds=180)

            self.assertEqual(result.status, "timeout")
            self.assertEqual(result.error, "startup_probe_timeout:180s")
            self.assertEqual(
                [row["stage"] for row in result.stages],
                ["python_started", "torch_import_start"],
            )
            self.assertIn("diagnostic warning", result.stderr)

    def test_missing_provider_does_not_launch_a_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = ProviderRegistry(Path(temporary)).snapshot("chronos2")
            with patch("dusty.provider_startup_diagnostics.subprocess.run") as runner:
                result = diagnose_provider(snapshot)
            runner.assert_not_called()
            self.assertEqual(result.status, "unavailable")
            self.assertIn("provider_not_installed", result.error)


if __name__ == "__main__":
    unittest.main()
