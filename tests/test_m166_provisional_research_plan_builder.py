from __future__ import annotations

from hashlib import sha256
import unittest

from tools.build_m166_provisional_research_plan import build_payload


def fp(value: str) -> str:
    return sha256(value.encode()).hexdigest()


class M166ProvisionalResearchPlanBuilderTests(unittest.TestCase):
    def fixtures(self):
        lane = "eurusd:m15:test"
        strategy = fp("strategy")
        head = "1" * 40
        qualification = {
            "manifests": [
                {
                    "lane_id": lane,
                    "strategy_hash": strategy,
                    "manifest_fingerprint": fp("manifest"),
                }
            ]
        }
        discovery = {
            "source_commit": head,
            "status": "unique_candidate",
            "lane_id": lane,
            "qualification_strategy_hash": strategy,
            "identity_set_count": 1,
            "identity_sets": [
                {
                    "strategy_fingerprint": strategy,
                    "dataset_fingerprint": fp("dataset"),
                    "parameter_fingerprint": fp("parameter"),
                    "sources": [{"source_path": "artifact.json"}],
                }
            ],
        }
        custody = {
            "observation_count": 20,
            "distinct_days": 2,
            "calibration": {"status": "insufficient"},
        }
        return head, lane, strategy, qualification, discovery, custody

    def test_builds_provisional_checkpoint_without_production_authority(self) -> None:
        head, lane, strategy, qualification, discovery, custody = self.fixtures()
        payload = build_payload(
            expected_head=head,
            lane_id=lane,
            qualification=qualification,
            discovery=discovery,
            custody_summary=custody,
            calibration_fingerprint=fp("calibration-20-2"),
        )
        self.assertEqual(payload["status"], "provisional_research_ready")
        self.assertEqual(payload["source_commit"], head)
        self.assertEqual(payload["qualification_strategy_hash"], strategy)
        self.assertEqual(payload["current_m165_status"], "insufficient")
        self.assertFalse(payload["production_semantics"]["m166_production_admission_granted"])
        self.assertFalse(payload["production_semantics"]["m174_production_certification_granted"])
        self.assertFalse(payload["production_semantics"]["m185_eligible"])
        self.assertEqual(
            payload["production_semantics"]["final_calibration_revalidation"],
            ["m170_cost_torture", "m174_robustness"],
        )
        self.assertFalse(payload["authority"]["broker_write"])
        self.assertFalse(payload["authority"]["promotion"])

    def test_rejects_ambiguous_or_strategy_drifted_discovery(self) -> None:
        head, lane, _, qualification, discovery, custody = self.fixtures()
        ambiguous = dict(discovery)
        ambiguous["status"] = "ambiguous_candidates"
        ambiguous["identity_set_count"] = 2
        with self.assertRaises(PermissionError):
            build_payload(
                expected_head=head,
                lane_id=lane,
                qualification=qualification,
                discovery=ambiguous,
                custody_summary=custody,
                calibration_fingerprint=fp("calibration"),
            )

        drifted = dict(discovery)
        drifted["qualification_strategy_hash"] = fp("other")
        with self.assertRaises(ValueError):
            build_payload(
                expected_head=head,
                lane_id=lane,
                qualification=qualification,
                discovery=drifted,
                custody_summary=custody,
                calibration_fingerprint=fp("calibration"),
            )

    def test_rejects_discovery_from_different_git_head(self) -> None:
        head, lane, _, qualification, discovery, custody = self.fixtures()
        stale = dict(discovery)
        stale["source_commit"] = "2" * 40
        with self.assertRaisesRegex(ValueError, "source commit"):
            build_payload(
                expected_head=head,
                lane_id=lane,
                qualification=qualification,
                discovery=stale,
                custody_summary=custody,
                calibration_fingerprint=fp("calibration"),
            )

    def test_requires_real_nonempty_m165_breadth(self) -> None:
        head, lane, _, qualification, discovery, custody = self.fixtures()
        empty = dict(custody)
        empty["observation_count"] = 0
        with self.assertRaises(PermissionError):
            build_payload(
                expected_head=head,
                lane_id=lane,
                qualification=qualification,
                discovery=discovery,
                custody_summary=empty,
                calibration_fingerprint=fp("calibration"),
            )


if __name__ == "__main__":
    unittest.main()
