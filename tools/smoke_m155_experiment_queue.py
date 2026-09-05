from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path

from dusty.experiment_queue import (
    ExperimentJobSpec,
    ExperimentResource,
    ExperimentState,
    SQLiteExperimentQueue,
)


def _sha(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _spec(label: str, resource: ExperimentResource, priority: int) -> ExperimentJobSpec:
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
        max_attempts=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-root", required=True)
    args = parser.parse_args()

    root = Path(args.work_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "m155-queue.sqlite3"
    if path.exists():
        path.unlink()

    now = datetime(2026, 9, 4, 23, 45, tzinfo=timezone.utc)
    cpu = _spec("cpu", ExperimentResource.CPU_RESEARCH, 10)
    mt5 = _spec("mt5", ExperimentResource.MT5_TESTER, 20)

    first = SQLiteExperimentQueue(path)
    assert first.enqueue(cpu, now=now)
    assert first.enqueue(mt5, now=now)
    assert not first.enqueue(cpu, now=now + timedelta(seconds=1))

    lease = first.claim(
        "cpu-worker",
        resources=(ExperimentResource.CPU_RESEARCH,),
        now=now,
        lease_seconds=30,
    )
    assert lease is not None and lease.job_fingerprint == cpu.fingerprint
    state = first.fail(
        cpu.fingerprint,
        "cpu-worker",
        error="synthetic retry",
        now=now + timedelta(seconds=1),
        retryable=True,
        retry_delay_seconds=2,
    )
    assert state is ExperimentState.QUEUED
    first.close()

    second = SQLiteExperimentQueue(path)
    retried = second.claim(
        "cpu-worker-2",
        resources=(ExperimentResource.CPU_RESEARCH,),
        now=now + timedelta(seconds=3),
        lease_seconds=30,
    )
    assert retried is not None and retried.attempt == 2
    second.complete(
        cpu.fingerprint,
        "cpu-worker-2",
        result_fingerprint=_sha("cpu-result"),
        now=now + timedelta(seconds=4),
    )

    mt5_lease = second.claim(
        "mt5-agent-1",
        resources=(ExperimentResource.MT5_TESTER,),
        now=now + timedelta(seconds=5),
        lease_seconds=30,
    )
    assert mt5_lease is not None and mt5_lease.job_fingerprint == mt5.fingerprint
    second.renew_lease(
        mt5.fingerprint,
        "mt5-agent-1",
        now=now + timedelta(seconds=10),
        lease_seconds=60,
    )
    second.complete(
        mt5.fingerprint,
        "mt5-agent-1",
        result_fingerprint=_sha("mt5-result"),
        now=now + timedelta(seconds=11),
    )

    counts = second.counts()
    assert counts[ExperimentState.SUCCEEDED] == 2
    assert second.integrity_ok()
    busy, log_frames, checkpointed = second.checkpoint_wal()
    report = {
        "protocol": "dusty-m155-durable-experiment-queue-smoke-v1",
        "status": "pass",
        "queue_path": str(path),
        "counts": {state.value: count for state, count in counts.items()},
        "cpu_events": [event.event_type for event in second.history(cpu.fingerprint)],
        "mt5_events": [event.event_type for event in second.history(mt5.fingerprint)],
        "wal_checkpoint": {"busy": busy, "log_frames": log_frames, "checkpointed": checkpointed},
        "safety": {
            "broker_write": second.broker_write_authorized,
            "promotion": second.promotion_authorized,
        },
    }
    second.close()
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
