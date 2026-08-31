from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.demo_certification import (
    ChaosCase,
    ChaosResult,
    DemoMilestoneEvidence,
    DeskRunEvidence,
    REQUIRED_MILESTONES,
    certify_demo_phase,
)
from dusty.demo_supervisor import (
    SQLiteSupervisorState,
    SupervisorPriority,
    admit_supervisor_job,
    assign_desks,
    decide_next_capital_cycle,
)
from dusty.growth import ResearchCycle
from dusty.resource import ResourceState


NOW = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40


class SupervisorTests(unittest.TestCase):
    def test_red_resource_state_preserves_position_supervision_but_throttles_research(self):
        self.assertTrue(admit_supervisor_job(SupervisorPriority.POSITION_SUPERVISION, ResourceState.RED).admitted)
        self.assertFalse(admit_supervisor_job(SupervisorPriority.RESEARCH, ResourceState.RED).admitted)

    def test_terminal_lease_prevents_overlapping_owners(self):
        db = SQLiteSupervisorState()
        try:
            first = db.acquire_lease("t1", "desk-a", at=NOW, duration=timedelta(minutes=5))
            second = db.acquire_lease("t1", "desk-b", at=NOW + timedelta(minutes=1), duration=timedelta(minutes=5))
            third = db.acquire_lease("t1", "desk-b", at=NOW + timedelta(minutes=6), duration=timedelta(minutes=5))
            self.assertIsNotNone(first)
            self.assertIsNone(second)
            self.assertIsNotNone(third)
            self.assertTrue(db.integrity_ok())
        finally:
            db.close()

    def test_sequential_desk_assignment_respects_terminal_capacity(self):
        db = SQLiteSupervisorState()
        try:
            assigned = assign_desks(("d1", "d2", "d3"), ("t1", "t2"), db, at=NOW)
            self.assertEqual(len(assigned), 2)
        finally:
            db.close()

    def test_failed_cycle_repeats_same_capital_and_pass_can_compress(self):
        failed = ResearchCycle(1000, 990, 0.02, 10, True, True, True)
        decision = decide_next_capital_cycle(failed, proposed_next_capital=800)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.next_starting_capital, 1000)

        passed = ResearchCycle(1000, 1050, 0.02, 10, True, True, True, 0.20)
        decision2 = decide_next_capital_cycle(passed, proposed_next_capital=800)
        self.assertTrue(decision2.passed)
        self.assertEqual(decision2.next_starting_capital, 800)


class CertificationTests(unittest.TestCase):
    def evidence(self):
        return tuple(
            DemoMilestoneEvidence(m, True, f"artifact-{m}", f"data-{m}", f"config-{m}", f"test-{m}", COMMIT)
            for m in REQUIRED_MILESTONES
        )

    def chaos(self):
        return tuple(ChaosResult(case, True, 0, f"chaos-{case.value}") for case in ChaosCase)

    def desks(self, count=6):
        return tuple(
            DeskRunEvidence(f"desk-{index}", f"gen-{index}", True, f"session-{index}", f"ledger-{index}", f"cycle-{index}")
            for index in range(count)
        )

    def test_six_desk_full_pass_certifies_demo_but_never_live(self):
        result = certify_demo_phase(self.evidence(), self.chaos(), self.desks(), current_commit_sha=COMMIT)
        self.assertTrue(result.demo_desk_certified)
        self.assertFalse(result.live_write_authorized)
        self.assertEqual(result.desk_pass_count, 6)

    def test_one_failed_desk_invalidates_entire_round(self):
        desks = list(self.desks())
        desks[2] = DeskRunEvidence("desk-2", "gen-2", False, "session-2", "ledger-2", "cycle-2")
        result = certify_demo_phase(self.evidence(), self.chaos(), desks, current_commit_sha=COMMIT)
        self.assertFalse(result.demo_desk_certified)
        self.assertIn("desk_failed:desk-2", result.reasons)

    def test_unsafe_chaos_attempt_blocks_certification(self):
        chaos = list(self.chaos())
        chaos[0] = ChaosResult(chaos[0].case, True, 1, chaos[0].artifact_hash)
        result = certify_demo_phase(self.evidence(), chaos, self.desks(), current_commit_sha=COMMIT)
        self.assertFalse(result.demo_desk_certified)
        self.assertTrue(any(reason.startswith("unsafe_entry_attempt:") for reason in result.reasons))

    def test_commit_mismatch_blocks_certification(self):
        evidence = list(self.evidence())
        evidence[0] = DemoMilestoneEvidence("M66", True, "a", "d", "c", "t", "b" * 40)
        result = certify_demo_phase(evidence, self.chaos(), self.desks(), current_commit_sha=COMMIT)
        self.assertFalse(result.demo_desk_certified)
        self.assertIn("commit_mismatch:M66", result.reasons)


if __name__ == "__main__":
    unittest.main()
