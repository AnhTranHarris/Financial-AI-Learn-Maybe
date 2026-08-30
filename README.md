# Dusty Dragon — AI Trading Reasoning Core

> Status: **Reasoning Core v1.0 design frozen — implementation starts at M0**

Dusty Dragon is being rebuilt from a clean slate as an external AI trading-research system. This repository begins with the **reasoning core only**: the cognitive operating system that will later consume strategy rules, market evidence, forecasting models, and execution adapters.

The first implementation deliberately contains **no trading strategy rules, no broker execution, no position sizing, and no live-money logic**.

## Core design principle

The base cognitive unit is intentionally narrow:

- **One synthetic person**
- **One symbol**
- **One strategy**
- **One repeating reasoning lifecycle**

The Person contains four parallel cognitive functions:

1. **Analyst — construct**
   - What does the evidence currently support?
   - Produces directional/thesis interpretation.

2. **Skeptic — attack**
   - Why might the current interpretation be wrong?
   - Searches for contradictions, broken assumptions, and invalidation.

3. **Patience — time**
   - Is action justified now?
   - Prevents premature entry, premature exit, and unnecessary interference with a valid position.

4. **Guardian — govern**
   - Can the reasoning process itself be trusted?
   - Watches coherence, readiness, exceptions, process integrity, and stand-down conditions.

These are **not four independent trading agents**. They are four cognitive functions of one Person.

## Initial categorical Person state

Reasoning Core v1 begins with intentionally simple categorical outputs rather than fabricated precision.

### Analyst

- `LONG`
- `SHORT`
- `NEUTRAL`
- `UNCLEAR`

### Skeptic

- `CLEAR`
- `CONCERN`
- `INVALID`
- `UNKNOWN`

### Patience

- `WAIT`
- `READY`
- `COMPLETE`

### Guardian

- `NORMAL`
- `CAUTION`
- `STOP`

Continuous probabilities may be introduced later only if measured evidence shows they improve the system.

## Frozen human reasoning lifecycle

The Person follows a deterministic lifecycle rather than jumping arbitrarily between decisions.

```text
PURPOSE / READINESS
        ↓
PERCEIVE
        ↓
FILTER / ATTENTION
        ↓
INFORMATION COHERENCE
        ↓
STRATEGY ELIGIBILITY
        ↓
BUILD SCENARIOS
        ↓
HYPOTHESIS
        ↓
MEMORY / ANALOGY
        ↓
CONTRADICTION CHECK
        ↓
UNCERTAINTY
        ↓
FALSIFY
        ↓
CONFIDENCE CALIBRATION
        ↓
OPPORTUNITY VALUE
        ↓
PREPARE
        ↓
WAIT / WATCH
        ↓
SCENARIO MATCH
        ↓
SURPRISE CHECK
        ↓
FINAL VALIDATION
        ↓
ENTRY QUALIFIED / NO ACTION
        ↓
POSITION SUPERVISION
        ↓
HOLD / CONTINUE / EXIT QUALIFIED
        ↓
REVIEW
        ↓
ATTRIBUTE
        ↓
LEARN
        ↓
UPDATE MEMORY
        ↓
PERCEIVE
```

A future strategy layer will define what constitutes a scenario, entry eligibility, invalidation, expected lifecycle, and completion. The reasoning core itself remains strategy-agnostic.

## Repeating loop behavior

### No position — Opportunity Loop

```text
WATCH → RECOGNIZE → VALIDATE → ENTRY QUALIFIED or NO ACTION → REPEAT
```

### Position open — Position Management Loop

```text
SUPERVISE → CONTINUE? → WAIT → NEW INFORMATION → SUPERVISE AGAIN
```

The continuation question is based on the forward justification for remaining in the position, not simply current P&L.

### After completion — Learning Loop

```text
REVIEW → ATTRIBUTE → LEARN → UPDATE MEMORY → RESTART
```

## Entry / hold / exit semantics

The core produces **semantic reasoning outcomes**, never broker commands.

Examples:

- `OBSERVE`
- `WAIT`
- `READY`
- `ENTRY_LONG`
- `ENTRY_SHORT`
- `HOLD`
- `EXIT`
- `ABORT`
- `STAND_DOWN`

`ENTRY_LONG` means the Person has cognitively qualified a long entry. It does **not** call MT5 or place an order.

### Entry

Entry requires the relevant cognitive permissions to agree:

- Analyst supports the thesis/direction.
- Skeptic finds no material invalidation.
- Patience determines the situation is ready.
- Guardian considers the reasoning process trustworthy.

### Hold

Hold/continue remains the default while:

- the thesis remains valid,
- no material contradiction appears,
- the opportunity remains alive,
- timing does not justify completion,
- and no Guardian exception applies.

