from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from dusty.experiment_queue import (
    ExperimentJobSpec,
    ExperimentResource,
    ExperimentState,
    SQLiteExperimentQueue,
)


def _sha(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _spec(
    label: str,
    *,
    resource: ExperimentResource = ExperimentResource.CPU_RESEARCH,
    priority: int = 0,
    max_attempts: int = 3,
) -> ExperimentJobSpec:
    return ExperimentJobSpec(
        proposal_fingerprint=_sha(f"proposal:{label}"),
        genome_fingerprint=_sha(f"genome:{label}"),
        variant_fingerprint=_sha(f"variant:{label}"),
        context_fingerprint=_sha(f"context:{label}"),
        symbol="EURUSD",
        timeframe="M15",
        school="A1",
        fidelity="cheap-screen",
        resource=resource,
        priority=priority,
        max_attempts=max_attempts,
    )


class M155DurableExperimentQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 4, 23, 45, tzinfo=timezone.utc)

    def test_enqueue_is_idempotent_and_audited(self):
        queue = SQLiteExperimentQueue()
        spec = _spec("one")
        self.assertTrue(queue.enqueue(spec, now=self.now))
        self.assertFalse(queue.enqueue(spec, now=self.now + timedelta(seconds=1)))
        snapshot = queue.snapshot(spec.fingerprint)
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot.state, ExperimentState.QUEUED)
        self.assertEqual(snapshot.attempt_count, 0)
        self.assertEqual([event.event_type for event in queue.history(spec.fingerprint)], ["ENQUEUED"])
        self.assertTrue(queue.integrity_ok())
        self.assertFalse(queue.broker_write_authorized)
        self.assertFalse(queue.promotion_authorized)
        queue.close()

    def test_claim_filters_resource_and_prefers_priority(self):
        queue = SQLiteExperimentQueue()
        low = _spec("low", priority=1)
        high = _spec("high", priority=20)
        mt5 = _spec("mt5", resource=ExperimentResource.MT5_TESTER, priority=100)
        for spec in (low, high, mt5):
            queue.enqueue(spec, now=self.now)

        lease = queue.claim(
            "cpu-worker-1",
            resources=(ExperimentResource.CPU_RESEARCH,),
            now=self.now,
            lease_seconds=60,
        )
        self.assertIsNotNone(lease)
        assert lease is not None
        self.assertEqual(lease.job_fingerprint, high.fingerprint)
        self.assertEqual(lease.attempt, 1)
        self.assertEqual(queue.snapshot(mt5.fingerprint).state, ExperimentState.QUEUED)  # type: ignore[union-attr]
        queue.close()

    def test_two_connections_cannot_claim_same_live_lease(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.sqlite3"
            first = SQLiteExperimentQueue(path)
            second = SQLiteExperimentQueue(path)
            spec = _spec("shared")
            first.enqueue(spec, now=self.now)

            lease = first.claim(
                "worker-a",
                resources=(ExperimentResource.CPU_RESEARCH,),
                now=self.now,
                lease_seconds=60,
            )
            self.assertIsNotNone(lease)
            self.assertIsNone(
                second.claim(
                    "worker-b",
                    resources=(ExperimentResource.CPU_RESEARCH,),
                    now=self.now + timedelta(seconds=1),
                    lease_seconds=60,
                )
            )
            first.close()
            second.close()

    def test_expired_lease_is_reclaimed_and_stale_worker_rejected(self):
        queue = SQLiteExperimentQueue()
        spec = _spec("reclaim", max_attempts=3)
        queue.enqueue(spec, now=self.now)
        first = queue.claim(
            "worker-a",
            resources=(ExperimentResource.CPU_RESEARCH,),
            now=self.now,
            lease_seconds=10,
        )
        self.assertIsNotNone(first)

        reclaimed_at = self.now + timedelta(seconds=11)
        second = queue.claim(
            "worker-b",
            resources=(ExperimentResource.CPU_RESEARCH,),
            now=reclaimed_at,
            lease_seconds=30,
        )
        self.assertIsNotNone(second)
        assert second is not None
        self.assertEqual(second.attempt, 2)
        with self.assertRaises(RuntimeError):
            queue.complete(
                spec.fingerprint,
                "worker-a",
                result_fingerprint=_sha("stale-result"),
                now=reclaimed_at + timedelta(seconds=1),
            )
        queue.complete(
            spec.fingerprint,
            "worker-b",
            result_fingerprint=_sha("good-result"),
            now=reclaimed_at + timedelta(seconds=1),
        )
        self.assertEqual(queue.snapshot(spec.fingerprint).state, ExperimentState.SUCCEEDED)  # type: ignore[union-attr]
        self.assertEqual(
            [event.event_type for event in queue.history(spec.fingerprint)],
            ["ENQUEUED", "LEASED", "LEASE_EXPIRED", "LEASED", "SUCCEEDED"],
        )
        queue.close()

    def test_retry_is_bounded_and_final_failure_dead_letters_job(self):
        queue = SQLiteExperimentQueue()
        spec = _spec("retry", max_attempts=2)
        queue.enqueue(spec, now=self.now)

        first = queue.claim(
            "worker",
            resources=(ExperimentResource.CPU_RESEARCH,),
            now=self.now,
            lease_seconds=30,
        )
        assert first is not None
        state = queue.fail(
            spec.fingerprint,
            "worker",
            error="synthetic transient failure",
            now=self.now + timedelta(seconds=1),
            retryable=True,
            retry_delay_seconds=5,
        )
        self.assertEqual(state, ExperimentState.QUEUED)
        self.assertIsNone(
            queue.claim(
                "worker",
                resources=(ExperimentResource.CPU_RESEARCH,),
                now=self.now + timedelta(seconds=2),
            )
        )

        second_at = self.now + timedelta(seconds=6)
        second = queue.claim(
            "worker",
            resources=(ExperimentResource.CPU_RESEARCH,),
            now=second_at,
            lease_seconds=30,
        )
        assert second is not None
        state = queue.fail(
            spec.fingerprint,
            "worker",
            error="still failing",
            now=second_at + timedelta(seconds=1),
            retryable=True,
        )
        self.assertEqual(state, ExperimentState.FAILED)
        snapshot = queue.snapshot(spec.fingerprint)
        assert snapshot is not None
        self.assertEqual(snapshot.attempt_count, 2)
        self.assertEqual(snapshot.state, ExperimentState.FAILED)
        self.assertIsNone(
            queue.claim(
                "worker",
                resources=(ExperimentResource.CPU_RESEARCH,),
                now=second_at + timedelta(seconds=2),
            )
        )
        queue.close()

    def test_long_job_can_renew_lease_without_changing_attempt(self):
        queue = SQLiteExperimentQueue()
        spec = _spec("renew", resource=ExperimentResource.MT5_TESTER)
        queue.enqueue(spec, now=self.now)
        lease = queue.claim(
            "mt5-agent-1",
            resources=(ExperimentResource.MT5_TESTER,),
            now=self.now,
            lease_seconds=60,
        )
        assert lease is not None
        renewed = queue.renew_lease(
            spec.fingerprint,
            "mt5-agent-1",
            now=self.now + timedelta(seconds=30),
            lease_seconds=120,
        )
        self.assertGreater(renewed, lease.lease_until)
        snapshot = queue.snapshot(spec.fingerprint)
        assert snapshot is not None
        self.assertEqual(snapshot.attempt_count, 1)
        self.assertEqual(snapshot.lease_owner, "mt5-agent-1")
        queue.close()

    def test_restart_preserves_queue_and_final_expired_lease_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "queue.sqlite3"
            queue = SQLiteExperimentQueue(path)
            spec = _spec("restart", max_attempts=1)
            queue.enqueue(spec, now=self.now)
            lease = queue.claim(
                "worker-a",
                resources=(ExperimentResource.CPU_RESEARCH,),
                now=self.now,
                lease_seconds=5,
            )
            self.assertIsNotNone(lease)
            queue.close()

            reopened = SQLiteExperimentQueue(path)
            self.assertIsNone(
                reopened.claim(
                    "worker-b",
                    resources=(ExperimentResource.CPU_RESEARCH,),
                    now=self.now + timedelta(seconds=6),
                )
            )
            snapshot = reopened.snapshot(spec.fingerprint)
            assert snapshot is not None
            self.assertEqual(snapshot.state, ExperimentState.FAILED)
            self.assertEqual(snapshot.last_error, "lease_expired_after_final_attempt")
            self.assertTrue(reopened.integrity_ok())
            busy, log_frames, checkpointed = reopened.checkpoint_wal()
            self.assertGreaterEqual(busy, 0)
            self.assertGreaterEqual(log_frames, checkpointed)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
