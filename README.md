# Dusty Dragon — Reasoning, Learning & Research Core

Dusty Dragon is being rebuilt as a **Windows-first autonomous quantitative research system**. The current development branch contains the frozen deterministic Reasoning Core (M0–M11), the learning/research layer (M12–M23), and the resource-aware curriculum/validation layer (M24–M35).

It still has **no broker-write authority, no broker credentials, no position sizing, no live-money path, and zero third-party runtime dependencies**.

## Engineering laws

- **Minimum code surface, maximum behavioral coverage.**
- **Disk holds knowledge; RAM holds only the current thought.**
- **The internet supplies hypotheses, not truth.**
- **External performance is a claim until Dusty independently reproduces it.**
- **Raw market history is not duplicated merely because Dusty can duplicate it.**
- **Compute is budgeted like capital: core safety outranks background research.**

Complexity must earn its way into the repository. Immutable data, enums, protocols, pure functions, transition tables, append-only SQLite records, bounded iteration, and standard-library components are preferred over frameworks or speculative abstractions.

## Frozen Person

One synthetic Person reasons about one symbol + one strategy through four cognitive functions:

- **Analyst — construct**
- **Skeptic — attack**
- **Patience — time**
- **Guardian — govern**

They remain functions of one Person, not independent trading agents. Reasoning decisions are semantic only and **never mutate execution truth**.

## M0–M23 foundation

M0–M11 established deterministic reasoning, coherence/exception semantics, journal/replay, evidence boundaries and the four-function Person. M12 exhaustively certified all 6,480 categorical reasoning combinations. M13–M18 built point-in-time human/market experience learning. M19–M21 built constrained Strategy IR, cheap experiments and the strategy graveyard. M22 made forecast models replaceable evidence specialists. M23 certified read-only shadow-research readiness while keeping broker writes impossible.

The M0–M11 Reasoning Core remains frozen unless measured failures justify a semantic change.

## M24–M35 — Resource-aware curriculum and validation

| Milestone | Capability | Hard boundary |
|---|---|---|
| **M24** | Resource Governor + disk-first Learning Library | RAM/CPU/disk pressure throttles low-priority work first; production library cannot silently default to `:memory:` |
| **M25** | External Strategy Gateway + quarantine | hidden/unlicensed code is not imported; insufficiently understood strategies remain discovered, not executable |
| **M26** | Canonical translation + lineage/family identity | source popularity never becomes statistical evidence; structurally related strategies can share a family without sharing exact hashes |
| **M27** | Independent claim reproduction | reported performance must be expressed in Dusty's metric semantics and reproduced independently |
| **M28** | Compact curriculum retrieval | a large local education yields a small tag-relevant working set for reasoning |
| **M29** | Streaming research + batched durable memory | experiments use one-pass constant aggregate state; large strategy/journal histories expose batched iterators |
| **M30** | Bounded refinement tournament | parameter mutation is explicitly capped; candidates are deterministically compared without agent voting |
| **M31** | Walk-forward robustness gate | strategy identity must remain consistent across folds and unstable/failed folds are recorded explicitly |
| **M32** | MT5 Strategy Tester contract | terminal path/symbol/timeframe/date/fidelity are explicit; tester integration has no broker-write authority |
| **M33** | Fast-lab ↔ MT5 reconciliation | higher-fidelity MT5 results can reject material disagreement with the cheap research screen |
| **M34** | Resource-aware multi-terminal scheduling | one planned test per terminal; mass backtesting yields automatically under host pressure |
| **M35** | Demo-integration qualification gate | passing means eligible to build controlled demo integration; **broker-write authority remains false** |

## Dusty Learning Library

Dusty's long-term memory is deliberately compact. The library stores:

- source identity, URL/reference, retrieval time, hash and provenance;
- normalized strategy/method knowledge;
- indicator/method/failure/context lessons and tags;
- strategy identities, families, lineage and experiment conclusions;
- journals, model scores and validation results.

Raw MT5 market history is treated as an upstream/cache responsibility rather than copied into a second Dusty warehouse. Reconstructible feature caches and temporary research artifacts are explicitly lower-value than irreplaceable learning records.

```text
large public curriculum + MT5/history sources
                  |
                  v
        provenance + quarantine
                  |
                  v
      compact Dusty Learning Library
                  |
         targeted retrieval
                  |
                  v
        small reasoning working set
                  |
 Analyst + Skeptic + Patience + Guardian
                  |
                  v
          testable hypothesis
```

