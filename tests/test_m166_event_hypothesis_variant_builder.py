from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dusty.experience import TradeSide
from dusty.m166_research_identity import M166ResearchIdentity, canonical_bar_payload, dataset_fingerprint, dataset_payload, parameter_fingerprint
from dusty.mt5worker import MT5Bar
from dusty.provisional_research import ProvisionalResearchPlan
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from dusty.strategy_library_snapshot import load_reconstruction_library, write_reconstruction_library
from dusty.trading_skills import ReconstructionActor, ReconstructionRule, ReconstructionRuleBasis, StrategyReconstruction
from tools import build_m166_event_hypothesis_variant as builder

UTC = timezone.utc
GIT = "1" * 40
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
CAL = "e" * 64


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def reconstruction() -> StrategyReconstruction:
    spec = StrategySpecV2(
        strategy_id="ollama-parent",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup((Clause("rsi", RuleOp.GE, 55.0),)),),
        exit_plan=ExitPlan("pct:0.01", "rr:2", max_hold_steps=4),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
        session_filters=("ASIA", "LONDON_NY_OVERLAP"),
        event_exclusion_minutes=15,
    )
    return StrategyReconstruction(
        SHA_A,
        "source",
        "https://example.com/strategy",
        SHA_B,
        SHA_C,
        "Parent",
        ("EURUSD",),
        "M15",
        spec,
        (ReconstructionRule("hypothesis.direction", "long", ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),),
        (),
        ReconstructionActor.OLLAMA,
        SHA_A,
        datetime(2026, 9, 1, tzinfo=UTC),
    )


def bars() -> tuple[MT5Bar, ...]:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return tuple(
        MT5Bar(start + timedelta(minutes=15 * i), 1.10 + i * 0.001, 1.11 + i * 0.001, 1.09 + i * 0.001, 1.105 + i * 0.001, 10, 2, 5)
        for i in range(4)
    )


