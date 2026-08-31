# Dusty Dragon — Reasoning Core

Dusty Dragon is being rebuilt as a **native Windows-first autonomous quantitative research system**. This repository currently contains only the provider-neutral **human-based reasoning core**. It deliberately contains no trading strategy, position sizing, MT5 order placement, portfolio allocation, or live-money authority.

## Engineering law

**Minimum code surface, maximum behavioral coverage.**

Complexity must earn its way into the repository. The core prefers immutable data, enums, protocols, pure functions, transition tables, and standard-library components over frameworks or speculative abstractions. There are currently **zero third-party runtime dependencies**.

## The Person

One synthetic Person reasons about **one symbol + one strategy** through four cognitive functions:

- **Analyst — construct:** what does the evidence support?
- **Skeptic — attack:** why might the interpretation be wrong?
- **Patience — time:** is action justified now?
- **Guardian — govern:** is the reasoning process trustworthy?

They are functions of one Person, not four trading agents.

Initial categorical state:

| Function | States |
|---|---|
| Analyst | `LONG`, `SHORT`, `NEUTRAL`, `UNCLEAR` |
| Skeptic | `CLEAR`, `CONCERN`, `INVALID`, `UNKNOWN` |
| Patience | `WAIT`, `READY`, `COMPLETE` |
| Guardian | `NORMAL`, `CAUTION`, `STOP` |

Entry requires the relevant four-way permission. Holding continues while the thesis remains defensible. Exit is asymmetric: a material invalidation channel may independently establish an exit case. Exiting never authorizes an automatic reversal.

## Deterministic lifecycle

The lifecycle is encoded as a compact `state + event -> next_state` transition table rather than a class per phase.

```text
ORIENTING
  -> PERCEIVING
  -> FILTERING
  -> COHERENCE
  -> HYPOTHESIS
  -> FALSIFYING
  -> WAITING
  -> VALIDATING
  -> ENTRY_QUALIFIED
  -> SUPERVISING
  -> EXIT_QUALIFIED
  -> REVIEWING
  -> LEARNING
  -> PERCEIVING ...
```

`STAND_DOWN` can interrupt the normal loop and requires explicit recovery.

## M0–M11 implementation

| Milestone | Implementation | Gate |
|---|---|---|
| **M0** Specification freeze | Canonical enums, dataclasses, protocols, runtime boundaries | one name/meaning per concept |
| **M1** Deterministic state machine | explicit transition table + illegal-transition rejection | complete table contract tests |
| **M2** Four-state Person | `Cognition` + `Person` | identical input is reproducible |
| **M3** Decision synthesis | entry/hold/exit/observe/abort/stand-down | asymmetric semantics tested |
| **M4** Coherence engine | relevance, freshness, redundancy, missing inputs, conflicts, overload | synthetic coherence cases pass |
| **M5** Exception engine | E1 reconsider, E2 abort, E3 stand-down + recovery | interrupt/recovery invariants pass |
| **M6** Position reasoning | semantic open/hold/exit state only; no P&L rules | winner continuation + invalidation tested |
| **M7** Journal + replay | append-only SQLite semantic journal | replay validates original transitions/decisions |
| **M8** Learner shell | explicit offline attribution + summaries | synthetic failures attributable |
| **M9** Evidence SDK | provider-neutral `EvidenceItem` / `EvidenceSnapshot` / protocols | core has no provider dependency |
| **M10** Adapter prototypes | one callable adapter shape + Kronos/Chronos/Moirai/Vibe factories | provider loss is isolated |
| **M11** Synthetic cognitive lab | scenario matrix for clear/conflicting/stale/overloaded/timing/position cases | reproducible expected decisions |

## Coherence and exceptions

Evidence is classified as:

`COHERENT` · `RESOLVABLE` · `OVERLOADED` · `INCOHERENT` · `INSUFFICIENT`

Exceptions are orthogonal to the normal loop:

