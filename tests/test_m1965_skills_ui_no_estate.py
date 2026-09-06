from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from dusty import skills_ui
from dusty.strategy_discovery import StrategyDiscoveryService


class M1965SkillsUINoEstateTests(unittest.TestCase):
    def test_no_strategy_estate_flag_consumed_and_disables_discovery(self) -> None:
        observed = {}

        def fake_ui(argv, discovery):
            observed["argv"] = argv
            observed["discovery"] = discovery
            return 23

        with patch("dusty.skills_ui.run_strategy_discovery_ui", side_effect=fake_ui):
            result = skills_ui.main([
                "--no-strategy-estate",
                "--repository", ".",
                "--terminal", "terminal64.exe",
            ])

        self.assertEqual(result, 23)
        self.assertEqual(observed["argv"], [
            "--repository", ".",
            "--terminal", "terminal64.exe",
        ])
        self.assertIsNone(observed["discovery"])

    def test_missing_default_estate_preserves_arguments_and_enables_discovery(self) -> None:
        observed = {}

        def fake_ui(argv, discovery):
            observed["argv"] = argv
            observed["discovery"] = discovery
            return 29

        with TemporaryDirectory() as temp, patch(
            "dusty.skills_ui.default_strategy_estate_path",
            return_value=Path(temp) / "definitely-missing-estate.json",
        ), patch("dusty.skills_ui.run_strategy_discovery_ui", side_effect=fake_ui):
            result = skills_ui.main(["--repository", "."])

        self.assertEqual(result, 29)
        self.assertEqual(observed["argv"], ["--repository", "."])
        self.assertIsInstance(observed["discovery"], StrategyDiscoveryService)


if __name__ == "__main__":
    unittest.main()
