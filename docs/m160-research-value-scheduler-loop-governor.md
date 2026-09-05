# M160 — Research Value Scheduler + Loop Governor

M160 turns the M155–M159 research components into a deterministic research-control loop. It decides which eligible experiment should be admitted next, keeps infrastructure failures separate from research failures, preserves parent/Challenger identity, and manages exhaustion/Graveyard state without giving an LLM operational authority.

## Scope and authority

M160 is research-only. It has no broker-write, risk-override, entry-veto, or Champion-promotion authority. Qwen or any other model may supply bounded research hypotheses through earlier layers, but it cannot assign scheduler priority, change loop state, exhaust a strategy family, or reopen Graveyard research.

## Research-value scheduling

Candidates provide bounded evidence scores for:

- expected information gain;
- probability of resolving the current research failure;
- novelty;
- strategic value;
- normalized compute cost;
- waiting age.

The versioned scheduler uses a weighted value score, a bounded multiplicative compute penalty, and a capped aging bonus. It deliberately avoids raw `value / cost`: a near-zero compute estimate must not create an unbounded score or permanently starve expensive but strategically important native-MT5 work.

Tie-breaking is deterministic by admission sequence and manifest fingerprint.

### M155 queue boundary

M155's current durable job fingerprint includes queue priority. Therefore M160 does not repeatedly rewrite priority as research value changes. It ranks candidates **before admission** and creates the admitted M155 job with stable priority `0`. FIFO queue sequence then preserves the governor's admission order within a resource class.

This keeps dynamic scheduling state outside immutable experiment identity and avoids turning reprioritization into a fake new experiment.

## Root versus active identity

A research loop is a campaign, not a mutable experiment.

The durable record separately preserves:

- root manifest fingerprint;
- active manifest fingerprint;
- root execution fingerprint;
- active execution fingerprint;
- active M158 strategy-subject fingerprint;
- structural family fingerprint.

When M158 produces a bounded Challenger, the root identities remain unchanged while the active manifest/execution/subject move to the child. The next admission therefore enters `RETESTING` against the child instead of silently relabeling parent evidence.

## Outcome routing

M160 consumes M158 decisions rather than re-diagnosing failures:

- `PASSED + ADVANCE` → `PASSED_STAGE`;
- `INFRASTRUCTURE_FAILED + RETRY_EXACT` → same testing state and exact active execution fingerprint;
- `RESEARCH_FAILED + CREATE_CHALLENGER` → `FAILED_RESEARCHABLE`, followed by explicit Challenger registration;
- `RESEARCH_FAILED` plus M159 warning/strong evidence → `EXHAUSTION_WARNING`.

Outcome and evolution evidence must bind the exact active M158 subject and exact outcome fingerprint.

## Exhaustion and Graveyard

M159 supplies exhaustion evidence; M160 owns state transitions.

A strong exhaustion signal from an ordinary test does **not** jump directly to `EXHAUSTED`. It first enters `EXHAUSTION_WARNING`. A subsequent exhaustion review must still be `STRONG` before M160 can transition to `EXHAUSTED`. Archiving to `GRAVEYARD` is then a separate explicit transition.

Graveyard preserves all history. `REOPEN_ELIGIBLE` requires explicit evidence of materially changed context identity such as a new dataset, evaluation policy, regime context, external evidence, or software surface. Reopening does not itself execute a trade or backtest.

## Durability

`SQLiteResearchLoopStore` is intentionally narrow. M155 owns jobs; M164 will own the complete artifact vault. M160 persists only loop-control state and append-only transition events.

The store uses:

- SQLite WAL;
- `synchronous=FULL`;
- short `BEGIN IMMEDIATE` write transactions;
- compare-and-swap state/execution checks;
- atomic admission state + iteration updates;
- append-only transition events;
- integrity checks and WAL checkpoint support.

This follows SQLite's one-writer WAL model and the same durability principle used by checkpointed workflow systems: completed state should be recoverable after process failure instead of replayed blindly.

## Research basis

The design borrows patterns, not wholesale code:

- Qlib separates task definitions from priority-based atomic task claiming.
- Microsoft Agent Framework checkpoints complete workflow state at superstep boundaries so completed work can resume rather than replay.
- GitHub Actions exposes concurrency/queue controls as scheduling state separate from job implementation.
- MetaTrader 5 Strategy Tester caches exact optimization parameter results and supports resumed optimization work, supporting M161–M163's native-executor/cache direction.
- Vibe-Trading persists research artifacts and performs deduplication before admitting new research.
- Automaton explicitly detects repeated unproductive loops, supporting a bounded governor rather than unlimited mutation churn.

## Graduation requirements

M160 graduates only when:

- value scoring is bounded and deterministic;
- aging can rescue waiting work without creating unbounded priority;
- duplicate execution identities cannot enter one admission batch;
- dynamic research value does not rewrite M155 queue identity;
- infrastructure failure preserves exact active execution and testing state;
- parent and Challenger identities remain distinct;
- outcome/decision subject drift fails closed;
- admission state and iteration commit atomically;
- exhaustion requires warning then subsequent strong confirmation;
- Graveyard reopening requires materially changed context identity;
- stale transitions fail closed;
- SQLite state survives reopen with integrity intact;
- no operational trading authority is introduced;
- dedicated Ubuntu/Windows Python 3.11/3.12 QC and the full repository CI pass on the exact final head.
