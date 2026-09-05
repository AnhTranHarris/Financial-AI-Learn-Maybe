# M155-M164 — Dusty Dragon Experiment Factory Roadmap

## Governing rule

Complexity is permitted only when it measurably buys speed, reproducibility, intelligence, safety, research throughput, or lower duplicated compute. Complexity that merely adds classes, agents, abstractions, or impressive diagrams is rejected.

The tranche is intentionally dense. Its objective is to turn the certified M154 research organism into a disciplined quantitative research factory that can decide what deserves testing, run it reproducibly, learn from failure, stop unproductive search, and remember every result.

## M155 — Experiment Constitution & Manifest

Immutable scientific contract containing hypothesis, ancestry, provenance, software/data/feature identities, broker economics, seed, windows, evaluation policy, risk assumptions, compute request and expected outputs. Any scientific change creates a new manifest. Infrastructure retry never edits the manifest.

Supporting runtime: durable experiment queue with leases, bounded retry, crash recovery and append-only transition events.

## M156 — Feature Intelligence Registry

Versioned feature catalog with dependency graph, point-in-time availability, lookahead classification, warm-up requirements, market/session applicability, computational cost, family membership and derived-feature provenance.

Dusty must be able to ask which features are legally knowable before entry and reject future-normalized or otherwise contaminated constructions.

## M157 — Strategy Genome Compiler v2

Unified strategy intermediate representation for User/Carson, Vibe, external and Dusty-generated strategies. Typed clauses cover universe, context, regime, setup, trigger, invalidation, management, exit, session constraints, forecast dependencies and risk requirements.

Unknowns remain explicit. Locked/researchable/forbidden constraints remain machine-enforced.

## M158 — Controlled Evolution + Failure-Directed Challenger Engine

Bounded mutation and carefully justified crossover. Parent preservation, semantic-change accounting, mutation budgets and explanations are mandatory.

Research failure first receives attribution. Only evidence-supported variables may change. Infrastructure failure creates no new strategy and retries the exact immutable experiment.

## M159 — Strategy Family, Novelty, Lineage & Exhaustion Engine

Structural fingerprints, family IDs, ancestry graph, parameter/semantic distance, behavioral signatures, near-duplicate detection, failure-mode similarity and novelty scoring.

The engine tracks rational search-space coverage, repeated failure mechanisms, stable parameter neighborhoods, marginal improvement and remaining novelty. Exhausted families enter the Graveyard instead of consuming indefinite compute.

## M160 — Research Value Scheduler & Loop Governor

Prioritize expected information value per expected compute cost while preserving starvation protection and family coverage.

Formal research-loop states:

- `PROPOSED`
- `TESTING`
- `FAILED_RESEARCHABLE`
- `CHALLENGER_CREATED`
- `RETESTING`
- `PASSED_STAGE`
- `EXHAUSTION_WARNING`
- `EXHAUSTED`
- `GRAVEYARD`
- `REOPEN_ELIGIBLE`

A failed experiment must create information. Information may create one bounded next experiment. Repeated failure narrows the search. When remaining rational search space and marginal information value are exhausted, Dusty must stop.

## M161 — Native MT5 Experiment Executor

Compile an immutable manifest into a deterministic Strategy Tester job package containing terminal identity, symbol/timeframe, dates, strategy binary/version, parameters, deposit/leverage, modeling mode, broker economics, spread/execution assumptions and expected artifacts.

Normalize tester outputs into Dusty evidence and classify operational failures distinctly from strategy failures:

- `STRATEGY_FAIL`
- `DATA_FAIL`
- `TERMINAL_FAIL`
- `TESTER_FAIL`
- `RESOURCE_FAIL`
- `TIMEOUT`
- `CONFIG_FAIL`

No infrastructure failure may masquerade as trading evidence.

## M162 — Adaptive Parallel Compute Governor

