# Dusty Dragon — Autonomous Quantitative Research Core

Dusty Dragon is being rebuilt as a **Windows-first autonomous quantitative research system for MetaTrader 5**.

The current development lineage now covers:

- **M0–M11** — deterministic reasoning core;
- **M12–M23** — experience, strategy research and learning;
- **M24–M35** — resource discipline, research memory, robustness and MT5 laboratory validation;
- **M36–M45** — exact-symbol public curriculum and bounded self-development;
- **M46–M55** — cross-asset, news/event and scenario intelligence;
- **M56–M65** — semantic integrity, broker economics, Strategy IR v2, realistic backtesting, statistical reality, capital/risk/portfolio governance and evidence-backed pre-demo certification.

Dusty still has **no broker-write authority, no live-money path, no direct LLM/model/news execution authority and no automatic demo-to-live promotion**.

## Engineering laws

- **Learn → reason → test → falsify → remember → improve.**
- **Minimum code surface, maximum behavioral coverage.**
- **Disk holds knowledge; RAM holds only the current thought.**
- **The internet supplies hypotheses, not truth.**
- **External performance is a claim until Dusty independently reproduces it.**
- **Popularity determines what Dusty investigates first; performance and independent testing determine what Dusty believes.**
- **Research universe may be broader than execution universe.**
- **Economic underlier is not trading instrument.**
- **MT5 owns raw broker history; Dusty owns knowledge about markets.**
- **Acquisition must answer a research question or fill a measured gap.**
- **Automatic news acquisition is free-only, legitimate and symbol-conditioned.**
- **News updates scenarios; markets determine whether scenarios are being priced.**
- **Source repetition is not independent evidence.**
- **Every scenario must be falsifiable.**
- **Free does not mean relevant. Relevant does not mean useful. Useful must be demonstrated.**
- **Dusty never autonomously pays for data.**
- **No model, LLM, article or external strategy has broker-write authority.**
- **A profitable rule violation is a governance failure. A rule-following loss is valid research evidence.**
- **Loss increases investigation priority, not position size.**

Complexity must earn its place. Immutable data, enums, pure functions, protocols, transition tables, append-only SQLite records, bounded loops and standard-library components are preferred over framework-heavy runtimes.

## Frozen Person reasoning model

One synthetic Person reasons about one symbol + one strategy through four cognitive functions:

- **Analyst — construct**
- **Skeptic — attack**
- **Patience — time**
- **Guardian — govern**

These are functions of one Person, not independent agents voting on trades. Semantic reasoning never mutates broker or position truth.

## Milestone map

| Phase | Capability | Hard boundary |
|---|---|---|
| **M0–M11** | Deterministic Person, coherence, phase transitions, journal/replay, evidence/provider boundaries | Reasoning cannot mutate execution truth |
| **M12–M23** | PIT experience learning, Strategy IR v1, experiments, graveyard, forecast tournament | Human/public/model evidence remains research evidence |
| **M24–M35** | Resource governor, disk-first library, quarantine/lineage, independent reproduction, walk-forward, MT5 laboratory/reconciliation | MT5 remains research-only |
| **M36–M45** | Exact-symbol curriculum, duplicate/family compression, bounded hypothesis composition, adaptive acquisition/regime learning | Curriculum must be consumed before uncontrolled acquisition |
| **M46–M55** | Market identity, free symbol-news policy, Event Capsules, scenario forecasting, reaction/session research, source-value gate | News never maps directly to trade authority |
| **M56–M65** | PIT hardening, instrument economics, Strategy IR v2, realistic ledger, multiple-testing defenses, Money/Risk/Quant/Growth managers, pre-demo evidence bundle | Passing M65 authorizes only Demo Desk engineering; broker writes remain false |

Detailed phase semantics:

- `docs/m36-m45.md`
- `docs/m46-m55.md`
- `docs/m56-m65.md`

## Research and curriculum

Forex Factory, Myfxbook, TradingView, Quantpedia, QuantConnect, GitHub, papers and future authorized sources are **curriculum**, not authority.

For a target symbol Dusty can maintain bounded cohorts such as:

- raw high-gain exemplars;
- research-quality exemplars;
- failure/control exemplars;
- popularity-ranked discovery exemplars;
- related-symbol/transfer material kept separate from exact-symbol evidence.

Exact strategy hashes identify concrete rules. Structural family identity prevents copied or lightly retuned strategies from masquerading as independent ideas. Point-in-time timestamps prevent later leaderboard success from leaking into earlier reconstructions.

## Event and scenario intelligence

