# M158 — Controlled Evolution & Failure-Directed Challengers

## Scope

M158 turns Dusty's existing M143-M152 research genetics and M157 typed strategy compiler into a governed failure-directed Challenger engine.

It is intentionally **not** a free-form genetic optimizer. A failed experiment does not grant permission to randomly change indicators, sessions, entries, exits, stops, risk, or forecast logic until something eventually looks profitable.

The governing rule is:

> Research failure may justify a small, evidence-directed child. Infrastructure failure may only justify recovery and an exact retry.

M158 remains research-only and grants no broker-write, risk-override, or Champion-promotion authority.

## Research basis

The design follows several independently useful patterns while keeping Dusty small and deterministic:

1. Dusty's M143-M152 genetics already preserve origin, parent ancestry, LOCKED/RESEARCHABLE/FORBIDDEN constraints, bounded 1-2-variable descendants, and explicit failure diagnoses. M158 reuses those contracts instead of building a second mutation system.
2. M156 Feature Intelligence Registry defines which feature transformations are registered, point-in-time legal, repaint-safe, and compatible with explicit mutation families.
3. M157 Strategy Genome Compiler v2 binds strategy semantics to exact M156 feature identities and rejects forbidden, unresolved, or point-in-time-illegal decision logic.
4. Microsoft RD-Agent's quantitative loop explicitly distinguishes execution exceptions from useful hypothesis feedback. Dusty adopts the stronger rule that provider/MT5/data/storage/resource/process failures cannot mutate strategy semantics.
5. Vibe-Trading's current research tooling separates hypothesis generation, screening/survivor queues, validation, deduplication, and artifact registration. Dusty similarly makes a child earn a new immutable research identity instead of rewriting its parent.
6. MetaTrader Strategy Tester exposes explicit input/optimization ranges. Later M161 can therefore execute M158 descendants as deterministic parameterized jobs rather than hidden mutable state.
7. Automaton's persistent loop protections and audit trail illustrate why autonomous iteration needs explicit loop boundaries instead of unlimited repeated tool/action sequences.
8. Practitioner discussions of genetic optimization repeatedly warn that duplicate parameter regions, narrow winning islands, and repeated post-failure tweaking can manufacture overfit results. M158 therefore limits semantic mutation distance now; M159/M160 add novelty and exhaustion logic next.

Community material is treated as implementation-risk evidence, not as proof of trading edge.

## Outcome taxonomy

Every completed attempt is classified as one of:

### `PASSED`

The current immutable strategy/evidence contract passed the active research stage. M158 returns `ADVANCE` and creates no child.

### `INFRASTRUCTURE_FAILED`

Examples:

- forecasting provider crash
- MT5 timeout/fault
- missing/corrupt data
- storage error
- RAM/VRAM/resource failure
- worker/process failure

M158 returns `RETRY_EXACT` with the **same M157 execution fingerprint**. Any mutation suggestions supplied alongside an infrastructure failure are ignored.

Infrastructure failure is not negative market evidence.

### `RESEARCH_FAILED`

The experiment executed correctly but its evidence failed the active research criterion.

Only this state may create a Challenger, and only when a bounded defensible mutation is supplied or derived from an existing M152 `FailureDiagnosis`.

If no defensible mutation exists, M158 returns `STOP_RESEARCH`. This is not yet a permanent Graveyard declaration; M159/M160 will determine family novelty, exhaustion, and reopen eligibility from accumulated evidence.

## Mutation constitution

A Challenger may contain only one or two meaningful mutations.

Every mutated source must already be declared `RESEARCHABLE` in the parent genome.

M158 rejects mutation of:

- `LOCKED` user/strategy premises
- `FORBIDDEN` constitutional rules
- undeclared variables
- operational authority

Permanent forbidden examples inherited from the existing genetics layer include martingale, revenge sizing, stop widening, future leakage, HFT, and scalping.

A child always receives:

- exact parent fingerprint
- exact experiment-outcome fingerprint
- deterministic mutation fingerprint
- generation = parent generation + 1
- evidence/lesson fingerprints used to justify the change
- a fresh M157 compiled identity

The parent is never edited in place.

## Feature replacement discipline

A mutation may request a feature replacement only when:

1. the original feature is actually bound to the target M157 clause;
2. the replacement feature already exists in the frozen M156 registry;
3. the source feature explicitly permits the requested mutation family;
4. the resulting M157 strategy still passes point-in-time/repaint/lookahead eligibility.

For example, `rsi_14@v1 -> rsi_21@v1` may be allowed as a `rolling_window` mutation if the registry declares that family compatible.

A replacement cannot smuggle a future-looking or availability-unknown feature into an entry clause; the M157 compiler rejects the resulting child.

M158 therefore does not let the evolution engine become a back door around M156/M157 safety.

## Determinism and duplicate handling

The same:

- parent
- outcome evidence
- mutation instructions

produces the same child mutation/genome identities.

Duplicate candidate instruction groups are collapsed before they consume later experiment capacity.

Near-duplicate semantic families and parameter-distance novelty are deliberately deferred to M159, where Dusty can compare candidates across a larger lineage rather than merely deduplicating exact M158 mutation identities.

## Existing M152 diagnosis reuse

M158 consumes the existing `FailureDiagnosis` contract directly.

A diagnosis such as:

- mechanism: `ENTRY`
- research variable: `entry.trigger`
- candidate values: `rsi_reclaim_55`, `rsi_reclaim_60`

becomes two one-change Challenger proposals.

M158 does not infer a causal diagnosis from P&L by itself. Causal attribution quality remains a separate research problem.

## QC defect caught before certification

During implementation, the first `decide_evolution()` draft tested whether an iterable of candidate instructions was empty by converting it to a tuple, then attempted to iterate the original object again. A generator could therefore be consumed before child creation.

The defect was identified before M158 certification, corrected by materializing the candidate groups exactly once, and covered by a regression test using a one-shot generator.

This is preserved here because the Artifact/experiment architecture should remember engineering failures as well as research failures.

## M158 certification gates

M158 is accepted only when tests prove:

- pass -> advance with no mutation
- infrastructure failure -> exact retry identity with no Challenger
- M152 diagnosis -> bounded one-change descendants
- locked and forbidden mutations fail closed
- more than two semantic changes are rejected
- one-shot candidate iterators cannot be consumed accidentally
- feature replacements require registered M156 targets and compatible mutation families
- future/lookahead replacement attempts fail at M157 eligibility
- research failure with no defensible mutation stops at the M158 boundary
- outcome and diagnosis must belong to the exact parent
- identical parent/outcome/mutation inputs produce deterministic Challenger identity
- duplicate exact candidate groups do not generate duplicate children
- no broker-write, risk-override, or promotion authority appears

The dedicated gate must pass on Python 3.11 and 3.12 on Ubuntu and Windows, followed by the complete repository CI on the same exact head.

## Next milestones

M159 adds family/novelty/lineage/exhaustion intelligence:

- semantic family identities
- ancestry graph
- mutation/parameter distance
- near-duplicate suppression
- failure-mode similarity
- diminishing family novelty
- exhaustion evidence

M160 then turns those signals into the full Research Value Scheduler and Loop Governor, including `EXHAUSTION_WARNING`, `EXHAUSTED`, `GRAVEYARD`, `REOPEN_ELIGIBLE`, starvation protection, and information-value-per-compute scheduling.
