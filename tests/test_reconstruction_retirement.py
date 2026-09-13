from __future__ import annotations

import unittest

from dusty.reconstruction_retirement import retire_dead_reconstruction


H = lambda ch: ch * 64


def audit(*, status: str = "dead_reconstruction", matches: int = 0) -> dict[str, object]:
    return {
        "strategy_fingerprint": H("a"),
        "reconstruction_fingerprint": H("b"),
        "audit_fingerprint": H("c"),
        "assessment": {"status": status, "entry_match_count": matches},
    }


def receipt(*, tuned: bool = False) -> dict[str, object]:
    return {
        "child_strategy_fingerprint": H("a"),
        "child_reconstruction_fingerprint": H("b"),
        "parent_strategy_fingerprint": H("d"),
        "parent_reconstruction_fingerprint": H("e"),
        "parent_preserved": True,
        "threshold_tuning_performed": tuned,
    }


class ReconstructionRetirementTests(unittest.TestCase):
    def test_dead_conjunction_creates_exact_lineage_retirement_without_theory_rejection(self) -> None:
        row = retire_dead_reconstruction(audit(), receipt())
        self.assertEqual(row.reason, "dead_entry_conjunction_on_bounded_pit_training")
        self.assertEqual(row.payload["scope"], "exact_reconstruction_lineage_only")
        self.assertFalse(row.payload["underlying_trading_theory_rejected"])
        self.assertFalse(row.payload["threshold_tuning_authorized"])
        self.assertFalse(row.payload["automatic_retry_authorized"])
        self.assertTrue(all(value is False for value in row.payload["authority"].values()))
        self.assertEqual(len(row.fingerprint), 64)

    def test_activatable_or_nonzero_match_candidate_cannot_be_retired_as_dead(self) -> None:
        with self.assertRaises(ValueError):
            retire_dead_reconstruction(audit(status="activatable", matches=1), receipt())
        with self.assertRaises(ValueError):
            retire_dead_reconstruction(audit(matches=1), receipt())

    def test_threshold_tuned_child_cannot_use_semantic_dead_retirement_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "threshold-tuned"):
            retire_dead_reconstruction(audit(), receipt(tuned=True))

    def test_identity_drift_fails_closed(self) -> None:
        bad = receipt()
        bad["child_strategy_fingerprint"] = H("f")
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            retire_dead_reconstruction(audit(), bad)


if __name__ == "__main__":
    unittest.main()
