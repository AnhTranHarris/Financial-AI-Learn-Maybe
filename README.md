# Dusty Dragon

Dusty Dragon is a Windows-first quantitative research and controlled-demo trading architecture built around MetaTrader 5.

The project is designed to **learn → reason → test → falsify → remember → improve** while keeping model intelligence, capital allocation, risk governance, and broker execution as separate authority layers.

> Dusty Dragon does not promise profitability. Historical, simulated, model, public-strategy, or demo results are evidence to evaluate, not guarantees of future returns.

## Authority state

Current engineering phase: **M0–M103 plus investment-trust remediation**.

- deterministic reasoning core: implemented;
- point-in-time evidence and research memory: implemented;
- public strategy curriculum and event/scenario research: implemented;
- realistic account research and statistical overfit defenses: implemented;
- Money Manager, Risk Constitution, Quant Portfolio Manager, and Growth Manager: implemented;
- StrategySpecV2 executable runtime: implemented;
- MT5 research/parity boundary: implemented, including a Strategy-Tester-only EA contract;
- controlled **DEMO-only** execution boundary: implemented;
- durable order/deal/position lifecycle and multi-terminal supervisor: implemented;
- six-desk/chaos certification framework: implemented;
- investment-trust review: implemented for software proof and explicit native/live evidence qualification;
- MT5 indicator/chart intelligence and StrategySpecV3 lifecycle reasoning: implemented;
- probabilistic forecast evaluation and bounded challenger governance: implemented;
- local Windows MT5 discovery, read-only terminal/account/symbol inventory, strategy/mode gates,
  a bare-bones desktop shell and a least-privilege Codex bridge: implemented;
- connected, bounded read-only MT5-history research with immutable seed packages and saved results: implemented;
- broker minimum-lot display, timestamped balance refresh, explanatory sizing thresholds and versioned numerical provenance: implemented;
- fixed historical development/holdout windows, past-only warm-up, boundary guards and explicit cost-source/observation provenance: implemented;
- **live-money write authority: false; the desktop's Demo and Live modes remain locked**.

M75/M85 engineering does **not** mean that a real six-desk demo certification has already occurred. Operational certification requires real local MT5 indicator/chart/tester/demo evidence, the required chaos runs, and six individually passing desk runs. GitHub-hosted CI cannot substitute for those broker-terminal observations.

The investment-trust remediation likewise does not manufacture native MT5 evidence. It proves the software evaluation paths and defines the exact local/native evidence required before indicator or execution claims can become operational facts.

## Permanent laws

1. Internet material supplies hypotheses and context, not truth.
2. Public performance is a claim until independently reproduced.
3. News, an LLM, or a forecast model can never directly authorize a broker write.
4. Point-in-time correctness is mandatory from storage through reasoning.
5. Economic underlier, broker instrument, and contract economics are separate concepts.
6. Popularity determines what Dusty investigates first; independent testing determines what Dusty believes.
7. Research may propose opportunity. Money management may size it. Portfolio management may allocate it. Growth management may reduce deployment. Risk and Guardian may veto it. No lower layer may enlarge the constitutional risk envelope.
8. Position size is derived from stop distance and approved loss budget. Losses never trigger recovery sizing.
9. Martingale, loss-recovery sizing, unbounded averaging, HFT, latency-critical execution, and prohibited scalping cannot be promoted by good backtests.
10. A profitable rule violation is still a governance failure.
11. Broker/session identity drift latches permanently for that session object; recovery requires a new fully verified session.
12. A crash after an ambiguous broker send is reconciled from broker state; the intent is never blindly resubmitted.
13. Open-position supervision outranks research and training under resource pressure.
14. Dusty never autonomously pays for data.
15. Live trading remains outside the currently authorized implementation, including M103.
16. Analytical tools are versioned dependencies; invalid, repainting, future-dependent, drifted, or semantically unknown tools cannot be rescued by attractive performance.
17. A deployed Champion is immutable. Tool modification or removal creates a new challenger and repeats native/backtest/demo certification.

## Architecture

```text
public strategies / free event context / MT5 history
                     │
                     ▼
              point-in-time evidence
                     │
                     ▼
      Analyst + Skeptic + Patience + Guardian
                     │
                     ▼
               StrategySpecV2
             one typed runtime
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   cheap screen  realistic     MT5 tester
                  ledger       deal parity
        └────────────┼────────────┘
                     ▼
             statistical reality gate
                     │
                     ▼
              frozen eligible strategy
                     │
                     ▼
                 Money Manager
                     │
                     ▼
            Quant Portfolio Manager
                     │
                     ▼
                Growth Manager
                     │
                     ▼
               Risk Constitution
                     │
                     ▼
                   Guardian
                     │
                     ▼
            immutable OrderIntent
                     │
                     ▼
          broker-native DEMO preflight
                     │
                     ▼
        DEMO-only MT5 execution adapter
                     │
                     ▼
             order / deal / position
                     │
                     ▼
       protection + reconciliation supervisor
                     │
                     ▼
        capital attribution + future research
```

## Milestone phases

