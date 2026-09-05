from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import unittest

from dusty.experiment_manifest import (
    BrokerAssumptions,
    ComputeRequest,
    EvaluationPlan,
    EvaluationStage,
    ExperimentManifest,
    ExperimentWindow,
    FeatureRef,
    ManifestOrigin,
)
from dusty.experiment_queue import ExperimentResource


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _manifest(**overrides: object) -> ExperimentManifest:
    values: dict[str, object] = {
        "experiment_id": "DD-EXP-000014728",
        "hypothesis_id": "HYP-NAS-ASIA-COMPRESSION",
        "hypothesis": "Asia-session compression improves later continuation expectancy.",
        "origin": ManifestOrigin.USER_CARSON,
        "proposal_fingerprint": _fp("proposal"),
        "strategy_fingerprint": _fp("strategy"),
        "variant_fingerprint": _fp("variant"),
        "context_fingerprint": _fp("context"),
        "strategy_ancestry_fingerprints": (_fp("ancestor-b"), _fp("ancestor-a")),
        "source_provenance_fingerprints": (_fp("source-b"), _fp("source-a")),
        "parent_manifest_fingerprints": (),
        "software_commit": "a" * 40,
        "dataset_fingerprint": _fp("dataset-v1"),
        "features": (
            FeatureRef("relative_volume", "v1", _fp("relative-volume-v1")),
            FeatureRef("asia_range", "v2", _fp("asia-range-v2")),
        ),
        "broker": BrokerAssumptions(
            profile_fingerprint=_fp("broker-profile"),
            cost_model_fingerprint=_fp("cost-model"),
            account_currency="USD",
            initial_balance=10_000.0,
            leverage=100,
            execution_model="native_mt5_research",
        ),
        "seed": 442901,
        "windows": (
            ExperimentWindow(
                "holdout",
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            ExperimentWindow(
                "development",
                datetime(2020, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
        ),
        "symbols": ("NAS100", "EURUSD"),
        "timeframes": ("M15", "H1"),
        "research_school": "edge_discovery",
        "fidelity": "python_screen",
        "evaluation": EvaluationPlan(
            stage=EvaluationStage.A1,
            policy_fingerprint=_fp("a1-policy"),
            required_metrics=("expectancy", "trade_count", "drawdown"),
            minimum_trades=30,
            walk_forward_required=False,
            cost_stress_required=False,
        ),
        "risk_policy_fingerprint": _fp("research-risk-policy"),
        "risk_assumptions": (("position_size", "minimum_lot"), ("risk_mode", "research_only")),
        "compute": ComputeRequest(
            resource=ExperimentResource.CPU_RESEARCH,
            max_wall_seconds=300,
            max_ram_mb=2048,
            max_workers=2,
            gpu_allowed=False,
        ),
        "expected_outputs": ("normalized_metrics.json", "trade_ledger.json"),
        "created_at": datetime(2026, 9, 4, 23, 59, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return ExperimentManifest(**values)  # type: ignore[arg-type]


class M155ExperimentManifestTests(unittest.TestCase):
    def test_manifest_is_canonical_under_semantically_irrelevant_ordering(self) -> None:
        left = _manifest()
        right = _manifest(
            strategy_ancestry_fingerprints=tuple(reversed(left.strategy_ancestry_fingerprints)),
            source_provenance_fingerprints=tuple(reversed(left.source_provenance_fingerprints)),
            features=tuple(reversed(left.features)),
            windows=tuple(reversed(left.windows)),
            symbols=tuple(reversed(left.symbols)),
            timeframes=tuple(reversed(left.timeframes)),
            risk_assumptions=tuple(reversed(left.risk_assumptions)),
            expected_outputs=tuple(reversed(left.expected_outputs)),
        )
        self.assertEqual(left.fingerprint, right.fingerprint)
        self.assertEqual(left.execution_fingerprint, right.execution_fingerprint)
        self.assertEqual(json.loads(left.canonical_record())["manifest_fingerprint"], left.fingerprint)

    def test_execution_change_creates_new_evidence_identity(self) -> None:
        original = _manifest()
        changed = _manifest(dataset_fingerprint=_fp("dataset-v2"))
        self.assertNotEqual(original.execution_fingerprint, changed.execution_fingerprint)
        self.assertNotEqual(original.fingerprint, changed.fingerprint)

    def test_hypothesis_change_preserves_reusable_execution_identity(self) -> None:
        original = _manifest()
        changed = _manifest(
            hypothesis_id="HYP-NAS-ALTERNATE-RATIONALE",
            hypothesis="Same executable test, different research rationale.",
        )
        self.assertEqual(original.execution_fingerprint, changed.execution_fingerprint)
        self.assertNotEqual(original.fingerprint, changed.fingerprint)

    def test_compute_request_changes_manifest_not_execution_cache_identity(self) -> None:
        original = _manifest()
        changed = _manifest(
            compute=ComputeRequest(
                resource=ExperimentResource.CPU_RESEARCH,
                max_wall_seconds=600,
                max_ram_mb=4096,
                max_workers=4,
                gpu_allowed=False,
            )
        )
        self.assertEqual(original.execution_fingerprint, changed.execution_fingerprint)
        self.assertNotEqual(original.fingerprint, changed.fingerprint)

    def test_display_record_identity_does_not_defeat_scientific_deduplication(self) -> None:
        original = _manifest()
        later = _manifest(
            experiment_id="DD-EXP-000014729",
            created_at=datetime(2026, 9, 5, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(original.fingerprint, later.fingerprint)
        self.assertEqual(original.execution_fingerprint, later.execution_fingerprint)
        self.assertNotEqual(original.record_fingerprint, later.record_fingerprint)

    def test_queue_binding_is_manifest_bound_and_research_only(self) -> None:
        manifest = _manifest()
        spec = manifest.to_queue_spec(symbol="nas100", timeframe="m15", priority=7, max_attempts=2)
        self.assertEqual(spec.context_fingerprint, manifest.fingerprint)
        self.assertEqual(spec.genome_fingerprint, manifest.strategy_fingerprint)
        self.assertEqual(spec.variant_fingerprint, manifest.variant_fingerprint)
        self.assertEqual(spec.resource, ExperimentResource.CPU_RESEARCH)
        self.assertEqual(spec.priority, 7)
        self.assertEqual(spec.max_attempts, 2)

    def test_manifest_rejects_any_operational_authority(self) -> None:
        for field in (
            "broker_write_authority",
            "risk_override_authority",
            "entry_veto_authority",
            "promotion_authority",
        ):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    _manifest(**{field: True})

    def test_manifest_rejects_undeclared_queue_binding(self) -> None:
        manifest = _manifest()
        with self.assertRaises(ValueError):
            manifest.to_queue_spec(symbol="GBPUSD", timeframe="M15")
        with self.assertRaises(ValueError):
            manifest.to_queue_spec(symbol="EURUSD", timeframe="M5")

    def test_feature_or_window_corruption_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            _manifest(features=())
        with self.assertRaises(ValueError):
            ExperimentWindow(
                "bad",
                datetime(2025, 1, 2, tzinfo=timezone.utc),
                datetime(2025, 1, 1, tzinfo=timezone.utc),
            )
        with self.assertRaises(ValueError):
            replace(_manifest().features[0], fingerprint="not-a-sha")


if __name__ == "__main__":
    unittest.main()