Dusty treats news as an update to possible world states, not a direct signal.

```text
free symbol-relevant sources
        -> normalized PIT news/events
        -> scheduled capsule / unscheduled cluster
        -> dedupe + publisher independence
        -> falsifiable scenarios
        -> transmission + confirmation + invalidation
        -> cross-market/session reaction research
        -> strategy × event interaction memory
        -> Analyst + Skeptic + Patience + Guardian
        -> testable hypothesis
```

The system does not hard-code simplistic causal rules such as `war -> oil up`. Scenarios require an explicit premise, transmission path, confirmation criteria and invalidation criteria.

## M56-M60: research reality

### Semantic integrity

The deterministic core now rejects evidence observed after the reasoning instant. Trade episodes use quantity-aware cash-flow PnL for scale-in/scale-out. Event-reaction research can reject overlapping intervals instead of accidentally double-counting cumulative market moves.

### Broker/instrument economics

`InstrumentEconomics` models contract size, tick size/value, broker volume constraints, margin, commission, swaps and stop/freeze distances. Values must be finite. Broker volume normalization rounds downward only.

The read-only MT5 worker can capture broker symbol/account specifications without exposing an order surface.

### Strategy IR v2

The versioned declarative strategy representation supports grouped entries, mandatory stops, targets, trailing/breakeven, maximum hold, sessions, event exclusions, cooldown and bounded scaling.

Deployment promotion is constitutionally prohibited for scalping, HFT, latency-critical strategies, martingale, loss-recovery sizing, unbounded averaging, sub-M5 decisions and sub-15-minute intended horizons. High execution sensitivity is research-only by default.

### Realistic simulator

The original cheap experiment remains a screening engine. A separate realistic account ledger supports overlapping positions, realized/unrealized PnL, explicit costs, margin, equity and true high-water drawdown.

Warm-up is not scored. Dusty constructs purge/embargo walk-forward ranges so immediate test-boundary information is not silently recycled into subsequent training.

### Statistical reality

Dusty records **all trials**, including failures, in append-only research history. M60 adds deterministic bootstrap intervals, family-wise multiple-testing adjustment, profit concentration, parameter-neighborhood stability and a bounded PBO-style overfit proxy.

Search breadth increases the burden of evidence. Zero-variance positive return streams are suspicious rather than magically perfect.

## M61-M64: capital authority chain

The capital chain is deliberately decomposed:

```text
frozen eligible strategy
        |
        v
Money Manager
  stop -> expected loss -> volume
        |
        v
Quant Portfolio Manager
  allocate supplied risk budget
        |
        v
Portfolio Growth Manager
  deploy / de-risk / research-only
        |
        v
Risk Constitution + Guardian
  final veto
```

### Money Manager

Two sizing concepts are separate:

- `MINIMUM_LOT_STRATEGY_TEST` — research-only broker-feasibility sizing;
- `GROWTH_RISK` — percentage-risk sizing inside an approved capital envelope.

If the broker minimum lot does not fit the allowed risk, Dusty chooses **zero volume**. It never rounds upward to manufacture a trade.

### Risk Constitution

Default limits currently encode conservative research assumptions including 0.25% normal trade risk, 0.50% Champion soft maximum and a 1.00% absolute per-trade ceiling, plus symbol/portfolio heat, daily/weekly loss, drawdown and margin gates.

Initial stops are mandatory. Stop widening, martingale, loss-recovery sizing and unbounded averaging are prohibited.

Zero equity is a deterministic FAILED state, not an exception.

### Quant Portfolio Manager

The Quant PM allocates only a supplied risk budget using equal-risk, inverse-volatility or quality/volatility/correlation methods. Per-symbol and signed factor heat are bounded. Unused risk is a valid result.

The Quant PM cannot create risk merely because capital is available.

### Portfolio Growth Manager

Capital health states are THRIVING, HEALTHY, CAUTION, DEFENSIVE, CRITICAL and CAPITAL_INSUFFICIENT.

Even THRIVING has a maximum deployment multiplier of `1.0`: growth may expand the opportunity set but may not enlarge constitutional percentage-risk limits. Drawdown compresses deployment. Critical/insufficient states become research-only.

External deposits are separated from trading PnL. Capital-compression research is earned cycle by cycle; failures repeat the same starting capital. At micro-capital, zero eligible strategies is considered a safe, valid conclusion.

## M65 pre-demo certification

`pre_demo_certification.py` binds M56-M64 evidence to one commit using:

- artifact hash;
- data fingerprint;
- configuration fingerprint;
- test fingerprint;
- commit SHA;
- pass/fail state.

