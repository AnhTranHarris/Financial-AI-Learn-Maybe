from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from dusty.m194_native_demo_journal import (
    M194NativeEventKind,
    M194NativeEvidenceEvent,
    SQLiteM194NativeEvidenceJournal,
)


NOW = datetime(2026, 9, 8, 6, 0, tzinfo=timezone.utc)
COMMIT = "a" * 40
CHAMPION = "b" * 64


def event(kind: M194NativeEventKind, *, index: int = 0, run_id: str = "desk-run-001", commit: str = COMMIT):
    return M194NativeEvidenceEvent(
        run_id=run_id,
        kind=kind,
        occurred_at=NOW + timedelta(seconds=index),
        source_commit=commit,
        champion_fingerprint=CHAMPION,
        evidence_fingerprints=(f"{index + 1:064x}",),
        payload={"index": index, "kind": kind.value},
    )


class M1941NativeDemoJournalTests(unittest.TestCase):
    def test_run_is_append_only_and_summarized(self):
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            journal.append(event(M194NativeEventKind.RUN_STARTED))
            journal.append(event(M194NativeEventKind.HEARTBEAT, index=1))
            journal.append(event(M194NativeEventKind.COMPLETED_CYCLE, index=2))
            journal.append(event(M194NativeEventKind.RECONCILED_EXECUTION, index=3))
            journal.append(event(M194NativeEventKind.EXECUTION_COST_SAMPLE, index=4))
            journal.append(event(M194NativeEventKind.RECOVERY_CHECKPOINT, index=5))
            journal.append(event(M194NativeEventKind.OPERATIONAL_EXERCISE, index=6))
            journal.append(event(M194NativeEventKind.RUN_ENDED, index=7))
            summary = journal.summary("desk-run-001")
            self.assertTrue(summary["started"])
            self.assertTrue(summary["ended"])
            self.assertEqual(summary["heartbeats"], 1)
            self.assertEqual(summary["operational_exercises"], 1)

    def test_non_start_event_requires_started_run(self):
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            with self.assertRaisesRegex(ValueError, "must start"):
                journal.append(event(M194NativeEventKind.HEARTBEAT, index=1))

    def test_run_identity_drift_is_rejected(self):
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            journal.append(event(M194NativeEventKind.RUN_STARTED))
            with self.assertRaisesRegex(ValueError, "identity drift"):
                journal.append(event(M194NativeEventKind.HEARTBEAT, index=1, commit="c" * 40))

    def test_run_id_cannot_be_reused(self):
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            journal.append(event(M194NativeEventKind.RUN_STARTED))
            with self.assertRaisesRegex(ValueError, "already exists"):
                journal.append(event(M194NativeEventKind.RUN_STARTED, index=1))

    def test_closed_run_rejects_new_evidence(self):
        with TemporaryDirectory() as root:
            journal = SQLiteM194NativeEvidenceJournal(Path(root) / "m194.db")
            journal.append(event(M194NativeEventKind.RUN_STARTED))
            journal.append(event(M194NativeEventKind.RUN_ENDED, index=1))
            with self.assertRaisesRegex(ValueError, "already closed"):
                journal.append(event(M194NativeEventKind.HEARTBEAT, index=2))

    def test_tampering_is_detected_on_read(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "m194.db"
            journal = SQLiteM194NativeEvidenceJournal(path)
            journal.append(event(M194NativeEventKind.RUN_STARTED))
            with closing(sqlite3.connect(path)) as db:
                db.execute("UPDATE m194_native_events SET payload_json=?", ('{"index":999}',))
                db.commit()
            with self.assertRaisesRegex(RuntimeError, "integrity failure"):
                journal.events("desk-run-001")


if __name__ == "__main__":
    unittest.main()
