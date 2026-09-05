# M155 — Durable Experiment Queue & Resume Engine

## Scope

M155 begins the MT5 Experiment Factory tranche without granting any operational trading authority. Its purpose is to coordinate large numbers of bounded research experiments on one workstation while preserving exact identities, retries, failure evidence, and crash recovery.

The certified M154 workstation chain remains the rollback anchor. M155 adds a queue beside the existing research heartbeat; it does not replace the M154 blackboard/checkpoint runtime.

## Research basis

The design follows four externally validated patterns:

1. SQLite WAL allows concurrent readers while serializing writers. Queue state transitions therefore use short `BEGIN IMMEDIATE` transactions; expensive model/MT5 work happens after the lease transaction commits.
2. Durable workflow systems checkpoint completed work and resume from stable executor identities instead of replaying completed stages.
3. GitHub Actions supports rerunning failed jobs without rerunning successful work; Dusty applies the same principle locally at experiment granularity.
4. MetaTrader 5 Strategy Tester is itself a dispatcher over isolated tester agents. Dusty should queue immutable experiment specifications and later hand them to bounded tester workers rather than spawning uncontrolled processes from the queue layer.

## Durable job identity

Each `ExperimentJobSpec` is content-addressed from:

- proposal fingerprint
- strategy genome fingerprint
- experiment variant fingerprint
- research context fingerprint
- symbol/timeframe
- research school/fidelity
- resource class
- priority
- bounded maximum attempts

Enqueue is idempotent. The same specification cannot silently produce duplicate work.

## Resource classes

M155 defines routing classes only; it does not execute them:

- `cpu_research`
- `mt5_tester`
- `forecast`
- `ollama`

Later milestones can attach certified workers to these classes. Workers claim only the classes they are allowed to execute.

## Lease semantics

A worker claim is an atomic SQLite transaction. The queue:

- selects the highest-priority eligible job
- records a lease owner and expiry
- increments the attempt count once
- appends an immutable audit event
- commits before expensive work begins

A second worker cannot claim the same unexpired lease. Long MT5 jobs can renew a lease. A stale worker cannot complete a lease after it has expired or been reclaimed.

Expired leases are recoverable when attempts remain. If the final allowed attempt expires, the queue fails the job closed with `lease_expired_after_final_attempt` rather than looping forever.

## Retry discipline

Failures are explicit and bounded.

- Retryable failures return to `queued` with an optional bounded delay.
- Non-retryable failures transition directly to `failed`.
- Retryable failures also become `failed` once `max_attempts` is reached.
- No queue transition can convert research evidence into a Champion or trading authorization.

## Auditability

`experiment_events` is append-only and records:

- enqueue
- lease
- lease expiry
- lease renewal
- retry scheduling
- success
- terminal failure

The mutable job row is a current-state projection; the event table preserves the transition history.

## SQLite durability choices

M155 uses:

- `journal_mode=WAL`
- `synchronous=FULL`
- bounded `busy_timeout`
- foreign keys
- `PRAGMA integrity_check`
- optional passive WAL checkpoint inspection

The queue is intended for a single machine and local filesystem. WAL databases must not be placed on a network filesystem.

## Safety constitution

M155 exposes no:

- MT5 order API
- broker credentials
- broker writes
- live risk override
- entry veto authority
- Champion promotion authority

`broker_write_authorized` and `promotion_authorized` are permanently false.

## M155 certification gates

M155 is accepted only when all dedicated tests and smoke checks pass on Python 3.11 and 3.12 on both Ubuntu and Windows.

The tests prove:

- idempotent enqueue
- resource routing and priority
- one live lease across independent SQLite connections
- expired-lease reclaim
- stale-worker rejection
- bounded retry/dead-letter behavior
- lease renewal without duplicate attempts
- process restart persistence
- final expired attempt fails closed
- database integrity
- no broker/promotion authority

The cross-process smoke closes and reopens the database between attempts, retries a failed CPU job, renews a long MT5-class lease, completes both jobs, and verifies SQLite integrity.

## Next milestone

M156 should connect bounded worker supervision to M155 resource classes. The worker layer should add process lifecycle telemetry, stage-level timeouts, resource budgets, and checkpointed results while continuing to keep broker-write authority absent.
