# M106 — Research Acceleration Foundation

M106 begins the post-audit acceleration work without changing trading strategy rules, broker writes, Demo eligibility, or live authority.

## Why this exists

The latest research audit showed that Dusty can reproduce and reconcile large historical campaigns, but calendar time is still wasted when identical work is repeated or an interrupted experiment must restart from zero. The ten-repository architecture review identified three reusable operational patterns worth adopting now:

- one authoritative research cycle rather than many disconnected scripts;
- durable checkpoint/resume behavior;
- content-addressed experiment identity and verified cache reuse.

Dusty copies those patterns, not the external frameworks themselves.

## New primitive

`dusty.research_cycle.ResearchCycle` executes an ordered tuple of `ResearchStage` objects.

The immutable experiment identity contains:

- cycle protocol;
- caller-supplied frozen request;
- exact `code_commit`;
- ordered stage names;
- stage semantic versions.

That identity produces a SHA-256 cycle fingerprint and a dedicated run directory.

Each completed stage is written atomically in a hash-verified envelope containing:

- cycle fingerprint;
- stage identity and version;
- fingerprints of every prior stage;
- stage payload;
- payload fingerprint.

A rerun of the exact same experiment reuses verified checkpoints. An interrupted experiment resumes from the first missing stage. A corrupt or mismatched checkpoint fails closed instead of being silently overwritten or treated as evidence.

## Deliberate boundaries

M106 does **not**:

- access MT5;
- place, modify, or close orders;
- choose a strategy;
- select a forecast model;
- change risk limits;
- declare a winner;
- create Demo or Live authority;
- self-modify Dusty application code.

The primitive is infrastructure for later wiring of:

1. frozen data acquisition;
2. point-in-time feature construction;
3. forecast generation;
4. seed and filtered strategy simulation;
5. matched-exposure controls;
6. cost stress;
7. forecast/filter diagnostics;
8. research attribution;
9. bounded challenger generation.

## Resume and cache semantics

A cache hit is intentionally strict. It requires the same request, code commit, stage order, and stage versions. This may yield fewer cache hits than a looser system, but it prevents stale or semantically different experiments from masquerading as reusable evidence.

Changing stage semantics requires a stage-version change. Changing Dusty code requires a new request `code_commit`. Either action creates a different experiment identity.

## QC

`tests/test_research_cycle.py` covers:

- complete-cycle verified cache hits;
- interruption after an early checkpoint and restart from the first missing stage;
- corruption detection and fail-closed behavior;
- stage-version identity changes;
- mandatory code identity;
- unique/simple stage names.

M105 remains untouched on its prior branch. M106 work is isolated on `carson/m106-research-acceleration`.
