# Dusty Dragon — Reasoning + Learning Research Core

Dusty Dragon is being rebuilt as a **Windows-first autonomous quantitative research system**. The current branch contains the deterministic Reasoning Core (M0–M11) plus a compact research-learning layer (M12–M23). It still has **no broker-write authority, no position sizing, no live-money path, and zero third-party runtime dependencies**.

## Engineering law

**Minimum code surface, maximum behavioral coverage.**

Complexity must earn its way into the repository. Immutable data, enums, protocols, pure functions, transition tables, append-only SQLite records, and standard-library components are preferred over frameworks or speculative abstractions.

## Frozen Person

One synthetic Person reasons about one symbol + one strategy through four cognitive functions:

- **Analyst — construct**
- **Skeptic — attack**
- **Patience — time**
- **Guardian — govern**

They remain functions of one Person, not independent trading agents. Reasoning decisions are semantic only and **never mutate execution truth**.

## M0–M11 — Reasoning Core v1

M0–M11 established the canonical vocabulary, deterministic lifecycle, four-function Person, asymmetric decision synthesis, coherence gate, E1/E2/E3 exception semantics, semantic position reasoning, SQLite journal/replay, offline attribution shell, evidence-provider SDK, replaceable forecast/research adapter prototypes, and the synthetic cognitive laboratory.

The Reasoning Core is now treated as frozen unless measured failures justify a change.

## M12–M23 — Learning Research Core

| Milestone | Capability | Gate |
|---|---|---|
| **M12** | Exhaustive Reasoning Core certification | all 6,480 categorical semantic combinations deterministic; execution truth never mutates |
| **M13** | Universal trading-experience schema | one validated episode representation across human, research, MT5, and Dusty sources |
| **M14** | Human demonstration source firewall | Forex Factory / Myfxbook records preserve source, grade, verification, and reference identity |
| **M15** | Point-in-time context | a historical action can only see facts whose `known_at` is not later than that action |
| **M16** | Trade-story reconstruction | chronological entry/scale/exit episodes validate loudly before learning |
| **M17** | Behavioral archetype discovery | outcome-free behavior signatures are grouped before outcome statistics are attached |
| **M18** | Counterfactual learning | nearby alternatives use only prices actually present in the observed historical path |
| **M19** | Constrained Strategy IR | strategies are declarative, hashable, deterministic specifications; no generated Python/MQL5 |
| **M20** | Rapid historical experiment screen | deterministic feature → outcome experiments include explicit cost drag and cheap rejection gates |
| **M21** | Strategy memory / graveyard | append-only SQLite remembers challengers, rejection reasons, promotions, and duplicate hashes |
| **M22** | Forecast evidence tournament | Kronos/Chronos/Moirai/baselines share one scoring contract and become evidence, never trade commands |
| **M23** | Research qualification gate | all research invariants must pass before read-only shadow research; broker writes remain impossible |

## Human-reference curriculum

The research layer intentionally separates **acquisition** from **learning semantics**. Website HTML is not imported into the core.

Structured observations can be normalized from:

- Forex Factory Trades → observed human entry/exit behavior
- Forex Factory Calendar → point-in-time macro/event context
- Myfxbook → historical/forward/demo/live provenance and trade outcomes when structured records are available
- later MT5 history → broker-specific market and execution observations
- Dusty's own research journal → self-generated experiments and lessons

`SourceGrade` distinguishes `LIVE`, `DEMO`, `FORWARD_TEST`, `BACKTEST`, and `UNKNOWN`. A public website claim therefore never silently becomes market truth.

## Research loop

```text
human demonstrations + point-in-time market context
                    |
                    v
              TradingEpisode
                    |
        +-----------+-----------+
        |                       |
        v                       v
behavior archetypes      counterfactuals
        |                       |
        +-----------+-----------+
                    v
             StrategySpec
                    |
                    v
         deterministic experiment
                    |
           +--------+--------+
           |                 |
        reject             survive
           |                 |
           v                 v
      graveyard          challenger
           |                 |
           +--------+--------+
                    v
              research memory
```

The research objective is **hypotheses falsified and useful lessons retained per unit of compute**, not raw backtest count.

## Forecast models

Kronos, Chronos, Moirai, simple statistical models, or future providers are replaceable specialists. They compete through normalized `Forecast` objects and out-of-sample `ForecastScore` results. A forecast is converted to `EvidenceItem` records before the Person can reason about it.

There is no `forecast -> order` path.

## Reference-repository genetics

The rebuild continues to borrow ideas rather than dependency trees:

- Kronos — specialized financial time-series forecasting boundary
- Chronos — probabilistic forecast pipeline/evaluation concepts
- Uni2TS/Moirai — universal forecast evaluation and rolling-test concepts
- Vibe-Trading — point-in-time research, Shadow Account, market-data/provider separation, validation funnel
- Qlib — data/model/backtest/evaluation separation
- RD-Agent — propose → implement → evaluate → learn research cycle
- TradingAgents — adversarial reasoning/checkpoint concepts without LangGraph in the core
- ai-hedge-fund — point-in-time honesty, one-pipeline aspiration, gated promotion, LLM never directly executes
- Automaton — durable SQLite memory, integrity checks, resumable autonomous loops
- Microsoft Agent Framework — checkpoint/resume patterns retained only if Dusty's simpler primitives later prove insufficient

No reference framework is imported wholesale.

## Repository shape

```text
src/dusty/
  core.py           # M0–M6 reasoning semantics
  journal.py        # M7 durable semantic replay
  learning.py       # M8 attribution shell
  ports.py          # M0/M9 runtime boundaries
  providers.py      # M9/M10 provider isolation
  certification.py  # M12 + M23 certification gates
  experience.py     # M13–M18 human/market experience learning
  research.py       # M19–M21 Strategy IR, experiments, memory
  forecasting.py    # M22 forecast tournament + evidence translation
```

## Explicitly still out of scope

- MT5 order placement
- broker credentials
- position sizing
- portfolio allocation
- live capital
- self-modifying production code
- direct LLM broker-write authority
- automatic reversal
- generated strategy source code
- importing large agent frameworks merely for orchestration

M23 means **ready to begin controlled read-only shadow research**, not ready for live trading and not a profitability claim.