## External strategy curriculum

Quantpedia, QuantConnect, TraderDev, TradingView, GitHub, research papers, Myfxbook, Forex Factory, and future sources are **curriculum**, not authority.

The acquisition boundary distinguishes:

- `OPEN_SOURCE`
- `AUTHORIZED_PRIVATE`
- `DESCRIPTION_ONLY`
- `PERFORMANCE_ONLY`

Code that is hidden or lacks sufficient reuse provenance is quarantined. Description-only material may teach a method when its rules are explicit, but inaccessible proprietary code is not reverse engineered.

Exact `StrategySpec` hashes identify concrete rule sets. A separate structural family hash deliberately ignores numeric thresholds so copied/retuned versions of the same underlying idea do not masquerade as independent evidence.

## Research funnel

```text
public strategy / human behavior / paper / prior failure
                         |
                         v
                 source quarantine
                         |
                         v
                 canonical strategy
                         |
                         v
             reproduce the source claim
                         |
                         v
              streaming cheap experiment
                         |
                 reject / survive
                         |
                         v
              bounded local refinement
                         |
                         v
                 walk-forward folds
                         |
                         v
             MT5 Strategy Tester contract
       open -> M1 OHLC -> every tick -> real ticks
                         |
                         v
             fast-lab / MT5 reconciliation
                         |
                         v
              demo-integration candidate
```

The objective is **useful hypotheses falsified and lessons retained per unit of compute**, not raw backtest count.

## MT5 boundary

M32–M35 establish the laboratory contract only. They do not pretend CI has a MetaTrader terminal and do not add an MT5 runtime dependency prematurely.

A future Windows adapter will bind a specific terminal executable to a dedicated worker process. MT5 owns its broker-specific historical cache and Strategy Tester. Dusty supplies explicit test requests and retains compact provenance/results. Coarse testing modes screen candidates cheaply; high-fidelity/real-tick testing is reserved for survivors.

## Reference-repository genetics

The rebuild borrows engineering ideas rather than dependency trees:

- Kronos — specialized financial time-series forecast boundary
- Chronos — probabilistic/quantile forecast evidence
- Uni2TS/Moirai — rolling and universal forecast evaluation concepts
- Vibe-Trading — point-in-time research, shadow research, validation funnel and provider separation
- Qlib — dataset/model/backtest/evaluation separation
- RD-Agent — propose → implement → evaluate → learn research cycle
- TradingAgents — constructive/adversarial reasoning and checkpoint ideas without LangGraph in the deterministic core
- ai-hedge-fund — point-in-time honesty, ledger/risk separation and gated promotion
- Automaton — durable SQLite memory, integrity and resumable-loop patterns
- Microsoft Agent Framework — checkpoint/resume patterns retained conceptually; the framework is not imported

## Repository shape

```text
src/dusty/
  core.py            # M0–M6 frozen reasoning semantics
  journal.py         # M7 + M29 durable/batched semantic replay
  learning.py        # M8 attribution shell
  ports.py           # runtime/provider boundaries
  providers.py       # provider isolation
  experience.py      # M13–M18 point-in-time behavior learning
  research.py        # M19–M21 + M29 Strategy IR, streaming experiments, memory
  forecasting.py     # M22 forecast tournament/evidence
  resource.py        # M24 host resource governor
  library.py         # M24/M28 disk-first learning library + retrieval
  acquisition.py     # M25/M26 quarantine, translation, family/lineage
  reproduction.py    # M27 independent claim reproduction
  validation.py      # M30/M31 bounded refinement + robustness
  mt5lab.py          # M32 Strategy Tester-only contract
  operations.py      # M33/M34 reconciliation + resource-aware scheduling
  certification.py   # M12/M23/M35 certification gates
```

## Explicitly still out of scope

- MT5 order placement or demo/live order adapter
- broker credentials
- position sizing
- portfolio allocation
- live capital
- self-modifying production code
- direct LLM/model broker-write authority
- automatic reversal
- blind execution of downloaded strategy code
- wholesale imports of large agent frameworks

**M35 is a pre-execution research milestone.** It can certify that a strategy has earned the right to enter a future controlled demo-integration phase; it is not a profitability claim and it does not authorize a broker write.