### Exit

Exit is intentionally asymmetric with entry. A materially valid invalidation channel may establish an exit case without requiring unanimous opposite-direction agreement.

Exiting a long position does **not** automatically authorize a short position, and vice versa. Reversal requires a new independently qualified reasoning cycle.

## Information Coherence Gate

The core explicitly distinguishes:

- **Uncertainty** — insufficient knowledge.
- **Contradiction** — important evidence disagrees.
- **Information overload** — too much information to reason efficiently.
- **Incoherence** — available evidence cannot form a sufficiently usable decision picture.

Initial coherence states:

- `COHERENT`
- `RESOLVABLE`
- `OVERLOADED`
- `INCOHERENT`
- `INSUFFICIENT`

The coherence layer is responsible for relevance, freshness, redundancy, missing critical inputs, contradictions, and overload. Guardian consumes its result but does not own the mechanism.

## Exception / Reset system

An exception is different from ordinary falsification.

- **Falsification:** this particular hypothesis is wrong.
- **Exception:** the current reasoning process, evidence environment, or decision state is no longer trustworthy enough to continue normally.

Exception severities:

- **E1 — RECONSIDER**: return to perception/reassessment.
- **E2 — ABORT**: destroy the active thesis and restart.
- **E3 — STAND_DOWN**: stop active participation until readiness is restored.

The exception monitor is orthogonal to the normal lifecycle and may interrupt any reasoning phase.

## Decision journal

Every meaningful cognitive transition must be observable and replayable.

Initial journal fields include:

- timestamp
- person ID
- symbol
- strategy ID
- reasoning phase
- evidence snapshot ID
- Analyst state
- Skeptic state
- Patience state
- Guardian state
- coherence state
- exception state
- hypothesis ID
- semantic decision
- reason codes
- previous state
- new state

Later records add observed outcome, review result, and attribution.

A central invariant is:

```text
original semantic run == replayed semantic run
```

## External learning — outside the Person

The Person reasons about the present. A separate offline learner studies many past Person decisions.

```text
PERSON
  ↓
DECISION
  ↓
JOURNAL
  ↓
OFFLINE REVIEW / ATTRIBUTION / LEARNING
  ↓
PROPOSED IMPROVEMENT
  ↓
VALIDATED CHANGE
  ↓
PERSON
```

The learner is **not a fifth cognitive state**.

A new cognitive component may not be added merely because it sounds useful. Repeated measurable failure must first show that the deficiency cannot be absorbed by Analyst, Skeptic, Patience, or Guardian.

## Evidence architecture

External systems do not reach directly into the Person. They sit behind replaceable adapters.

```text
SOURCE / MODEL
      ↓
EVIDENCE PROVIDER
      ↓
NORMALIZATION + PROVENANCE
      ↓
EVIDENCE SNAPSHOT
      ↓
COHERENCE GATE
      ↓
PERSON
```

Every evidence item will eventually carry source, timestamp, freshness, category, provenance, and supplied quality/confidence metadata where applicable.

## Ten primary repository references

These projects are **reference architectures and optional engines**, not a monolithic dependency stack.

1. **shiyu-coder/Kronos** — financial time-series forecasting engine.
2. **amazon-science/chronos-forecasting** — probabilistic/multivariate forecasting evidence.
3. **SalesforceAIResearch/uni2ts (Moirai)** — probabilistic forecasting and evaluation.
4. **HKUDS/Vibe-Trading** — finance abstractions, factors, research tooling, journaling and MT5 concepts.
5. **microsoft/qlib** — dataset, feature, model and evaluation separation.
6. **microsoft/RD-Agent / RD-Agent-Quant** — autonomous research → develop → evaluate → learn cycle.
7. **TauricResearch/TradingAgents** — constructive versus adversarial research patterns and persistent decision logs.
8. **virattt/ai-hedge-fund** — clean analysis/risk/execution/ledger separation.
9. **Conway-Research/automaton** — durable state, heartbeat, memory, policy, loop protection and audit concepts.
10. **Microsoft Agent Framework** — workflow graphs, checkpoints, observability and human-in-the-loop patterns.

### Repository integration law

Dusty Dragon remains a **small independent core**. External repositories are accessed through clean interfaces where useful. We do not merge their codebases into one giant system.

## Research/source firewall for later phases

External internet material will be treated according to its evidentiary role rather than as ground truth.

