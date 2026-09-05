from __future__ import annotations

import base64
from datetime import datetime, time, timezone
from pathlib import Path
import runpy
import unittest

from dusty.native_mt5_executor import PowerShellTerminalIsolationVerifier


class M161HardwareHarnessContractTests(unittest.TestCase):
    def test_powershell_harness_is_exact_head_fail_closed_and_process_specific(self) -> None:
        source = Path("tools/validate_m161_hardware.ps1").read_text(encoding="utf-8")

        self.assertIn("ExpectedHead", source)
        self.assertIn("git rev-parse HEAD", source)
        self.assertIn("git status --porcelain", source)
        self.assertIn("origin.txt", source)
        self.assertIn("Get-CimInstance Win32_Process", source)
        self.assertIn("ExecutablePath", source)
        self.assertIn("Assert-TerminalPathIdle", source)
        self.assertIn("metaeditor64.exe", source)
        self.assertIn("/compile:", source)
        self.assertIn("/include:", source)
        self.assertIn("/log", source)
        self.assertIn("Get-FileHash", source)
        self.assertIn("-Algorithm SHA256", source)
        self.assertIn("smoke_m161_hardware.py", source)
        self.assertIn("M161 LOCAL HARDWARE CERTIFICATION PASSED", source)
        self.assertNotIn("taskkill /im", source.lower())
        self.assertNotIn("stop-process -name", source.lower())

    def test_metaeditor_cli_success_is_proven_by_log_and_ex5_not_zero_exit_assumption(self) -> None:
        source = Path("tools/validate_m161_hardware.ps1").read_text(encoding="utf-8")

        self.assertIn("metaeditor-exit-code.txt", source)
        self.assertIn("$compileExitCode = [int]$compileProcess.ExitCode", source)
        self.assertIn("0\\s+errors?,\\s*0\\s+warnings?", source)
        self.assertIn("Compiled DustyResearchEA.ex5 not found", source)
        self.assertIn("diagnostic only; log + EX5 prove compile success", source)
        self.assertNotIn("if ($compileProcess.ExitCode -ne 0)", source)
        self.assertNotIn("throw \"MetaEditor returned exit code", source)

    def test_python_terminal_isolation_uses_encoded_powershell_contract(self) -> None:
        terminal = Path(r"C:\Program Files\Coinexx MT5 Terminal\terminal64.exe")
        encoded = PowerShellTerminalIsolationVerifier._encoded_command(terminal)
        decoded = base64.b64decode(encoded).decode("utf-16le")
        target_b64 = base64.b64encode(str(terminal).encode("utf-8")).decode("ascii")

        self.assertIn(target_b64, decoded)
        self.assertIn("[Convert]::FromBase64String", decoded)
        self.assertIn("[Text.Encoding]::UTF8.GetString", decoded)
        self.assertIn("Get-CimInstance Win32_Process", decoded)
        self.assertIn("ExecutablePath", decoded)
        self.assertNotIn("$args[0]", decoded)

        source = Path("src/dusty/native_mt5_executor.py").read_text(encoding="utf-8")
        self.assertIn('"-EncodedCommand"', source)
        self.assertIn('script.encode("utf-16le")', source)
        self.assertNotIn('"-Command",\n                    self._SCRIPT', source)
        self.assertNotIn("$args[0]", source)

    def test_python_hardware_smoke_uses_bounded_native_executor_without_strategy_verdict(self) -> None:
        source = Path("tools/smoke_m161_hardware.py").read_text(encoding="utf-8")

        self.assertIn("NativeMT5ExperimentExecutor", source)
        self.assertIn("PowerShellTerminalIsolationVerifier", source)
        self.assertIn("SubprocessNativeMT5Runner", source)
        self.assertIn("MT5TickMode.EVERY_TICK", source)
        self.assertIn('"broker_write_authorized": False', source)
        self.assertIn('"strategy_verdict": None', source)
        self.assertIn('"promotion_authority": False', source)
        self.assertNotIn("with_strategy_verdict", source)

    def test_hardware_research_plan_is_bounded_and_deterministic(self) -> None:
        namespace = runpy.run_path("tools/smoke_m161_hardware.py")
        research_plan = namespace["_research_plan"]
        start = datetime(2026, 8, 31, tzinfo=timezone.utc)
        end = datetime(2026, 9, 1, tzinfo=timezone.utc)

        first = research_plan(
            start=start,
            end=end,
            entry_clock=time(10, 0),
            exit_clock=time(11, 0),
            volume=0.01,
            stop_price=0.1,
        )
        second = research_plan(
            start=start,
            end=end,
            entry_clock=time(10, 0),
            exit_clock=time(11, 0),
            volume=0.01,
            stop_price=0.1,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.count("m161-cert-1"), 1)
        self.assertIn("2026.08.31 10:00:00", first)
        self.assertIn("2026.08.31 11:00:00", first)
        self.assertIn(",long,0.01,0.1,0", first)

    def test_hardware_plan_rejects_times_outside_test_window(self) -> None:
        namespace = runpy.run_path("tools/smoke_m161_hardware.py")
        research_plan = namespace["_research_plan"]
        start = datetime(2026, 8, 31, tzinfo=timezone.utc)
        end = datetime(2026, 9, 1, tzinfo=timezone.utc)

        with self.assertRaises(ValueError):
            research_plan(
                start=start,
                end=end,
                entry_clock=time(11, 0),
                exit_clock=time(10, 0),
                volume=0.01,
                stop_price=0.1,
            )


if __name__ == "__main__":
    unittest.main()