Monitor CPU, RAM, GPU/VRAM, model residency, MT5 tester workers and process pressure. Allocate light/medium/heavy/very-heavy jobs, apply backpressure, avoid destructive model reload churn, enforce worker budgets and prefer reuse of resident contractors.

The physical PC is a finite research plant. The governor's goal is maximum useful research throughput without destabilizing Windows or corrupting evidence.

## M163 — Reproducibility & Result Cache

Content-addressed cache keyed from execution-relevant identity. If strategy, code, data, feature versions, broker profile, seed, parameters, evaluation policy and other evidence-producing inputs are identical, Dusty reuses verified evidence rather than recomputing it.

A material input change creates a new execution identity. Cache entries include validity metadata and replay verification so stale evidence cannot survive silent dependency drift.

## M164 — Research Artifact Vault & Experiment Ledger

Append-only institutional memory for manifests, artifacts, metrics, tester reports, forecast/provider evidence, failure diagnoses, lineage, checkpoints, cache references, Graveyard disposition and Challenger status.

An exhausted family is not forgotten. It can become `REOPEN_ELIGIBLE` only when materially new evidence exists, such as a new feature primitive, forecast provider, broker economics, market-regime evidence, user insight or exit architecture.

## Cross-cutting A1/A2/A3 behavior

### A1 — edge discovery

Determine whether a defensible underlying expectancy exists under the lowest-risk research assumptions. If A1 broadly fails across relevant regimes, return to the hypothesis level rather than polishing exits on a strategy with no measurable edge.

### A2 — profitability/reliability

Stress costs, walk-forward behavior, parameter neighborhoods and robustness. A strategy that survives only at a needle-point parameter value is fragile and should normally fail here.

### A3 — profit-capture velocity

Optimize how efficiently a robust strategy captures available favorable excursion while preserving the constitution established in A1/A2. A3 may not rescue a thesis that lacks A1/A2 robustness.

## Failure-directed restart rule

A later-stage failure does not automatically erase earlier evidence.

Example: A1 passes, A2 fails from exit-cost sensitivity. Dusty preserves the A1 evidence, diagnoses exit-cost sensitivity, creates bounded exit challengers, performs an A1 sanity check, then re-enters A2.

By contrast, if A1 finds no measurable expectancy across nearly every relevant regime, Dusty returns to the hypothesis level and does not burn A2/A3 compute.

## Exhaustion rule

There is no fixed universal test count. Research exhaustion is inferred from several signals together:

- meaningful mutation space covered
- family novelty remaining
- repeated failure mechanisms
- parameter-neighborhood stability
- A1/A2/A3 survivor counts
- marginal improvement trend
- information gained per unit compute
- last-N experiments producing no material new evidence

`EXHAUSTION_WARNING` triggers a bounded final search. Continued low value transitions the family to `EXHAUSTED` and then `GRAVEYARD`.

## Anti-parameter-mining rule

Tiny one-off parameter spikes are fragility warnings, not discoveries. Viable candidates should normally survive a defensible neighborhood rather than succeeding only at a single historical point.

## Evidence graph

Every major object becomes cryptographically identifiable:

`DATASET + FEATURES + STRATEGY + BROKER PROFILE + SOFTWARE -> EXPERIMENT MANIFEST -> EXECUTION -> RESULT -> A1/A2/A3 -> CHALLENGER or GRAVEYARD -> ARTIFACT VAULT`

Any material input change creates a new identity. Silent contamination is forbidden.

## End-state of M164

By M164 Dusty should be capable of:

1. deciding what deserves testing;
2. refusing work it already knows;
3. creating bounded, explainable descendants;
4. allocating finite hardware intelligently;
5. executing native MT5 research reproducibly;
6. distinguishing infrastructure faults from research failures;
7. recognizing diminishing research value and stopping;
8. remembering every meaningful result and reason permanently.

The Experiment Factory is therefore not an infinite optimizer. It is an autonomous research system that must be capable of both: `I have another defensible experiment` and `This thesis is exhausted; stop spending compute.`