- **Tier 0 — Tradable truth:** broker/MT5 data and our own demo/live execution records.
- **Tier 1 — Structured context:** economic-calendar/event data such as Forex Factory Calendar.
- **Tier 2 — Independent validation:** independent datasets such as QuantConnect/OANDA-derived data.
- **Tier 3 — Research priors:** Quantpedia and QuantConnect Research.
- **Tier 4 — Feature genetics:** reviewed indicator concepts, Stonehill, and appropriately licensed/recreated concepts.
- **Tier 5 — Crowd intelligence:** sources such as Forex Factory Trades.
- **Tier 6 — Case studies:** Myfxbook, public trader histories, Trader.dev and similar material.
- **Tier 7 — Narrative/domain knowledge:** FOREX.com, Investopedia and similar education/context sources.
- **Tier 8 — Quarantine/anti-patterns:** martingale, extreme drawdown, unbounded grids, suspicious short-sample systems and other failure examples.

TradingView concepts may be manually researched and independently recreated where licensing permits; TradingView market data is not to be scraped into Dusty's automated live decision path.

## Reasoning Core v1 roadmap

### M0 — Specification freeze

Create canonical enums, contracts, invariants and schemas for:

- Person
- four cognitive states
- reasoning phases
- semantic decisions
- exceptions
- coherence states
- hypotheses
- position state
- journal records

**Pass:** every frozen concept has exactly one machine-readable name and definition.

### M1 — Deterministic lifecycle state machine

Implement legal and illegal state transitions using synthetic events only.

**Pass:** complete transition coverage and deterministic behavior.

### M2 — Four-state Person

Implement Analyst, Skeptic, Patience and Guardian contracts with deterministic test doubles.

**Pass:** identical input produces identical Person output.

### M3 — Decision synthesis

Implement semantic direction, entry, hold, exit, no-action, abort and stand-down behavior.

**Pass:** entry/hold/exit asymmetry matches the frozen specification.

### M4 — Coherence engine

Implement relevance, freshness, redundancy, missing-input, conflict and overload representations.

**Pass:** synthetic evidence produces the expected coherence result.

### M5 — Exception engine

Implement E1 Reconsider, E2 Abort, E3 Stand Down and recovery semantics.

**Pass:** exceptions cannot leave the Person in an impossible lifecycle state.

### M6 — Position-management loop

Implement semantic position supervision and repeating hold/continue/exit reasoning without P&L rules.

**Pass:** position continuation and exit arise only from reasoning-state changes.

### M7 — Journal + deterministic replay

Persist every cognitive transition and reconstruct complete Person state from the journal.

**Pass:** replay reproduces the original semantic decisions exactly.

### M8 — Attribution + learner shell

Implement review, expected-vs-actual comparison, attribution and simple cognitive performance summaries.

**Pass:** synthetic failures can be attributed to Analyst, Skeptic, Patience, Guardian, process, randomness/unknown categories as appropriate.

### M9 — Evidence Provider SDK

Define provider, evidence-item, snapshot, provenance and freshness interfaces.

**Pass:** the Person can operate entirely on provider-neutral evidence snapshots.

### M10 — Read-only model/research adapters

Prototype adapters for Kronos, Chronos, Moirai and Vibe-Trading as evidence providers.

Qlib/RD-Agent remain outside the live reasoning path.

**Pass:** removing any individual provider does not break the Person.

### M11 — Synthetic-market cognitive test laboratory

Exercise scenarios including:

- clear directional evidence
- contradictory evidence
- high uncertainty
- stale information
- information overload
- unresolved incoherence
- timing not ready
- timing ready
- thesis deterioration
- major surprise
- exception/reset conditions
- winner-running continuation

**Pass:** reasoning is reproducible, explainable and compliant with all invariants.

## Definition of Reasoning Core v1 complete

Reasoning Core v1 is complete when M0–M11 pass without introducing actual trading rules.

Only then do we begin the next layer:

```text
STRATEGY RULES
      ↓
PERSON
      ↓
QUALIFIED SEMANTIC DECISION
      ↓
HARD RISK / EXECUTION LAYERS (later)
```

## Explicitly out of scope for Reasoning Core v1

- MT5 order placement
- real-money credentials
- live capital
- position sizing
- strategy optimization
- portfolio allocation
- specific indicators or indicator thresholds
- broker-specific trading rules
- self-modifying production code
- direct LLM broker-write authority
- automatic reversal after exit
- adding more cognitive states without measured evidence

## Development language

**Python first.**

Most of the planned forecasting, quantitative-research and finance components are Python-native or Python-friendly. Node-based concepts from Automaton should be selectively reimplemented rather than making Node a mandatory dependency of the cognitive kernel.

## Safety and research philosophy

Dusty Dragon is a research system intended to discover and validate robust decision processes. No architecture can guarantee profitability.

The objective is not to maximize headline backtest profit. The eventual research system should minimize the time required to establish **statistically credible, robust out-of-sample expectancy** while preserving strict risk and validation constraints.

---

**Current next step:** `M0 — Specification Freeze`
