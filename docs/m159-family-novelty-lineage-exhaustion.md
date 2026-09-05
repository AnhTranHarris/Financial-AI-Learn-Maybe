# M159 — Strategy Family, Novelty, Lineage, and Exhaustion Intelligence

M159 prevents Dusty Dragon from mistaking parameter churn for new research. It classifies semantic/behavioral similarity, preserves ancestry, and emits bounded evidence that a strategy family may be approaching diminishing returns.

## Scope

M159 is research-only. It has no broker-write, risk-override, entry-veto, Champion-promotion, or Graveyard-transition authority. M160 owns loop-state transitions.

## Strategy identity

M159 deliberately separates three questions:

1. **Exact execution identity** — if two compiled strategies have the same M157 `execution_fingerprint`, they are the same quantitative experiment even when names, authorship, generation, or provenance differ.
2. **Structural family identity** — symbols, timeframes, clause kinds/source keys, feature identities, parameter names, and resolution shape define a structural family while mutable clause/parameter values are excluded.
3. **Behavioral similarity** — optional aligned output signatures can refine classification, but only when both strategies were measured against the exact same evaluation fingerprint and observation count.

The default novelty policy combines structural, clause-value, and parameter distance. The policy is versioned. Its thresholds are research policy, not universal claims about market alpha.

## Novelty classes

- `EXACT_DUPLICATE`: execution identity is identical.
- `NEAR_DUPLICATE`: same family with very small semantic distance, or extremely similar behavior plus small semantic distance.
- `FAMILY_VARIANT`: related but nontrivial variant, including highly correlated behavior on identical evidence.
- `NOVEL`: no supported structural/behavioral basis for treating the candidate as a family variant.

Numeric parameter values use normalized numeric distance so `50` versus `51` is not treated like an unrelated string.

## Lineage

`StrategyLineageIndex` records immutable parent relationships using SHA-256 strategy identities.

- Unknown external roots are allowed as leaf references.
- A node cannot become its own parent.
- Registered parent sets cannot be rewritten.
- Cycles are rejected.
- Registration is transactional: a failed cycle check restores the previous graph state instead of leaving a poisoned node behind.

## Exhaustion evidence

M159 does not declare a family dead based on experiment count. It considers only research attempts and explicitly excludes `INFRASTRUCTURE_FAILED` outcomes.

A warning/strong signal requires a combination of evidence such as:

- sufficient research attempts;
- multiple mutation axes explored;
- low recent novelty;
- low recent improvement;
- high recent research-failure fraction;
- for strong exhaustion, a dominant repeated failure mechanism.

Meaningful recent improvement works against exhaustion. The assessment is evidence for M160, not an autonomous shutdown decision.

## Research basis

The design intentionally borrows patterns rather than code:

- Vibe-Trading performs explicit deduplication before accepting new research artifacts and distinguishes duplicate-like signals from potentially useful variants.
- Qlib task management uses priority-aware claiming, supporting M160's later research scheduler.
- TradingAgents, Qlib, and Microsoft Agent Framework persist checkpoints so completed research is not replayed after failure.
- MetaTrader 5's Strategy Tester caches exact parameter-set results and dispatches work to tester agents, supporting the later M161–M163 executor/cache architecture.
- Automaton detects repeated unproductive loops, supporting an explicit loop governor rather than unlimited mutation churn.

Community reports about optimization overfitting are treated as risk signals, not as proof. Dusty's later M165–M174 robustness tranche remains responsible for walk-forward, parameter-neighborhood, regime, cost, and tail-risk examination.

## Graduation requirements

M159 graduates only when:

- exact duplicates remain exact despite record/provenance changes;
- small numeric parameter changes are not misclassified as novel alpha;
- materially different strategies can remain novel;
- behavioral comparison rejects mismatched evidence sets;
- lineage cycles fail closed without mutating the graph;
- unknown external roots remain valid;
- infrastructure failures do not count toward exhaustion;
- experiment count alone cannot cause exhaustion;
- recent meaningful improvement prevents premature exhaustion;
- repeated low-novelty/low-improvement research failure can produce bounded exhaustion evidence;
- dedicated Ubuntu/Windows Python 3.11/3.12 QC and full repository CI pass on the exact head.
