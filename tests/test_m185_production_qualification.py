from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import unittest

from dusty.m185_production_qualification import (
    QUALIFICATION_STAGES,
    ProductionQualificationPlan,
    build_production_qualification_plan,
)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateSpecStub:
    strategy_hash: str


@dataclass(frozen=True)
class ReconstructionStub:
    fingerprint: str
    proposal_fingerprint: str
    candidate_spec: CandidateSpecStub
    source_id: str
    source_url: str
    source_content_sha256: str
    source_family_fingerprint: str
    title: str
    symbols: tuple[str, ...]
    timeframe: str
    source_claim_complete: bool
    hypothesis_rule_count: int


class M185ProductionQualificationTests(unittest.TestCase):
    def reconstruction(self, *, name: str = "alpha", symbols: tuple[str, ...] = ("EURUSD", "GBPUSD")) -> ReconstructionStub:
        return ReconstructionStub(
            fingerprint=fp(f"reconstruction-{name}"),
            proposal_fingerprint=fp(f"proposal-{name}"),
            candidate_spec=CandidateSpecStub(fp(f"strategy-{name}")),
            source_id=f"source-{name}",
            source_url=f"https://example.com/{name}",
            source_content_sha256=fp(f"content-{name}"),
            source_family_fingerprint=fp(f"family-{name}"),
            title=f"Strategy {name}",
            symbols=symbols,
            timeframe="M15",
            source_claim_complete=False,
            hypothesis_rule_count=2,
        )

    def test_every_candidate_symbol_becomes_a_qualification_lane(self) -> None:
        now = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
        plan = build_production_qualification_plan(
            (self.reconstruction(),),
            estate_sha256=fp("estate"),
            source_commit="1" * 40,
            created_at=now,
        )
        self.assertEqual(len(plan.manifests), 2)
        self.assertEqual({row.symbol for row in plan.manifests}, {"EURUSD", "GBPUSD"})
        self.assertTrue(all(row.required_stages == QUALIFICATION_STAGES for row in plan.manifests))
        self.assertTrue(all(row.hypothesis_rule_count == 2 for row in plan.manifests))

    def test_plan_is_deterministic_and_has_no_trading_or_promotion_authority(self) -> None:
        now = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
        kwargs = dict(estate_sha256=fp("estate"), source_commit="2" * 40, created_at=now)
        first = build_production_qualification_plan((self.reconstruction(name="b"), self.reconstruction(name="a")), **kwargs)
        second = build_production_qualification_plan((self.reconstruction(name="a"), self.reconstruction(name="b")), **kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first.fingerprint, second.fingerprint)
        for row in first.manifests:
            self.assertFalse(row.broker_write_authority)
            self.assertFalse(row.live_write_authority)
            self.assertFalse(row.promotion_authority)
            self.assertFalse(row.risk_override_authority)
            self.assertFalse(row.guardian_override_authority)

    def test_empty_estate_and_short_git_sha_fail_closed(self) -> None:
        now = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
        with self.assertRaisesRegex(ValueError, "at least one manifest"):
            build_production_qualification_plan((), estate_sha256=fp("estate"), source_commit="3" * 40, created_at=now)
        with self.assertRaisesRegex(ValueError, "full 40-character"):
            build_production_qualification_plan((self.reconstruction(),), estate_sha256=fp("estate"), source_commit="abc123", created_at=now)

    def test_duplicate_lane_reconstruction_identity_fails_closed(self) -> None:
        now = datetime(2026, 9, 8, 2, 0, tzinfo=timezone.utc)
        row = self.reconstruction(symbols=("EURUSD",))
        with self.assertRaisesRegex(ValueError, "duplicate lane"):
            build_production_qualification_plan((row, row), estate_sha256=fp("estate"), source_commit="4" * 40, created_at=now)


if __name__ == "__main__":
    unittest.main()
