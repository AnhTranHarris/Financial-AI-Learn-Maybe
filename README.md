# Dusty Dragon — Reasoning, Learning & Event-Intelligence Core

Dusty Dragon is being rebuilt as a **Windows-first autonomous quantitative research system** for MetaTrader 5. The current development lineage contains the deterministic Reasoning Core (M0–M11), experience/research learning (M12–M23), resource-aware curriculum and MT5 validation (M24–M35), symbol-conditioned self-development (M36–M45), and macro/event intelligence (M46–M55).

It still has **no broker-write authority, no broker credentials, no position sizing, no live-money path, and no direct LLM/model execution authority**.

## Engineering laws

- **Minimum code surface, maximum behavioral coverage.**
- **Disk holds knowledge; RAM holds only the current thought.**
- **The internet supplies hypotheses, not truth.**
- **External performance is a claim until Dusty independently reproduces it.**
- **Raw MT5 market history is not duplicated merely because Dusty can duplicate it.**
- **Compute is budgeted like capital: core safety outranks background research.**
- **Dusty must consume its curriculum before expanding it.**
- **Popularity determines what deserves investigation, not what Dusty believes.**
- **Research universe may be broader than execution universe.**
- **Economic underlier is not trading instrument.**
- **Automatic news acquisition is free-only and symbol-conditioned.**
- **News updates scenarios; markets validate scenarios.**
- **Source repetition is not independent evidence.**
- **Every scenario must be falsifiable.**
- **Free does not mean useful; incremental value must be demonstrated.**

Complexity must earn its way into the repository. Immutable data, enums, protocols, pure functions, transition tables, append-only SQLite records, bounded iteration, streaming aggregation, and standard-library components are preferred over frameworks or speculative abstractions.

## Frozen Person

One synthetic Person reasons about one symbol + one strategy through four cognitive functions:

- **Analyst — construct**
- **Skeptic — attack**
- **Patience — time**
- **Guardian — govern**

They remain functions of one Person, not independent trading agents. Reasoning decisions are semantic only and **never mutate execution truth**.

## Milestone map

| Phase | Capability | Hard boundary |
|---|---|---|
| **M0–M11** | Deterministic Person, coherence, phase transitions, journal/replay, evidence/provider boundaries | Reasoning cannot mutate position/execution truth |
| **M12–M23** | Exhaustive reasoning certification, PIT experience learning, Strategy IR, experiments, graveyard, forecast tournament | Human/public/model evidence remains read-only research evidence |
| **M24–M35** | Resource governor, disk-first library, external quarantine/lineage, independent reproduction, streaming research, bounded refinement, walk-forward, MT5 tester contract/reconciliation | MT5 boundary is laboratory-only; no broker writes |
| **M36–M45** | Exact-symbol curriculum cohorts, duplicate/family compression, reasoning bridge, bounded hypothesis composition, adaptive acquisition, regime learning, real read-only MT5 worker and fidelity ladder | Dusty must consume curriculum before more acquisition; M45 only allows continued demo-execution development |
| **M46–M55** | Cross-asset market identity, free symbol-news registry, Event Capsules, unscheduled clustering, source independence, scenario forecasting, session/reaction research, strategy-event interactions, event reasoning bridge, source value gate | News never maps directly to trade authority; M55 still keeps `broker_write_authorized=False` |

Detailed event-intelligence semantics are documented in `docs/m46-m55.md`.

## Symbol-conditioned curriculum

Forex Factory, Myfxbook, TradingView, Quantpedia, QuantConnect, GitHub, papers, and future authorized sources are curriculum rather than authority.

For a target symbol Dusty can retain bounded cohorts such as:

- raw top-gain exemplars;
- research-quality exemplars;
- failure/control exemplars;
- TradingView popularity exemplars;
- related-symbol and transfer-learning material kept explicitly separate from exact-symbol evidence.

Exact strategy hashes identify concrete rules. Structural family hashes prevent copied or retuned strategies from masquerading as independent ideas. `known_at` prevents future leaderboard success from leaking into historical reconstructions.

## Event and macro intelligence

Dusty treats news as a possible update to future market states, not as a direct signal.

```text
free symbol-relevant sources
            |
            v
   normalized PIT news/events
            |
            v
 scheduled capsule / unscheduled cluster
            |
 dedupe + publisher independence
            |
            v
 conditional scenario hypotheses
 continuation / escalation / de-escalation / etc.
            |
 transmission + confirmation + invalidation
            |
            v
 cross-market + session reaction research
            |
 strategy × event interaction memory
            |
            v
 Analyst + Skeptic + Patience + Guardian
            |
            v
 testable strategy hypothesis
```

