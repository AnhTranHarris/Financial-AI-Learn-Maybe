import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from dusty.long_running_soak import (
    LongRunningSoakEvidence,
    LongRunningSoakPolicy,
    LongRunningSoakStatus,
    SoakDisturbanceEvidence,
    SoakDisturbanceKind,
    SoakEvidenceMode,
    SoakRecoveryStatus,
    certify_long_running_soak,
)
from dusty.multi_desk_certification import CertifiedDeskEvidence, certify_multi_desk_generation
from dusty.single_desk_demo_certification import SingleDeskDemoStatus
from dusty.six_desk_graduation import GraduationGenerationEvidence, certify_six_desk_graduation


def _sha(ch: str) -> str:
    return ch * 64


def _desk(index: int, generation: str) -> CertifiedDeskEvidence:
    chars = "bcdef0123456789"
    cert = chars[index % len(chars)]
    account = chars[(index + 1) % len(chars)]
    terminal = chars[(index + 2) % len(chars)]
    runtime = chars[(index + 3) % len(chars)]
    return CertifiedDeskEvidence(
        desk_id=f"desk-{index}",
        generation_id=generation,
        single_desk_status=SingleDeskDemoStatus.CERTIFIED,
        single_desk_fingerprint=_sha(cert),
        champion_fingerprint=_sha("a"),
        account_fingerprint=_sha(account),
        terminal_fingerprint=_sha(terminal),
        broker_profile_fingerprint=_sha("9"),
        runtime_attestation_fingerprint=_sha(runtime),
    )


def _graduation():
    desks = tuple(_desk(i * 4, "generation-a") for i in range(6))
    cert = certify_multi_desk_generation("generation-a", desks)
    return certify_six_desk_graduation(
        _sha("a"),
        (GraduationGenerationEvidence(cert, desks),),
    )


def _event(kind: SoakDisturbanceKind, index: int, status=SoakRecoveryStatus.RECOVERED):
    return SoakDisturbanceEvidence(
        kind=kind,
        occurred_at=datetime(2026, 9, 7, 12, index, tzinfo=timezone.utc),
        recovery_status=status,
        evidence_fingerprint=_sha("123456789abcdef"[index]),
    )


def _policy():
    return LongRunningSoakPolicy(
        minimum_duration=timedelta(hours=12),
        minimum_heartbeats=12,
    )


def _evidence():
    return LongRunningSoakEvidence(
        graduation=_graduation(),
        source_commit="7" * 40,
        started_at=datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc),
        ended_at=datetime(2026, 9, 7, 13, 0, tzinfo=timezone.utc),
        heartbeat_count=52,
        disturbances=tuple(
            _event(kind, index)
            for index, kind in enumerate(SoakDisturbanceKind)
        ),
        duplicate_action_count=0,
        unauthorized_write_count=0,
        unresolved_reconciliation_count=0,
        state_integrity_ok=True,
        artifact_integrity_ok=True,
        final_safe_state=True,
    )


class M200LongRunningSoakTests(unittest.TestCase):
    def test_complete_evidence_certifies(self):
        result = certify_long_running_soak(_policy(), _evidence())
        self.assertIs(result.status, LongRunningSoakStatus.CERTIFIED)
        self.assertEqual(result.blockers, ())

    def test_short_duration_is_pending(self):
        evidence = replace(
            _evidence(),
            ended_at=datetime(2026, 9, 7, 6, 0, tzinfo=timezone.utc),
        )
        result = certify_long_running_soak(_policy(), evidence)
        self.assertIs(result.status, LongRunningSoakStatus.PENDING)
        self.assertIn("minimum_duration_not_met", result.blockers)

    def test_missing_required_disturbance_is_pending(self):
        evidence = replace(_evidence(), disturbances=_evidence().disturbances[:-1])
        result = certify_long_running_soak(_policy(), evidence)
        self.assertIs(result.status, LongRunningSoakStatus.PENDING)
        self.assertTrue(any(x.startswith("missing_disturbance:") for x in result.blockers))

    def test_unresolved_disturbance_rejects(self):
        rows = list(_evidence().disturbances)
        rows[0] = replace(rows[0], recovery_status=SoakRecoveryStatus.UNRESOLVED)
        result = certify_long_running_soak(_policy(), replace(_evidence(), disturbances=tuple(rows)))
        self.assertIs(result.status, LongRunningSoakStatus.REJECTED)
        self.assertIn("unresolved_disturbance", result.blockers)

    def test_unapproved_controlled_exercise_rejects(self):
        rows = list(_evidence().disturbances)
        rows[0] = replace(rows[0], mode=SoakEvidenceMode.CONTROLLED_EXERCISE)
        result = certify_long_running_soak(_policy(), replace(_evidence(), disturbances=tuple(rows)))
        self.assertIs(result.status, LongRunningSoakStatus.REJECTED)
        self.assertIn("unapproved_controlled_exercise:market_closure", result.blockers)

    def test_explicitly_allowed_controlled_exercise_can_satisfy_kind(self):
        rows = list(_evidence().disturbances)
        rows[0] = replace(rows[0], mode=SoakEvidenceMode.CONTROLLED_EXERCISE)
        policy = replace(
            _policy(),
            controlled_exercises_allowed=(SoakDisturbanceKind.MARKET_CLOSURE,),
        )
        result = certify_long_running_soak(policy, replace(_evidence(), disturbances=tuple(rows)))
        self.assertIs(result.status, LongRunningSoakStatus.CERTIFIED)

    def test_duplicate_action_rejects(self):
        result = certify_long_running_soak(_policy(), replace(_evidence(), duplicate_action_count=1))
        self.assertIs(result.status, LongRunningSoakStatus.REJECTED)

    def test_unauthorized_write_rejects(self):
        result = certify_long_running_soak(_policy(), replace(_evidence(), unauthorized_write_count=1))
        self.assertIs(result.status, LongRunningSoakStatus.REJECTED)

    def test_unresolved_reconciliation_rejects(self):
        result = certify_long_running_soak(
            _policy(), replace(_evidence(), unresolved_reconciliation_count=1)
        )
        self.assertIs(result.status, LongRunningSoakStatus.REJECTED)

    def test_integrity_failure_rejects(self):
        for kwargs in (
            {"state_integrity_ok": False},
            {"artifact_integrity_ok": False},
            {"final_safe_state": False},
        ):
            result = certify_long_running_soak(_policy(), replace(_evidence(), **kwargs))
            self.assertIs(result.status, LongRunningSoakStatus.REJECTED)

    def test_fingerprint_is_deterministic(self):
        left = certify_long_running_soak(_policy(), _evidence())
        right = certify_long_running_soak(_policy(), _evidence())
        self.assertEqual(left.fingerprint, right.fingerprint)

    def test_certification_has_no_operational_authority(self):
        result = certify_long_running_soak(_policy(), _evidence())
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)


if __name__ == "__main__":
    unittest.main()
