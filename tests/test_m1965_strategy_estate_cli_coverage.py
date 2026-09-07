from __future__ import annotations

from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dusty.strategy_estate_cli import _seed_coverage_exit, main


class M1965StrategyEstateCliCoverageTests(unittest.TestCase):
    def test_list_require_all_seeds_fails_closed_when_estate_is_incomplete(self) -> None:
        with TemporaryDirectory() as temp:
            estate = Path(temp) / "missing-estate.json"
            output = StringIO()
            with redirect_stdout(output):
                result = main([
                    "--list",
                    "--estate",
                    str(estate),
                    "--require-all-seeds",
                ])

        self.assertEqual(result, 4)
        rendered = output.getvalue()
        self.assertIn("Governed seed coverage: 0/6", rendered)
        self.assertIn("Missing governed seeds:", rendered)

    def test_list_without_coverage_gate_preserves_read_only_success_semantics(self) -> None:
        with TemporaryDirectory() as temp:
            estate = Path(temp) / "missing-estate.json"
            with redirect_stdout(StringIO()):
                result = main(["--list", "--estate", str(estate)])
        self.assertEqual(result, 0)

    def test_required_seed_coverage_succeeds_only_when_nothing_is_missing(self) -> None:
        output = StringIO()
        with patch(
            "dusty.strategy_estate_cli._missing_seed_proposals",
            return_value=((), 6),
        ), redirect_stdout(output):
            result = _seed_coverage_exit(Path("unused-estate.json"))

        self.assertEqual(result, 0)
        self.assertIn("Governed seed coverage: 6/6", output.getvalue())


if __name__ == "__main__":
    unittest.main()