- **M0–M12 — deterministic reasoning core:** finite states, legal transitions, evidence coherence, exception hierarchy, and exhaustive reasoning certification.
- **M13–M23 — experience and research:** human/public behavior normalization, point-in-time context, declarative strategies, streaming experiments, memory, forecasts, and shadow-research qualification.
- **M24–M35 — durable research laboratory:** resource governance, SQLite learning library, external strategy quarantine/reproduction, robustness, and MT5 research abstractions.
- **M36–M45 — symbol curriculum and MT5 training gate:** exact-symbol curriculum, compression, method knowledge, bounded hypothesis composition, adaptive research, and read-only multi-fidelity MT5 validation.
- **M46–M55 — event intelligence:** market/underlier identity, free-source policy, scheduled/unscheduled events, source independence, scenarios, event reactions, source value, and event certification.
- **M56–M65 — research reality and capital governance:** semantic hardening, broker economics, Strategy IR v2, realistic account ledger, statistical overfit defenses, Money Manager, Risk Constitution, Quant PM, Growth Manager, and evidence-backed pre-demo certification.
- **M66–M75 — semantic unification and controlled Demo Desk:** one executable V2 runtime, MT5 deal parity, empirical portfolio risk, capital reputation, latched demo identity, immutable order intents, DEMO-only send boundary, durable execution lifecycle, multi-terminal supervision, and six-desk chaos certification.
- **M76–M85 — MT5 indicator, chart and trade intelligence:** durable analytical-tool registry, generated native/custom indicator probes, chart-object semantics, temporal/repainting validation, typed analysis graph, StrategySpecV3 long/short lifecycle, ablation/retirement, native tournament, frozen Demo analysis, pending-order preflight, and firm-mandate certification.
- **M86–M95 — quant forecasting and bounded autonomous improvement:** typed probabilistic forecasts, point-in-time datasets, broker-native market time, proper scoring/calibration, forecast cognition, immutable challenger refinement, purged walk-forward promotion, append-only Demo forecast evidence, realized-capital opportunity expansion, and exact-head certification.
- **M96–M100 — local runtime foundation and basic UI:** bounded Windows MT5 discovery, explicit terminal confirmation, read-only account/symbol/order/position/deal inventory, revocable strategy/mode selection gates, sanitized Codex reporting/development, and a disposable one-window desktop shell.
- **M101 — connected research correction:** exact reviewed seed packages, selected-terminal history acquisition, two-stage laboratory simulation, cancellable spawned worker, hashed local evidence and visible research results. This is not native Strategy Tester certification or autonomous forecasting.
- **M102 — sizing transparency and reproducibility:** broker minimum lot, last-checked account balance, read-only refresh before research, sample-derived sizing-only balance estimates, rejection explanations and explicit cross-Python indicator arithmetic. No risk limit or trading gate is relaxed.
- **M103 — fixed-window evaluation foundation:** optional fixed UTC end and chronological holdout, independent flat-start simulations with past-only indicator warm-up and entry-tail guards, frozen cost notes and separate read-only broker cost observations. Historical data is not asserted to be previously unseen; costs are not automatically verified.

See [`docs/m66-m75.md`](docs/m66-m75.md) for the M66–M75 phase in detail.

See [`docs/m75-investment-trust-review.md`](docs/m75-investment-trust-review.md) for the four-capability proof standard, execution/spread semantics, cash-economics parity rule, and native MT5 evidence campaign.

See [`docs/m76-m85.md`](docs/m76-m85.md) for the indicator/chart intelligence, complete trade lifecycle, tool governance, native tournament and firm-mandate phase.

See [`docs/m86-m95.md`](docs/m86-m95.md) for probabilistic forecasting, autonomous challenger research, market-clock behavior, forward-test evidence, capital-opportunity growth and the M95 native proof campaign.

See [`docs/m96-m100.md`](docs/m96-m100.md) for the local Windows terminal inventory,
bare-bones UI and Codex safety boundary.

## Bare-bones local UI

The desktop shell is intentionally small. It discovers supported local MT5 executables,
requires the user to connect one explicitly, reads its current account and broker symbol inventory,
and displays compatible strategies and the Backtest/Demo/Live gates. Discovery is not account
assignment, a green UI gate is not broker authority, and the shell does not invent an executable
runtime when no coordinator is configured. M101 configures only the read-only history research
coordinator; it never configures a demo or live order loop.

