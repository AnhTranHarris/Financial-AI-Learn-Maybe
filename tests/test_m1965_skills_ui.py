from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp
from dusty.source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal
from dusty.strategy_discovery import StrategyDiscoveryService
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_library_snapshot import LIBRARY_PATH_ENV, LIBRARY_SHA256_ENV, write_reconstruction_library
from dusty.skills_ui import main
from dusty.trading_skills import ReconstructionActor, ReconstructionRule, ReconstructionRuleBasis, reconstruct_strategy


NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64


def reconstruction():
    proposal = StrategyProposal(
        "source:ui-test",
        SourceSnapshot("myfxbook", "https://www.myfxbook.com/strategies/ui-test/1", NOW - timedelta(days=1), H("a"), SourceAccess.MANUAL_REVIEW, False),
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.PARTIAL,
        "UI reconstruction",
        symbols=("EURUSD",),
        timeframes=("M15",),
        declared_rules=(("concept", "trend"),),
        unresolved=("entry_logic",),
    )
    spec = StrategySpecV2(
        "ui-reconstructed-v1",
        TradeSide.LONG,
        (RuleGroup((Clause("rsi", RuleOp.GE, 55.0),)),),
        ExitPlan("atr:2", "rr:2", max_hold_steps=8),
        15,
        120,
    )
    return reconstruct_strategy(
        proposal,
        candidate_spec=spec,
        symbols=("EURUSD",),
        timeframe="M15",
        rules=(
            ReconstructionRule("concept", "trend", ReconstructionRuleBasis.SOURCE_DECLARED),
            ReconstructionRule("entry_logic", "RSI >= 55", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ),
        actor=ReconstructionActor.CARSON_USER_REVIEW,
        actor_fingerprint=H("b"),
        created_at=NOW,
    )


class M1965SkillsUILauncherTests(unittest.TestCase):
    def test_no_library_launches_discovery_ui_with_default_service(self) -> None:
        with TemporaryDirectory() as temp, patch(
            "dusty.skills_ui.default_strategy_estate_path",
            return_value=Path(temp) / "missing-estate.json",
        ), patch("dusty.skills_ui.run_strategy_discovery_ui", return_value=7) as delegated:
            result = main(["--repository", ".", "--terminal", "C:/MT5/terminal64.exe"])
        self.assertEqual(result, 7)
        argv, discovery = delegated.call_args.args
        self.assertEqual(argv, ["--repository", ".", "--terminal", "C:/MT5/terminal64.exe"])
        self.assertIsInstance(discovery, StrategyDiscoveryService)

    def test_explicit_library_projects_reconstruction_and_disables_discovery_mutation(self) -> None:
        row = reconstruction()
        old_path = os.environ.get(LIBRARY_PATH_ENV)
        old_digest = os.environ.get(LIBRARY_SHA256_ENV)
        with TemporaryDirectory() as temp:
            library = Path(temp) / "strategy-library.json"
            digest = write_reconstruction_library(library, (row,))
            observed: dict[str, object] = {}

            def fake_ui(argv, discovery):
                self.assertIsNone(discovery)
                self.assertEqual(os.environ[LIBRARY_PATH_ENV], str(library.resolve()))
                self.assertEqual(os.environ[LIBRARY_SHA256_ENV], digest)
                index = argv.index("--catalog")
                catalog = json.loads(Path(argv[index + 1]).read_text(encoding="utf-8"))
                observed["catalog"] = catalog
                observed["argv"] = argv
                return 11

            with patch("dusty.skills_ui.run_strategy_discovery_ui", side_effect=fake_ui):
                result = main(["--strategy-library", str(library), "--repository", "."])
        self.assertEqual(result, 11)
        ids = {entry["strategy_id"] for entry in observed["catalog"]}
        self.assertIn("ui-reconstructed-v1", ids)
        self.assertIn("research-rsi-momentum-long-v1", ids)
        self.assertIn("research-rsi-momentum-short-v1", ids)
        self.assertEqual(os.environ.get(LIBRARY_PATH_ENV), old_path)
        self.assertEqual(os.environ.get(LIBRARY_SHA256_ENV), old_digest)

    def test_library_and_raw_catalog_cannot_be_combined(self) -> None:
        with TemporaryDirectory() as temp:
            library = Path(temp) / "strategy-library.json"
            write_reconstruction_library(library, (reconstruction(),))
            with self.assertRaises(SystemExit):
                main(["--strategy-library", str(library), "--catalog", "other.json"])


if __name__ == "__main__":
    unittest.main()
