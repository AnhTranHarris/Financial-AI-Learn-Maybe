from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dusty.long_running_soak import (
    SoakDisturbanceEvidence,
    SoakDisturbanceKind,
    SoakEvidenceMode,
    SoakRecoveryStatus,
)
from dusty.m200_soak_evidence import (
    SQLiteSoakEvidenceStore,
    SoakHeartbeatEvidence,
    SoakRunIdentity,
)


def h(ch: str) -> str:
    return ch * 64


NOW = datetime(2026, 9, 7, 20, 0, tzinfo=timezone.utc)


def identity() -> SoakRunIdentity:
    return SoakRunIdentity(
        run_id="m200-test",
        source_commit="7" * 40,
        terminal_fingerprint=h("a"),
        account_fingerprint=h("b"),
        started_at=NOW,
        baseline_artifact_fingerprint=h("c"),
    )


def heartbeat(at: datetime, pid: int, previous: str | None = None) -> SoakHeartbeatEvidence:
    return SoakHeartbeatEvidence(
        observed_at=at,
        process_id=pid,
        terminal_connected=True,
        terminal_fingerprint=h("a"),
        account_fingerprint=h("b"),
        positions_count=0,
        orders_count=0,
        state_fingerprint=h("d"),
        artifact_fingerprint=h("c"),
        previous_record_fingerprint=previous,
    )


class M200SoakEvidenceStoreTests(unittest.TestCase):
    def make_store(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "soak.sqlite3"
        store = SQLiteSoakEvidenceStore(path)
        store.initialize(identity())
        return tmp, store

    def test_append_only_heartbeat_chain_survives_reopen(self):
        tmp, store = self.make_store()
        try:
            first = heartbeat(NOW + timedelta(minutes=1), 100)
            store.append_heartbeat(first)
            second = heartbeat(NOW + timedelta(hours=1), 200, first.fingerprint)
            store.append_heartbeat(second)
            self.assertTrue(store.integrity_ok())
            self.assertTrue(store.process_restart_observed())
            store.close()

            reopened = SQLiteSoakEvidenceStore(Path(tmp.name) / "soak.sqlite3")
            self.assertEqual(reopened.identity(), identity())
            self.assertEqual(len(reopened.heartbeats()), 2)
            self.assertTrue(reopened.integrity_ok())
            reopened.close()
        finally:
            tmp.cleanup()

    def test_terminal_or_account_identity_drift_fails_closed(self):
        tmp, store = self.make_store()
        try:
            bad_terminal = heartbeat(NOW + timedelta(minutes=1), 100)
            object.__setattr__(bad_terminal, "terminal_fingerprint", h("e"))
            with self.assertRaisesRegex(ValueError, "terminal identity drift"):
                store.append_heartbeat(bad_terminal)

            bad_account = heartbeat(NOW + timedelta(minutes=2), 100)
            object.__setattr__(bad_account, "account_fingerprint", h("e"))
            with self.assertRaisesRegex(ValueError, "account identity drift"):
                store.append_heartbeat(bad_account)
        finally:
            store.close()
            tmp.cleanup()

    def test_wrong_previous_hash_or_nonadvancing_time_fails_closed(self):
        tmp, store = self.make_store()
        try:
            first = heartbeat(NOW + timedelta(minutes=1), 100)
            store.append_heartbeat(first)
            with self.assertRaisesRegex(ValueError, "hash chain mismatch"):
                store.append_heartbeat(heartbeat(NOW + timedelta(minutes=2), 101, h("f")))
            with self.assertRaisesRegex(ValueError, "must advance in real time"):
                store.append_heartbeat(heartbeat(first.observed_at, 101, first.fingerprint))
        finally:
            store.close()
            tmp.cleanup()

    def test_disturbance_mode_round_trips_without_upgrading_evidence(self):
        tmp, store = self.make_store()
        try:
            evidence = SoakDisturbanceEvidence(
                kind=SoakDisturbanceKind.PROVIDER_OR_MODEL_FAILURE,
                occurred_at=NOW + timedelta(minutes=5),
                recovery_status=SoakRecoveryStatus.RECOVERED,
                evidence_fingerprint=h("8"),
                mode=SoakEvidenceMode.CONTROLLED_EXERCISE,
            )
            store.append_disturbance(evidence)
            rows = store.disturbances()
            self.assertEqual(rows, (evidence,))
            self.assertIs(rows[0].mode, SoakEvidenceMode.CONTROLLED_EXERCISE)
        finally:
            store.close()
            tmp.cleanup()

    def test_store_never_exposes_operational_authority(self):
        tmp, store = self.make_store()
        try:
            self.assertFalse(hasattr(store, "order_send"))
            self.assertFalse(hasattr(store, "broker_write_authority"))
            self.assertFalse(hasattr(store, "live_write_authority"))
        finally:
            store.close()
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