class M166EventHypothesisVariantBuilderTests(unittest.TestCase):
    def prepare(self, root: Path):
        row = reconstruction()
        data = bars()
        estate = root / "estate.json"
        write_reconstruction_library(estate, (row,))
        dataset = root / "bars.jsonl"
        dataset_bytes = "".join(
            json.dumps(canonical_bar_payload(bar), sort_keys=True, separators=(",", ":")) + "\n"
            for bar in data
        ).encode("utf-8")
        dataset.write_bytes(dataset_bytes)
        metadata = dataset_payload(symbol="EURUSD", timeframe="M15", bars=data)
        data_fp = dataset_fingerprint(symbol="EURUSD", timeframe="M15", bars=data)
        param_fp = parameter_fingerprint(row.candidate_spec)
        identity_model = M166ResearchIdentity("eurusd:m15:test", row.candidate_spec.strategy_hash, data_fp, param_fp, metadata)
        identity = identity_model.payload | {
            "source_commit": "0" * 40,
            "reconstruction_fingerprint": row.fingerprint,
            "dataset_file_sha256": sha256(dataset_bytes).hexdigest(),
            "requested_start_utc": data[0].at.isoformat(),
            "requested_end_utc_exclusive": (data[-1].at + timedelta(minutes=15)).isoformat(),
            "lookback_days": 1,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "research_execution": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        }
        identity_path = root / "identity.json"
        identity_path.write_text(json.dumps(identity), encoding="utf-8")

        plan_model = ProvisionalResearchPlan("eurusd:m15:test", row.candidate_spec.strategy_hash, data_fp, param_fp, CAL, 20, 2)
        plan = {
            "status": "provisional_research_ready",
            "source_commit": "0" * 40,
            "current_m165_status": "PROVISIONAL",
            "current_m165_calibration_fingerprint": CAL,
            "plan_fingerprint": plan_model.fingerprint,
            "plan": plan_model.payload,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        }
        plan_path = root / "plan.json"
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

        requirement_body = {
            "protocol": "dusty-m166-event-requirement-inspector-v3",
            "strategy_fingerprint": row.candidate_spec.strategy_hash,
            "reconstruction_fingerprint": row.fingerprint,
            "event_exclusion_minutes": 15,
            "event_exclusion_basis": "research_hypothesis",
            "event_related_rules": [],
            "source_declared_event_rules": [],
            "unresolved_event_rules": [],
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        }
        requirement = requirement_body | {
            "requirement_fingerprint": sha256(
                json.dumps(requirement_body, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode("utf-8")
            ).hexdigest()
        }
        requirement_path = root / "requirement.json"
        requirement_path.write_text(json.dumps(requirement), encoding="utf-8")
        return row, dataset, identity_path, plan_path, requirement_path, estate, data_fp

    def invoke(self, root: Path, dataset: Path, identity: Path, plan: Path, requirement: Path, estate: Path) -> Path:
        output = root / "output"
        argv = [
            "build_m166_event_hypothesis_variant.py",
            "--repo", str(root),
            "--expected-head", GIT,
            "--dataset", str(dataset),
            "--parent-identity", str(identity),
            "--parent-plan", str(plan),
            "--event-requirement", str(requirement),
            "--strategy-estate", str(estate),
            "--output-root", str(output),
        ]

        def fake_git(_repo: Path, *args: str) -> str:
            if args == ("rev-parse", "HEAD"):
                return GIT
            if args == ("status", "--porcelain=v1", "--untracked-files=all"):
                return ""
            raise AssertionError(args)

        with patch.object(builder, "_git", side_effect=fake_git), patch("sys.argv", argv):
            self.assertEqual(builder.main(), 0)
        return output

    def test_builder_preserves_parent_and_materializes_deterministic_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent, dataset, identity, plan, requirement, estate, data_fp = self.prepare(root)
            parent_estate_sha = _sha(estate)
            dataset_sha = _sha(dataset)
            output = self.invoke(root, dataset, identity, plan, requirement, estate)

            sidecar = output / "strategy-estate-eventless.json"
            variant_identity = json.loads((output / "m166-research-identity-eventless.json").read_text())
            variant_plan = json.loads((output / "m166-m174-provisional-research-plan-eventless.json").read_text())
            receipt = json.loads((output / "m166-event-hypothesis-remediation.json").read_text())
            rows = load_reconstruction_library(sidecar, _sha(sidecar))

            self.assertEqual(_sha(estate), parent_estate_sha)
            self.assertEqual(_sha(dataset), dataset_sha)
            self.assertEqual(len(rows), 2)
            self.assertIn(parent.fingerprint, {row.fingerprint for row in rows})
            variants = [row for row in rows if row.fingerprint != parent.fingerprint]
            self.assertEqual(len(variants), 1)
            variant = variants[0]
            self.assertEqual(variant.candidate_spec.event_exclusion_minutes, 0)
            self.assertNotEqual(variant.candidate_spec.strategy_hash, parent.candidate_spec.strategy_hash)
            self.assertEqual(variant_identity["dataset_fingerprint"], data_fp)
            self.assertEqual(variant_plan["plan"]["dataset_fingerprint"], data_fp)
            self.assertEqual(variant_identity["strategy_fingerprint"], variant.candidate_spec.strategy_hash)
            self.assertEqual(variant_plan["plan"]["strategy_fingerprint"], variant.candidate_spec.strategy_hash)
            self.assertEqual(variant_plan["plan"]["current_observation_count"], 20)
            self.assertEqual(variant_plan["plan"]["current_distinct_days"], 2)
            self.assertFalse(receipt["authority"]["promotion"])

            first_hashes = {path.name: _sha(path) for path in output.iterdir() if path.is_file()}
            self.invoke(root, dataset, identity, plan, requirement, estate)
            second_hashes = {path.name: _sha(path) for path in output.iterdir() if path.is_file()}
            self.assertEqual(first_hashes, second_hashes)

    def test_requirement_fingerprint_tampering_fails_closed_before_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _parent, dataset, identity, plan, requirement, estate, _ = self.prepare(root)
            payload = json.loads(requirement.read_text())
            payload["event_exclusion_minutes"] = 30
            requirement.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "requirement fingerprint mismatch"):
                self.invoke(root, dataset, identity, plan, requirement, estate)
            self.assertFalse((root / "output").exists())


if __name__ == "__main__":
    unittest.main()
