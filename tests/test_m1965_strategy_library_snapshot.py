from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from dusty.experience import TradeSide
from dusty.research import Clause, RuleOp
from dusty.reviewed_strategies import resolve_research_package
from dusty.source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal
from dusty.strategy_catalog import StrategyCatalogEntry, StrategyStage
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_library_snapshot import (
    LIBRARY_PATH_ENV,
    LIBRARY_SHA256_ENV,
    configured_reconstructions,
    load_reconstruction_library,
    write_reconstruction_library,
)
from dusty.trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    ReconstructedResearchPackage,
    reconstruct_strategy,
)


NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64


def reconstructed():
    proposal = StrategyProposal(
        "external:breakout",
        SourceSnapshot(
            "myfxbook",
            "https://www.myfxbook.com/strategies/example/2",
            NOW - timedelta(days=1),
            H("a"),
            SourceAccess.MANUAL_REVIEW,
            False,
        ),
        EvidenceClass.STRATEGY_HYPOTHESIS,
        ProposalCompleteness.PARTIAL,
        "Breakout hypothesis",
        symbols=("EURUSD",),
        timeframes=("M15",),
        components=("breakout",),
        declared_rules=(("concept", "breakout"),),
        unresolved=("entry_logic", "exit_logic", "risk_logic"),
        tags=("research_only",),
    )
    spec = StrategySpecV2(
        "reconstructed-breakout-v1",
        TradeSide.LONG,
        (RuleGroup((Clause("return_1", RuleOp.GT, 0.0), Clause("rsi", RuleOp.GE, 55.0))),),
        ExitPlan("atr:2", "rr:2", max_hold_steps=12),
        15,
        180,
        cooldown_steps=4,
    )
    return reconstruct_strategy(
        proposal,
        candidate_spec=spec,
        symbols=("EURUSD",),
        timeframe="M15",
        rules=(
            ReconstructionRule("concept", "breakout", ReconstructionRuleBasis.SOURCE_DECLARED),
            ReconstructionRule("entry_logic", "return_1 > 0 and RSI >= 55", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("exit_logic", "ATR 2 stop and RR 2 target", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
            ReconstructionRule("risk_logic", "Dusty constitution", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ),
        actor=ReconstructionActor.OLLAMA,
        actor_fingerprint=H("b"),
        created_at=NOW,
    )


class M1965StrategyLibrarySnapshotTests(unittest.TestCase):
    def test_hash_pinned_round_trip_preserves_exact_reconstruction(self) -> None:
        row = reconstructed()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            digest = write_reconstruction_library(path, (row,))
            loaded = load_reconstruction_library(path, digest)
        self.assertEqual(loaded, (row,))
        self.assertEqual(loaded[0].fingerprint, row.fingerprint)
        self.assertEqual(loaded[0].candidate_spec.strategy_hash, row.candidate_spec.strategy_hash)

    def test_file_mutation_after_digest_is_detected_before_parse(self) -> None:
        row = reconstructed()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            digest = write_reconstruction_library(path, (row,))
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "byte digest mismatch"):
                load_reconstruction_library(path, digest)

    def test_unknown_json_field_fails_closed_even_when_attacker_rehashes_file(self) -> None:
        row = reconstructed()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            write_reconstruction_library(path, (row,))
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["unexpected"] = True
            content = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            path.write_bytes(content)
            from hashlib import sha256
            with self.assertRaisesRegex(ValueError, "schema mismatch"):
                load_reconstruction_library(path, sha256(content).hexdigest())

    def test_incomplete_environment_configuration_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "configured together"):
            configured_reconstructions({LIBRARY_PATH_ENV: "x", LIBRARY_SHA256_ENV: ""})
        with self.assertRaisesRegex(ValueError, "configured together"):
            configured_reconstructions({LIBRARY_PATH_ENV: "", LIBRARY_SHA256_ENV: H("a")})
        self.assertEqual(configured_reconstructions({}), ())

    def test_exact_configured_reconstruction_resolves_through_existing_runtime_resolver(self) -> None:
        row = reconstructed()
        package = ReconstructedResearchPackage(row)
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            digest = write_reconstruction_library(path, (row,))
            old_path, old_digest = os.environ.get(LIBRARY_PATH_ENV), os.environ.get(LIBRARY_SHA256_ENV)
            try:
                os.environ[LIBRARY_PATH_ENV] = str(path)
                os.environ[LIBRARY_SHA256_ENV] = digest
                resolved = resolve_research_package(package.catalog_entry)
            finally:
                if old_path is None:
                    os.environ.pop(LIBRARY_PATH_ENV, None)
                else:
                    os.environ[LIBRARY_PATH_ENV] = old_path
                if old_digest is None:
                    os.environ.pop(LIBRARY_SHA256_ENV, None)
                else:
                    os.environ[LIBRARY_SHA256_ENV] = old_digest
        self.assertEqual(resolved.spec.strategy_hash, row.candidate_spec.strategy_hash)
        self.assertEqual(resolved.fingerprint, package.fingerprint)

    def test_metadata_catalog_still_cannot_execute_without_exact_snapshot(self) -> None:
        entry = StrategyCatalogEntry(
            "reconstructed-breakout-v1",
            "pretend reconstructed strategy",
            H("c"),
            StrategyStage.BACKTEST_CANDIDATE,
            universal_symbol_compatibility=True,
            source_url="https://example.com",
            timeframe="M15",
        )
        with self.assertRaisesRegex(ValueError, "no_reviewed_executable_package"):
            resolve_research_package(entry)

    def test_fresh_python_process_inherits_same_pinned_library(self) -> None:
        row = reconstructed()
        package = ReconstructedResearchPackage(row)
        with TemporaryDirectory() as temp:
            path = Path(temp) / "library.json"
            digest = write_reconstruction_library(path, (row,))
            env = dict(os.environ)
            env[LIBRARY_PATH_ENV] = str(path)
            env[LIBRARY_SHA256_ENV] = digest
            script = (
                "from dusty.strategy_library_snapshot import configured_reconstructions; "
                "from dusty.trading_skills import ReconstructedResearchPackage; "
                "from dusty.reviewed_strategies import resolve_research_package; "
                "r=configured_reconstructions()[0]; p=ReconstructedResearchPackage(r); "
                "print(resolve_research_package(p.catalog_entry).fingerprint)"
            )
            completed = subprocess.run(
                [sys.executable, "-c", script],
                env=env,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), package.fingerprint)


if __name__ == "__main__":
    unittest.main()