Missing, duplicate, failed or commit-mismatched evidence blocks readiness. The final bundle has a deterministic SHA-256 identity and stable checkpoint payload.

Even a complete passing certification has:

```text
broker_write_authorized = False
```

and may only set:

```text
ready_for_demo_execution_engineering = True
```

That distinction is permanent: certification of research/governance is not authorization to trade.

## Adversarial closure testing

The M56-M65 closure suite includes deterministic invariant tests beyond example cases:

- future-evidence rejection;
- scaled-trade cash-flow accounting;
- overlapping event-return rejection;
- Strategy IR canonicalization and execution-style prohibitions;
- realistic equity/margin/drawdown accounting;
- purge/embargo boundaries;
- search-adjusted statistical rejection;
- zero-equity fail-closed states;
- rejection of `NaN`/infinite financial inputs;
- growth sizing across grids of equity, risk and stop distance;
- proof that volume never rounds upward and expected loss stays inside budget;
- all portfolio allocation methods across multiple budgets;
- proof that portfolio/symbol/factor heat stays bounded;
- monotone capital compression.

CI compiles and runs the full unittest suite on both `windows-latest` and `ubuntu-latest` using Python 3.11.

## MT5 boundary

Dusty's current MT5 boundary remains laboratory/read-only. Coarse modes screen candidates before expensive high-fidelity validation:

```text
Dusty cheap experiment
        -> leakage/statistical gates
        -> realistic account ledger
        -> MT5 Open Prices
        -> MT5 1-Minute OHLC
        -> MT5 Every Tick
        -> MT5 Real Ticks
```

Material disagreement with MT5 is rejection/research evidence, not something an AI may explain away. No `order_send` surface exists in M56-M65.

## Reference-repository genetics

Dusty borrows engineering ideas rather than dependency trees:

- **Kronos** — specialist financial K-line forecast evidence;
- **Chronos** — probabilistic/quantile/covariate forecast evidence;
- **Uni2TS/Moirai** — rolling probabilistic evaluation;
- **Vibe-Trading** — PIT, symbol/unit correctness, warm-up, purged-CV, minimum-lot and fail-closed lessons;
- **Qlib** — loose-coupled data/model/backtest/research separation;
- **RD-Agent** — hypothesis → implementation → quantitative validation → feedback;
- **TradingAgents** — PIT and durable memory/checkpoint lessons without importing LangGraph;
- **ai-hedge-fund** — persistent mandate and backtestable alpha separation;
- **Automaton** — durable state/resource-tier/immutable-law ideas only; autonomous funding, replication and runtime self-modification are rejected;
- **Microsoft Agent Framework** — restart/checkpoint patterns without importing the framework runtime.

## Current repository shape

```text
src/dusty/
  core.py                     # deterministic Person + PIT coherence
  experience.py               # quantity-aware PIT episodes
  research.py                 # cheap Strategy IR v1 screen + strategy memory
  strategy_ir.py              # Strategy IR v2 + eligibility constitution
  backtest.py                 # realistic account ledger + purge/embargo folds
  statistical.py              # trial registry + multiple-testing/overfit diagnostics
  markets.py                  # instrument identity + broker economics
  capital.py                  # Money Manager / stop-first sizing
  risk.py                     # Risk Constitution + outcome quality
  portfolio.py                # Quant Portfolio Manager
  growth.py                   # capital-health / growth / compression policy
  pre_demo_certification.py   # evidence-backed M65 gate
  mt5lab.py / mt5worker.py    # research-only MT5 boundary
  curriculum.py               # exact-symbol curriculum
  news.py / events.py         # free PIT event inputs
  scenario.py                 # falsifiable scenario hypotheses
  event_research.py           # event/session reaction research
  information_value.py        # source incremental-value gate
  library.py                  # disk-first knowledge
  resource.py                 # host resource governor
```

## Explicitly still out of scope at M65

- MT5 demo order placement;
- MT5 live order placement;
- broker credentials in the research core;
- automatic demo → live escalation;
- live capital;
- self-modifying production code;
- direct LLM/model/news broker-write authority;
- automatic reversal;
- blind execution of downloaded strategy code;
- paid/restricted automatic news acquisition;
- wholesale import of large agent frameworks.

**M65 is the end of pre-demo research and capital-governance engineering.** Passing it is not a profitability claim. The next phase may design the Controlled Demo Desk, but that phase must earn its own execution authority through new environment, session, order-intent, reconciliation and multi-desk certification gates.
