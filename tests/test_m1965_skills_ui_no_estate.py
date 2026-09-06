from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
import unittest

from dusty import skills_ui


class M1965SkillsUINoEstateTests(unittest.TestCase):
    def test_no_strategy_estate_flag_is_consumed_before_legacy_ui(self) -> None:
        observed = {}

        def fake_ui(argv):
            observed["argv"] = argv
            return 23

        with patch("dusty.skills_ui.basic_ui.main", side_effect=fake_ui):
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

    def test_missing_default_estate_preserves_basic_ui_arguments(self) -> None:
        observed = {}

        def fake_ui(argv):
            observed["argv"] = argv
            return 29

        with patch("dusty.skills_ui.default_strategy_estate_path", return_value=Path("definitely-missing-estate.json")), patch(
            "dusty.skills_ui.basic_ui.main", side_effect=fake_ui
        ):
            result = skills_ui.main(["--repository", "."])

        self.assertEqual(result, 29)
        self.assertEqual(observed["argv"], ["--repository", "."])


if __name__ == "__main__":
    unittest.main()