- **E1 RECONSIDER** — return to observation/reassessment.
- **E2 ABORT** — discard the current hypothesis; if a position exists, qualify an exit.
- **E3 STAND_DOWN** — stop participation; if a position exists, qualify an exit first.

## Journal and learning

Every durable decision can be written to SQLite as a compact semantic record containing cognitive state, evidence snapshot ID, exception/coherence state, transition, decision, and reason codes. Replay re-applies the recorded state-machine events and fails loudly on discontinuity or transition mismatch.

Learning remains outside the live Person:

```text
PERSON -> DECISION -> JOURNAL -> OFFLINE REVIEW -> ATTRIBUTION -> VALIDATED CHANGE
```

The learner is not a fifth cognitive state and does not rewrite production cognition while it is operating.

## Provider boundary

External models and research systems are evidence sources, not the brain:

```text
Kronos / Chronos / Moirai / Vibe / future source
                -> provider adapter
                -> EvidenceSnapshot
                -> coherence gate
                -> Person
```

The M10 adapters intentionally do **not** import those projects yet. A single callable adapter contract lets each engine be integrated lazily without making its dependency tree part of Reasoning Core v1.

## Ten-repository design audit — retained genetics only

| Reference | Retained idea | Deliberately rejected for M0–M11 |
|---|---|---|
| shiyu-coder/Kronos | clean forecast-provider boundary | model internals in cognition |
| amazon-science/chronos-forecasting | probabilistic/multivariate evidence | runtime dependency |
| SalesforceAIResearch/uni2ts | alternate forecast/evaluation evidence | runtime dependency |
| HKUDS/Vibe-Trading | finance/provider abstractions and later MT5 lessons | importing its broad application stack |
| microsoft/qlib | research/data/model/evaluation separation | live reasoning dependency |
| microsoft/RD-Agent | research -> develop -> evaluate -> learn pattern | autonomous code mutation in live core |
| TauricResearch/TradingAgents | constructive/adversarial separation, checkpoint lessons | LangGraph/LLM graph dependency |
| virattt/ai-hedge-fund | one pipeline, hard risk boundary, persistent ledger concept | LLM trade authority |
| Conway-Research/automaton | SQLite durability, integrity, loop/policy ideas | Node runtime and self-modification |
| microsoft/agent-framework | workflow/checkpoint/resume concepts | framework adoption before demonstrated need |

The result is intentionally smaller than any of the reference systems.

## Windows-first runtime boundary

Reasoning Core v1 is independent of ChatGPT, Codex, Ollama, MT5, or any external model. Later, Dusty will run as a local Windows application/service with replaceable adapters. `ports.py` freezes narrow contracts for future reasoning, evidence/model providers, journals, MT5/research workers, operator API, LLM providers, and health state without implementing those systems prematurely.

## Repository shape

```text
src/dusty/
  core.py       # M0–M6: vocabulary, lifecycle, Person, coherence, exceptions
  journal.py    # M7: SQLite persistence + deterministic replay
  learning.py   # M8: offline attribution shell
  ports.py      # M0/M9: replaceable runtime boundaries
  providers.py  # M9/M10: provider isolation + model adapter prototypes

tests/
  test_core.py
  test_journal_learning.py
  test_providers_scenarios.py  # includes M11 laboratory
```

No duplicate strategy implementation exists because **strategy rules do not exist yet**.

## QC

CI runs on both **Windows and Linux** using Python 3.11 and performs:

```text
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

No network, model download, API key, broker credential, or MT5 terminal is required to certify M0–M11.

## Explicitly out of scope

- strategy rules or indicators
- MT5 orders
- broker credentials
- position sizing
- portfolio allocation
- optimization/backtesting orchestration
- live capital
- self-modifying production code
- direct LLM broker-write authority
- automatic reversal
- a fifth cognitive state without measured evidence

Reasoning Core v1 is a **decision architecture**, not a profitability claim. Trading intelligence and validation are subsequent layers.