The core does not infer simplistic causal rules such as `war -> oil up`. Scenario creation requires an explicit premise, economic transmission channels, confirmation criteria, and invalidation criteria. Low-liquidity movement followed by London/New York participation is measured as a research relationship, not hard-coded as a pre-positioning rule.

## Cross-asset identity

Dusty separates economic underlier from instrument identity. For example, XAUUSD CFD and a COMEX gold future may share `economic_underlier=GOLD` while retaining separate symbols, venues, contracts, expiries, costs, and histories.

The research universe can therefore include FX, metals, energy, crypto, futures, indices, and selectively equities without implying that every researched market is eligible for execution.

## Resource discipline

Dusty stores compact knowledge and conclusions rather than a second giant raw market warehouse. MT5 remains the primary broker-history/tester cache. External curriculum and news acquisition are bounded.

M54 additionally measures **incremental research utility** by source. A source that is free and relevant can still be paused if it repeatedly fails to improve the caller-defined research utility relative to a baseline.

## MT5 boundary

The MT5 architecture uses an explicit terminal executable and a read-only historical/tester boundary. Coarse testing modes screen candidates before expensive high-fidelity work:

```text
Dusty cheap experiment
        -> walk-forward
        -> MT5 Open Prices
        -> MT5 1-Minute OHLC
        -> MT5 Every Tick
        -> MT5 Real Ticks
```

Material disagreement with MT5 is a rejection/research signal, not something an AI may explain away. No `order_send` surface exists in the current research core.

## Reference-repository genetics

Dusty borrows engineering ideas rather than dependency trees:

- **Kronos** — specialized financial K-line forecast evidence boundary
- **Chronos** — probabilistic/quantile and covariate-aware forecast evidence
- **Uni2TS/Moirai** — rolling evaluation and universal time-series benchmarking
- **Vibe-Trading** — PIT research, provider/instrument provenance, shadow validation, fail-closed execution lessons
- **Qlib** — dataset/model/backtest/evaluation separation and disk-reloadable research state
- **RD-Agent** — hypothesis → quantitative validation → feedback → refinement
- **TradingAgents** — constructive/adversarial research, PIT news lessons, durable decision memory/checkpoint concepts without importing LangGraph
- **ai-hedge-fund** — mandate/alpha-model separation and backtestable research components
- **Automaton** — budgeted memory/retrieval, durable SQLite state and policy concepts; self-modification/replication are rejected
- **Microsoft Agent Framework** — checkpoint/resume after completed workflow steps; framework runtime remains unnecessary while Dusty's primitives suffice

## Current repository shape

```text
src/dusty/
  core.py                    # frozen deterministic reasoning
  experience.py              # PIT human/market episodes
  research.py                # Strategy IR + streaming experiments + graveyard
  forecasting.py             # forecast evidence tournament
  resource.py                # host resource governor
  library.py                 # disk-first curriculum memory
  acquisition.py             # external quarantine/translation/lineage
  reproduction.py            # independent claim reproduction
  validation.py              # bounded refinement + walk-forward
  mt5lab.py / mt5worker.py   # tester contract + read-only Windows worker
  operations.py / fidelity.py# reconciliation, scheduling, fidelity ladder
  curriculum.py              # exact-symbol cohorts + knowledge compression
  reasoning_bridge.py        # bounded curriculum evidence bridge
  hypothesis.py              # bounded strategy composition
  adaptive.py                # acquisition/regime budgets
  development.py             # self-development tournament
  markets.py                 # underlier/instrument identity
  news.py                    # free-only symbol-conditioned news policy
  events.py                  # scheduled Event Capsules
  scenario.py                # unscheduled clusters + falsifiable scenarios
  event_research.py          # reaction/session + strategy-event research
  event_reasoning.py         # bounded scenario evidence bridge
  information_value.py       # source incremental-value gate
  event_certification.py     # M55 event-intelligence gate
```

## Explicitly still out of scope

- MT5 demo/live order placement
- broker credentials
- position sizing and portfolio allocation
- live capital
- self-modifying production code
- direct LLM/model/news broker-write authority
- automatic reversal
- blind execution of downloaded strategy code
- paid/restricted automatic news feeds
- wholesale imports of large agent frameworks

**M55 is still a pre-execution research milestone.** Passing it means Dusty has a certified symbol-conditioned curriculum and macro/event research layer suitable for the next controlled demo-execution engineering phase. It is not a profitability claim and it does not authorize a broker write.
