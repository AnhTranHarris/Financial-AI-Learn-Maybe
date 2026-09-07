from __future__ import annotations

from pathlib import Path
import unittest


class M1965NativeValidationHarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (
            Path(__file__).resolve().parents[1]
            / "tools"
            / "validate_m1965_ollama_hardening.ps1"
        ).read_text(encoding="utf-8")
        cls.lower = cls.script.casefold()

    def test_harness_never_mutates_git_topology_or_broadly_kills_processes(self) -> None:
        for forbidden in (
            "git fetch",
            "git switch",
            "git reset",
            "git clean",
            "git pull",
            "stop-process",
            "taskkill",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.lower)

    def test_harness_has_no_mt5_execution_or_order_surface(self) -> None:
        for forbidden in ("metatrader5", "order_send", "terminal64", "broker order"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.lower)

    def test_harness_handles_zero_native_output_without_trim_on_null(self) -> None:
        self.assertIn("$lines = @(& $filepath @arguments)", self.lower)
        self.assertIn("if ($lines.count -eq 0)", self.lower)
        self.assertIn("return ''", self.script)

    def test_harness_requires_exact_sha_clean_tree_and_full_seed_coverage(self) -> None:
        self.assertIn("[ValidatePattern('^[0-9a-f]{40}$')]", self.script)
        self.assertIn("Working tree is not clean", self.script)
        self.assertIn("--require-all-seeds", self.script)
        self.assertIn("M196.5 SOFTWARE PASSED — HARDWARE POPULATION INCOMPLETE", self.script)


if __name__ == "__main__":
    unittest.main()
