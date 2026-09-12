from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from dusty.experience import TradeSide
from dusty.m166_research_identity import parameter_fingerprint
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from tools import inspect_m166_event_requirement as inspector

SHA = "a" * 64


class EventRequirementInspectorTests(unittest.TestCase):
    def spec(self) -> StrategySpecV2:
        return StrategySpecV2(
            strategy_id="s1",
            direction=TradeSide.LONG,
            entry_groups=(RuleGroup((Clause("rsi", RuleOp.GT, 50.0),)),),
            exit_plan=ExitPlan("pct:0.01", max_hold_steps=4),
            decision_timeframe_minutes=15,
            intended_horizon_minutes=60,
            session_filters=("LONDON",),
            event_exclusion_minutes=30,
        )

    def reconstruction(self, spec: StrategySpecV2, *, actor: str = "ollama"):
        return SimpleNamespace(
            fingerprint=SHA,
            candidate_spec=spec,
            symbols=("EURUSD",),
            timeframe="M15",
            actor=SimpleNamespace(value=actor),
            rules=(SimpleNamespace(name="event_filter", value="avoid macro releases", basis=SimpleNamespace(value="source_declared")),),
            unresolved_source_rules=("calendar impact tier unspecified",),
        )

    def identity(self, spec: StrategySpecV2) -> dict[str, object]:
        return {
            "reconstruction_fingerprint": SHA,
            "strategy_fingerprint": spec.strategy_hash,
            "parameter_fingerprint": parameter_fingerprint(spec),
            "dataset_metadata": {
                "symbol": "EURUSD",
                "timeframe": "M15",
                "first_bar_utc": "2024-09-12T18:00:00+00:00",
                "last_bar_utc": "2026-09-11T23:45:00+00:00",
            },
        }

    def run_main(self, identity: dict[str, object], reconstruction) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            identity_path = root / "identity.json"
            estate_path = root / "estate.json"
            output_path = root / "result.json"
            identity_path.write_text(json.dumps(identity), encoding="utf-8")
            estate_path.write_text("[]", encoding="utf-8")
            argv = [
                "inspect_m166_event_requirement.py",
                "--identity", str(identity_path),
                "--strategy-estate", str(estate_path),
                "--output", str(output_path),
            ]
            with patch.object(inspector, "load_strategy_estate", return_value=(reconstruction,)), patch("sys.argv", argv):
                self.assertEqual(inspector.main(), 0)
            return json.loads(output_path.read_text(encoding="utf-8"))

    def test_uses_reconstruction_identity_and_exposes_ollama_hypothesis(self):
        spec = self.spec()
        result = self.run_main(self.identity(spec), self.reconstruction(spec))
        self.assertEqual(result["symbol"], "EURUSD")
        self.assertEqual(result["timeframe"], "M15")
        self.assertEqual(result["event_exclusion_minutes"], 30)
        self.assertEqual(result["event_exclusion_basis"], "research_hypothesis")
        self.assertEqual(result["source_declared_event_rules"][0]["basis"], "source_declared")
        self.assertIn("calendar impact tier unspecified", result["unresolved_event_rules"])
        self.assertFalse(result["authority"]["broker_write"])

    def test_non_ollama_nonzero_exclusion_provenance_fails_to_unresolved_label(self):
        spec = self.spec()
        result = self.run_main(self.identity(spec), self.reconstruction(spec, actor="deterministic_translator"))
        self.assertEqual(result["event_exclusion_basis"], "unresolved_provenance")

    def test_symbol_identity_drift_fails_closed(self):
        spec = self.spec()
        identity = self.identity(spec)
        metadata = identity["dataset_metadata"]
        assert isinstance(metadata, dict)
        metadata["symbol"] = "GBPUSD"
        with self.assertRaisesRegex(RuntimeError, "symbol differs"):
            self.run_main(identity, self.reconstruction(spec))

    def test_missing_dataset_coverage_fails_closed(self):
        spec = self.spec()
        identity = self.identity(spec)
        metadata = identity["dataset_metadata"]
        assert isinstance(metadata, dict)
        metadata.pop("first_bar_utc")
        with self.assertRaisesRegex(RuntimeError, "first_bar_utc"):
            self.run_main(identity, self.reconstruction(spec))


if __name__ == "__main__":
    unittest.main()
