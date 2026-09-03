# M106 — Research Acceleration Foundation

M106 begins the post-audit acceleration work without changing broker writes, Demo eligibility, Live authority, constitutional risk, or automatic strategy promotion.

## Why this exists

The latest research audit showed that Dusty can reproduce and reconcile large historical campaigns, but calendar time is still wasted when identical work is repeated or an interrupted experiment must restart from zero. The same audit exposed two research confounders that should never again require a manual forensic calculation:

- a cost stress can appear to improve P&L because the higher assumed cost changes position size or later occupancy;
- an entry-veto forecast can appear selective because it suppresses activity, even when it blocks many profitable baseline trades.

The ten-repository architecture review identified reusable operational patterns worth adopting now: one authoritative research cycle, durable checkpoint/resume behavior, content-addressed experiment identity, verified cache reuse, cheap diagnostic attribution, and later bounded challenger evolution. Dusty copies those patterns rather than importing the external frameworks.

## Content-addressed research cycle

`dusty.research_cycle.ResearchCycle` executes an ordered tuple of `ResearchStage` objects.

The immutable experiment identity contains:

- cycle protocol;
- caller-supplied frozen request;
- exact `code_commit`;
- ordered stage names;
- stage semantic versions.

That identity produces a SHA-256 cycle fingerprint and a dedicated run directory. Each completed stage is written atomically in a hash-verified envelope containing the cycle fingerprint, stage identity/version, fingerprints of every prior stage, stage payload, and payload fingerprint.

A rerun of the exact same experiment reuses verified checkpoints. An interrupted experiment resumes from the first missing stage. A corrupt or mismatched checkpoint fails closed instead of being silently overwritten or treated as evidence.

This is an infrastructure primitive in M106; the existing Windows research coordinator still preserves its separate-run/cancellation evidence semantics until the primitive is deliberately wired into that coordinator.

## Real elapsed-time exposure

`ExitPlan` now supports an explicit optional `max_elapsed_minutes` ceiling in addition to the existing observation-count `max_hold_steps` ceiling.

Legacy strategies remain unchanged when the new field is `None`, including their previous strategy hashes. The two reviewed M15 RSI research seeds explicitly opt into `max_elapsed_minutes=240`, matching their intended four-hour research horizon.

The runtime never invents a fill during missing market data. If a position crosses its elapsed ceiling during a weekend or history gap, Dusty exits only at the first actual observed bar after the ceiling, unless a higher-priority stop or target is encountered there. The recorded trade therefore preserves the real elapsed exposure rather than pretending sixteen observed M15 bars always equal four wall-clock hours.

A regression fixture reproduces the audited Friday 23:00 UTC to Monday 04:00 UTC case: the exposure is recorded as 53 hours and the exit occurs on the Monday observation, not at a synthetic Friday timestamp.

This does **not** yet prove whether a gap is a scheduled broker closure or missing history. Broker-native session policy remains a separate capability.

## Forecast-veto diagnostics

`dusty.research_diagnostics.audit_forecast_veto()` audits a forecast against frozen baseline entry timestamps. It reports:

- issued bullish/bearish/neutral forecast counts and fractions;
- baseline entries with and without a matched forecast;
- favorable, neutral, and conflicting forecast stances at baseline entries;
- conflicting winners, losers, and flat trades using caller-supplied reconciled net-P&L labels;
- net P&L associated with the conflicted baseline entries.

Because the baseline entry sequence is frozen, this diagnostic is not distorted by the filtered strategy later changing occupancy or cooldown. It describes what the forecast blocked; it does not claim the filtered strategy is profitable or that the forecast has selection skill.

A regression test encodes the audited shape: 77 of 90 issued forecasts bearish (85.6% when rounded), 45 conflicts among 46 long baseline entries, including 19 winners and 26 losers, with the remaining baseline entry neutral.

## Matched-exposure cost attribution

`dusty.research_diagnostics` also provides fixed-exposure cost attribution. The existing comparison report now automatically pairs each configured-cost candidate/segment with its stressed-cost case and freezes the configured case's approved lots before applying the additional stress.

For every candidate and development/holdout segment, the report separates:

1. original simulated net P&L;
2. pure additional-cost effect at the original approved exposure;
3. stressed P&L at that same exposure;
4. residual `exposure_or_sequence_effect`, which captures the difference created by re-sizing, rejected positions, occupancy, or later sequence changes;
5. actual stressed P&L and total change.

Higher costs therefore cannot receive credit for an apparent P&L improvement merely because they forced the strategy to trade smaller. A regression test encodes the audited decomposition exactly:

- original P&L: `-1522.27`;
- same-exposure additional cost effect: `-46.00`;
- exposure/sequence effect: `+113.54`;
- actual stressed P&L: `-1454.73`;
- total change: `+67.54`.

The automatic comparison integration also verifies that the no-trade control remains zero in every component.

## Deliberate boundaries

M106 does **not**:

- create broker-write authority;
- place, modify, or close orders;
- infer broker session hours from missing bars;
- select a forecast model;
- optimize a direction threshold;
- change risk limits;
- declare a winner;
- create Demo or Live authority;
- self-modify Dusty application code.

The acceleration infrastructure is intended for later wiring of frozen data acquisition, point-in-time feature construction, forecast generation, seed and filtered strategy simulation, matched-exposure controls, cost stress, research attribution, cheap-to-expensive screening, and bounded challenger generation.

## Resume and cache semantics

A cache hit is intentionally strict. It requires the same request, code commit, stage order, and stage versions. This may yield fewer cache hits than a looser system, but it prevents stale or semantically different experiments from masquerading as reusable evidence.

Changing stage semantics requires a stage-version change. Changing Dusty code requires a new request `code_commit`. Either action creates a different experiment identity.

## QC and certified checkpoint

Dedicated tests cover:

- complete-cycle verified cache hits;
- interruption after an early checkpoint and restart from the first missing stage;
- corruption detection and fail-closed behavior;
- stage-version identity changes;
- mandatory code identity and unique/simple stage names;
- elapsed holding over weekend/data gaps without synthetic fills;
- unchanged legacy step-only semantics when elapsed limits are not enabled;
- audited forecast-bias/veto attribution;
- exact cost-versus-exposure decomposition;
- automatic matched-exposure attribution for all five comparison candidates across development and holdout;
- no-trade control invariants.

Functional checkpoint `b192c7f016671cb11a1fcf3b8c5dbfdf292b7a41` passed GitHub Actions run #384 on Windows and Ubuntu with Python 3.11 and 3.12, including the full unittest suite.

M105 remains untouched on its prior branch. M106 work is isolated on `carson/m106-research-acceleration`.