From an installed development checkout on Windows:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . MetaTrader5
.\.venv\Scripts\python.exe -m dusty.basic_ui --repository .
```

Portable or nonstandard installations can be supplied explicitly without storing credentials:

```bash
dusty-dragon --repository . --terminal "C:\\Trading\\BrokerA\\terminal64.exe"
```

A reviewed metadata-only strategy catalog may be supplied with `--catalog`. Catalog loading never
loads or executes online code. Without it, two built-in **RESEARCH ONLY** RSI momentum hypotheses
appear for each symbol. They have no profitability or online-discovery claim. Start asks for a
bounded history window and explicit cost assumptions, then runs the existing Python laboratory on
MT5 history. Research Results shows the outcome and saved artifact directory. Demo/Live stay locked
even after positive simulated P&L. Unsupported currency conversions/nonlinear economics are rejected.
See [M101 development audit and beginner test steps](docs/m101-connected-research.md).

M102 adds **Refresh Account**, the selected symbol's broker minimum lot/volume step, and the
last-checked balance (not a live feed). Start refreshes the account before freezing its research
balance. Completed research explains growth rejections and displays a **preferred balance (risk
sizing only)**: the highest minimum-lot risk threshold among that run's sized setups. This is not
a deposit recommendation, a profitability claim or an amount that guarantees trading approval.
It excludes margin and unmodeled costs/gap losses; incomplete sizing yields no preferred estimate.
See [M102 definition, QC and safe update steps](docs/m102-sizing-transparency.md).

The Start dialog also accepts an optional fixed UTC end, holdout days and cost-source note.
A fixed historical holdout reports development and holdout separately, without parameter tuning
or capital carryover. The main sizing estimate applies to the holdout only. Recent broker execution
costs are recorded separately and never silently replace user assumptions. Neither a source note,
a historical split nor a profitable result unlocks trading. See [M103 scope and test procedure](docs/m103-fixed-window-evaluation.md).

Report requests use an existing local Codex CLI login in an
ephemeral read-only sandbox. Development requests require an explicit confirmation, a clean Git
worktree, an inactive Dusty runtime, and a workspace-write sandbox; Codex receives no MT5 order
surface or broker credential from Dusty. Desktop tasks are single-flight; development requires a
restart afterward. These application controls are not an operating-system security boundary against
another process or a user changing MT5 independently.

## M66–M75 safety highlights

### One strategy meaning

`StrategySpecV2` may retain source prose for research, but promotion into executable behavior requires Dusty's small typed runtime DSL. Unsupported exit semantics fail closed. The same Python runtime produces the decisions used by realistic research, tester manifests, shadow decisions, and future demo intents.

### MT5 remains an independent execution laboratory

`mt5/DustyResearchEA.mq5` refuses to initialize outside MetaTrader Strategy Tester. It consumes precomputed Dusty actions rather than re-implementing indicators, then exports normalized MT5 deal evidence. This avoids maintaining two competing strategy implementations.

### Broker-native economics

Before a demo intent can be sent, Dusty uses the connected MT5 environment for profit-at-stop, margin, current price, and `order_check` validation. Dusty's own arithmetic remains an independent research/sanity layer.

### Latched sessions

A verified `DemoSession` loses authority permanently if it detects DEMO→REAL drift, account/server drift, terminal/build drift, permission loss, or broker symbol-specification drift. A later normal observation cannot revive the same session object.

### Crash-safe broker writes

The execution ledger records `SENT_UNKNOWN` before `order_send()`. If the process loses the response, the intent remains non-resendable until broker orders/deals/positions are reconciled. Zero broker matches remain ambiguous and can be checked again; multiple conflicting matches fault closed.

### Resource hierarchy

When the host is under pressure, open-position supervision, broker reconciliation, and journaling survive while forecasting, research, backtesting, and training are progressively throttled.

### Six-desk rule

The certification framework requires six passing desk runs. Sequential runs are valid when hardware cannot support six concurrent terminals, but every run must pass individually. One failed desk invalidates the certification round.

## Research genetics, not dependency imports

Dusty borrows engineering ideas from ten reference repositories while keeping a small auditable runtime:

- Kronos — specialist financial K-line forecasting evidence;
- Chronos-2 — probabilistic, multivariate, and covariate-informed challenger forecasts;
- Uni2TS/Moirai — rolling probabilistic evaluation;
- Vibe-Trading — exact-symbol, unit/economics, warm-up, fail-closed execution, durable send ownership, and broker reconciliation lessons;
- Microsoft Qlib — loosely coupled data/model/strategy/backtest/portfolio/execution layers;
- Microsoft RD-Agent — hypothesis → implementation → quantitative test → feedback;
- TradingAgents — point-in-time and durable checkpoint lessons, without making LLM debate execution authority;
- ai-hedge-fund — persistent mandate/fund separate from the ticker universe;
- Automaton — durable SQLite state, heartbeat/resource-state and immutable-law concepts only; self-funding, replication and runtime sovereign self-modification are rejected;
- Microsoft Agent Framework — workflow fingerprints, checkpoint ancestry, committed-state restore, and atomic persistence patterns without importing the framework runtime.

## Development and CI

Runtime dependencies remain intentionally minimal. GitHub Actions runs compile and unittest on both Windows and Ubuntu, with named gates for feature semantics, cognition derivation, execution clock, investment-lab proofs, data/event acquisition, MT5 symbol economics, MT5 execution parity, M75 trust review, demo execution/supervision, M76–M85 analytical intelligence, M86–M95 forecasting intelligence, category suites, and the full unittest suite.

```bash
python -m compileall -q src tests
python -m unittest discover -s tests -v
```

Real MT5 Strategy Tester compilation, tester execution, broker-native demo preflight, chaos tests, and six-desk certification require a local Windows + MetaTrader 5 integration environment. Cloud CI uses deterministic injected boundaries and must not be represented as broker certification.
