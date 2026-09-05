# M155 Support Layer — Durable Experiment Queue & Resume Engine

## Scope

The durable queue is a supporting runtime inside M155, not the scientific definition of an experiment. The primary M155 contract is now the immutable `ExperimentManifest` documented in `m155-experiment-constitution.md`.

The queue coordinates bounded research work on one workstation while preserving exact identities, retries, failure evidence, and crash recovery. The certified M154 workstation chain remains the rollback anchor. M155 adds both the scientific manifest and this queue beside the existing M154 blackboard/checkpoint runtime; neither grants operational trading authority.

## Research basis

The design follows four externally validated patterns:

1. SQLite WAL allows concurrent readers while serializing writers. Queue state transitions therefore use short `BEGIN IMMEDIATE` transactions; expensive model/MT5 work happens after the lease transaction commits.
2. Durable workflow systems checkpoint completed work and resume from stable executor identities instead of replaying completed stages.
3. GitHub Actions supports rerunning failed jobs without rerunning successful work; Dusty applies the same principle locally at experiment granularity.
4. MetaTrader 5 Strategy Tester is itself a dispatcher over isolated tester agents. Dusty should queue immutable experiment specifications and later hand them to bounded tester workers rather than spawning uncontrolled processes from the queue layer.

## Manifest binding

A queue job produced from an `ExperimentManifest` binds its research context field to the manifest fingerprint. The queue therefore cannot silently execute a job detached from the scientific contract that defined it.

The manifest itself records:

- hypothesis and origin
- strategy/proposal/variant/context ancestry
- code commit
- dataset fingerprint
- feature versions/fingerprints
- broker and cost assumptions
- seed and time windows
- A1/A2/A3 evaluation policy
- risk assumptions
- requested compute
- expected outputs
- permanent research-only authority

The queue remains an execution coordinator. The manifest remains the source of scientific truth.

## Durable job identity

Each `ExperimentJobSpec` is content-addressed from its queue-facing execution fields. Enqueue is idempotent. A manifest-bound job uses the manifest fingerprint as its queue context identity so a scientific-contract change creates a distinct queued experiment.

M163 will separately use `ExperimentManifest.execution_fingerprint` for evidence-cache reuse. This avoids conflating scheduler metadata with evidence identity.

## Resource classes

M155 defines routing classes only; it does not execute them:

- `cpu_research`
- `mt5_tester`
- `forecast`
- `ollama`

Later milestones attach certified workers/governors to these classes. Workers claim only the classes they are allowed to execute.

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

Infrastructure failures and research failures are deliberately separated.

At M155 the queue only owns infrastructure retry semantics:

- Retryable infrastructure failures return to `queued` with an optional bounded delay.
- Non-retryable infrastructure failures transition directly to `failed`.
- Retryable failures become `failed` once `max_attempts` is reached.
- The exact immutable manifest remains unchanged during an infrastructure retry.

Later M158-M160 research logic may create a new challenger manifest after evidence-backed research failure. It must never mutate the failed manifest in place.

## Auditability

`experiment_events` is append-only and records enqueue, lease, expiry, renewal, retry, success and terminal failure. The mutable job row is a current-state projection; the event table preserves transition history.

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

M155 exposes no MT5 order API, broker credentials, broker writes, live risk override, entry veto authority, or Champion-promotion authority. `broker_write_authorized` and `promotion_authorized` remain permanently false.

## M155 certification gates

M155 is accepted only when its manifest contract, durable queue tests and smoke checks pass on Python 3.11 and 3.12 on both Ubuntu and Windows.

The queue tests prove idempotent enqueue, resource routing/priority, exclusive live leases across SQLite connections, expired-lease reclaim, stale-worker rejection, bounded retry/dead-letter behavior, lease renewal, process restart persistence, final-attempt fail-closed behavior, database integrity and absence of broker/promotion authority.

## Next milestone

M156 is the **Feature Intelligence Registry**. Worker supervision and adaptive hardware governance remain required, but they belong primarily in M162 so the milestone numbering stays aligned with the Experiment Factory roadmap.
