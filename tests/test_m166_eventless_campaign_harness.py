from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "run_m166_eventless_variant_campaign.ps1"


class M166EventlessCampaignHarnessTests(unittest.TestCase):
    def test_source_is_exact_head_fail_closed_and_broker_free(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        lowered = source.casefold()
        self.assertIn("git switch --detach $expectedhead", lowered)
        self.assertIn("check-runs?per_page=100", lowered)
        self.assertIn("git status --porcelain=v1 --untracked-files=all", lowered)
        self.assertIn("build_m166_event_hypothesis_variant.py", source)
        self.assertIn("run_m166_m173_provisional_quant_guarded.py", source)
        self.assertIn("provisional-quant-failure-prior-", source)
        self.assertNotIn("order_send", lowered)
        self.assertNotIn("metatrader5", lowered)
        self.assertNotIn("terminal64", lowered)
        self.assertNotIn("remove-item", lowered)
        self.assertNotIn("git reset", lowered)
        self.assertNotIn("git clean", lowered)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell parser gate")
    def test_windows_powershell_51_parser_accepts_script(self) -> None:
        command = (
            "$tokens=$null; $errors=$null; "
            "[System.Management.Automation.Language.Parser]::ParseFile(" 
            f"'{str(SCRIPT).replace("'", "''")}', [ref]$tokens, [ref]$errors) | Out-Null; "
            "if ($errors.Count -gt 0) { $errors | ForEach-Object { Write-Error $_.Message }; exit 1 }; exit 0"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
