# M75 Investment Trust Review

## Question

> **Can we actually trust what M0–M75 says it knows?**

Dusty Dragon answers this question with evidence classes, not confidence language.

The investment-review remediation does not add another trading milestone. It audits the scientific and execution claims already made by M0–M75 and defines what evidence is required before each claim can be promoted from software correctness to operational fact.

The governing principle is:

> **A test may prove the code path that evaluates a claim. It may not fabricate the external observation needed to prove the claim itself.**

This review branch begins from the original M75 head:

`76a0460e15afc34f1172fea9c5fa3d053711d8b8`

The review branch is:

`carson/m75-investment-review-remediation`

A software proof is valid only when its CI evidence names the **exact current review commit**. A green run from an ancestor cannot certify a newer tree.

---

## Proof levels

`src/dusty/trust_review.py` uses explicit proof states:

- `FAILED` — supplied evidence contradicts the claim or a required software proof failed;
- `UNPROVEN` — no acceptable proof exists yet;
- `SOFTWARE_PROVEN` — reserved for a deterministic implementation claim that has passed its software proof contract;
- `OPERATIONAL_EVIDENCE_REQUIRED` — software is capable of evaluating the claim, but the required live/native artifact does not exist yet;
- `OPERATIONALLY_PROVEN` — the required software and operational evidence both passed.

The trust report itself is fingerprinted. Software evidence is bound to commit and CI run identity. Native artifacts are hashed and bound to the inputs they are supposed to verify.

---

# The four capabilities

## Capability 1 — Data acquisition

### Claim

Dusty can acquire and normalize legitimate research data without silently inventing source provenance or treating inaccessible sources as available.

### Software proof

The software proof must demonstrate, at minimum:

1. read-only MT5 history acquisition;
2. chronological UTC normalization;
3. explicit source identity and provenance;
4. bounded acquisition and retry behavior;
5. fail-closed source-access policy;
6. point-in-time handling for scheduled and unscheduled event material;
7. authorized/public strategy-source handling without arbitrary executable-code import;
8. source-specific requirements such as identified SEC automated access.

### Operational proof

Operational data acquisition requires at least one successful, hashed, normalized live probe for every required class:

- `MARKET` — actual MT5 market-history data from the target environment;
- `MACRO` — an approved official/free macro source;
- `EVENT` — an approved official/free event/calendar source;
- `PUBLIC_STRATEGY` — an authorized/public strategy source.

A web request performed by a human or by an unrelated test harness is not automatically a Dusty live probe. The artifact must be produced by, or explicitly ingested into, the Dusty proof path with source identity and normalized-record count.

### Trust boundary

A source being reachable does not prove it is relevant. A source being relevant does not prove it adds value. Source value remains an empirical research question.

---

## Capability 2 — Market features and MT5 indicator equivalence

### Claim

Dusty can construct point-in-time market features and can determine whether its indicator values agree with native MT5 values for the same symbol, timeframe and terminal environment.

### Software proof

The software proof must demonstrate:

1. completed-bar availability semantics;
2. prefix invariance — adding future bars cannot change already-computed historical features;
3. deterministic SMA, EMA, ATR and RSI computation;
4. explicit feature timestamps;
5. source-bar timestamp preserved separately from decision availability time;
6. native-indicator CSV parsing;
7. parity drift detection;
8. environment binding to terminal build, symbol and timeframe.

### Native proof

Operational indicator proof requires a real `DustyIndicatorParity.mq5` Strategy Tester artifact produced by MetaTrader 5.

The proof must include:

- terminal build;
- exact broker symbol;
- exact timeframe;
- source bar-open timestamp;
- availability timestamp;
- native SMA;
- native EMA;
- native ATR;
- native RSI.

Dusty then compares the native rows against its own feature rows with a tolerance declared **before** reviewing the result.

A Python test that constructs an MT5-shaped CSV proves the comparator. It does not prove native MT5 equality.

---

## Capability 3 — Evidence to cognition

### Claim

Given the evidence Dusty is permitted to know at a reasoning instant, Dusty deterministically derives Analyst, Skeptic, Patience and Guardian states and produces an auditable decision without allowing forecasts, LLMs or external sources to manufacture execution authority.

### Software proof

This capability is itself a deterministic transformation, so its core proof can be completed in software.

The proof must demonstrate:

- strategy rules create the directional setup;
- forecasts may confirm or challenge but cannot create a setup;
- future-dated forecast evidence is rejected;
- coherence problems reach Skeptic;
- event/session/cooldown constraints reach Patience;
- risk and health can reduce or veto but cannot manufacture conviction;
- spread stress can reduce Guardian state;
- every role emits machine-readable reasons;
- identical inputs produce identical cognition and fingerprint;
- `Person.reason()` cannot mutate broker or position truth.

### Trust boundary

This proof means the transformation is trustworthy **given its inputs**. It does not certify that upstream market data, a forecast model or a news assertion is true. Those claims belong to the acquisition, feature and model-evaluation layers.

---

## Capability 4 — Real MT5 research laboratory

### Claim

Dusty can take a frozen strategy from point-in-time MT5 history through cognition, risk-aware research, Strategy Tester execution and trade-by-trade reconciliation without confusing intended trades with actual broker/tester outcomes.

### Software proof

The software chain must prove:

```text
MT5 history
    ↓
completed point-in-time bars
    ↓
features
    ↓
coherence
    ↓
Analyst / Skeptic / Patience / Guardian
    ↓
Person.reason()
    ↓
frozen StrategySpecV2 runtime
    ↓
minimum-lot research
    ↓
proper-risk growth sizing
    ↓
MT5 tester manifest
    ↓
MT5 deal export parser
    ↓
execution parity
    ↓
cash-economics parity
```

The tester comparison must verify more than price direction. It checks identity, side, volume, timing, entry price, initial stop, initial target, exit reason, exit timing and exit price.

For M75 operational trust it must additionally compare the native MT5 position cash effect:

```text
native net P&L = profit + commission + swap + fee
```

against an **ex-ante** Dusty expected net-P&L value for every certified trade.

The allowed net-P&L gap must be declared before the native result is inspected. Missing expected P&L is a certification failure. A result cannot be rescued by widening the tolerance after seeing the miss.

### Native proof

Operational laboratory proof requires all of the following from a real local MetaTrader 5 environment:

1. a live MT5 market-data probe;
2. a passing native indicator artifact;
3. a passing `DustyResearchEA.mq5` tester deal artifact;
4. execution parity;
5. cash-economics parity;
6. environment identity matching the intended terminal build, symbol and timeframe.

The GitHub-hosted Windows runner is not a broker terminal and cannot substitute for these observations.

---

# Execution-clock constitution

MT5 historical bar time is period-open time. Dusty therefore uses the following rules.

## Completed-bar availability

For a bar opened at `10:00` on M15:

```text
10:00 ---------------------- 10:15
      source bar evolves

10:15
      next bar exists
      ↓
      previous bar is now treated as completed
```

The completed bar's OHLC and indicators are available to Dusty at `10:15`, not retroactively at `10:00`.

The last raw bar in a bounded history slice is dropped unless a later bar proves its completion.

## Entry reference

A setup first known at `10:15` may not enter at the old bar's final close merely because that price exists in history.

For MT5-derived completed bars, Dusty's first historical execution reference is the following bar's open.

## Gap-through protection

If a protected long has a stop above the next observable market price, research does not award the stale stop price. The adverse gap is represented rather than pretending the position could exit at a price no longer available.

## Equity marks

Historical equity marks use a price observable at the mark timestamp. They do not use a completed bar's stale close after the decision clock has advanced.

---

# Spread constitution

Spread has three different meanings and they must never be conflated.

## 1. Historical source-bar spread

`FeatureBar.spread_points` belongs to the completed source bar. It is a historical market feature.

It is not the exact spread at the later decision instant.

## 2. Decision-time bar spread proxy

For MT5-derived completed bars, `decision_spread_proxy_points` comes from the following `MqlRates` row that establishes availability.

It is the best bar-level spread observation carried by that history representation at the decision clock, but it remains a **proxy**. It is not labeled as an exact executable Ask-Bid quote.

Guardian uses the decision-time proxy when available rather than the older source-bar spread.

## 3. Native executable cost

Actual Strategy Tester ticks, fills, commissions, swap and fees are the execution authority for final parity.

### Point versus tick size

`InstrumentEconomics` stores both:

- `point_size` — MT5 symbol point used to interpret point-denominated fields such as bar spread;
- `tick_size` — trade tick size used with broker tick-value economics.

Dusty does not assume they are identical.

### Research spread cost

The reference laboratory uses:

```text
spread_price_used = max(
    configured_spread_floor,
    decision_spread_proxy_points × broker_point_size
)
```

when the MT5 availability proxy and broker point size are known.

If point size is unavailable, Dusty does not invent a conversion. It falls back to the configured spread floor and records the basis as incomplete.

Every growth-sizing trace records:

- spread price used;
- spread basis.

The laboratory run records the set of spread-cost bases it used.

This bar-level model is deliberately conservative research evidence, not a replacement for native tester fills.

---

# Native evidence campaign

The following procedure is the required local proof campaign.

## 0. Freeze identity before testing

Record before the run:

- remediation Git commit SHA;
- working-tree cleanliness;
- MetaTrader terminal path;
- terminal build;
- broker/server;
- account mode;
- exact raw broker symbol;
- timeframe;
- StrategySpecV2 hash;
- feature configuration;
- test interval;
- tester fidelity;
- every numerical parity tolerance.

Do not change these after reviewing results and still call the evidence the same certification run.

## 1. Compile native probes

Compile in the actual local MetaEditor:

- `mt5/DustyIndicatorParity.mq5`;
- `mt5/DustyResearchEA.mq5`.

A Python syntax check or GitHub Actions run is not MQL5 compilation evidence.

## 2. Produce the market-data probe

Use Dusty's read-only MT5 worker against the intended terminal and symbol. Persist/hash the normalized result as the `MARKET` live probe.

## 3. Prove indicators

Run `DustyIndicatorParity.mq5` in Strategy Tester over the frozen interval.

Ingest its CSV with `qualify_native_indicators(...)` using the predeclared row minimum and absolute tolerance.

Any environment mismatch or indicator drift fails the native indicator proof.

## 4. Freeze an execution-eligible strategy

For the initial native proof choose a strategy whose semantics are fully represented by the current tester manifest.

Dynamic trailing and dynamic breakeven remain excluded from MT5 parity certification until an ordered protection-action manifest exists.

The strategy hash must not change during the run.

## 5. Produce Python expectations

Run the trusted Dusty laboratory with the same symbol, timeframe, history and broker economics.

For every trade preserve:

- trade id;
- strategy hash;
- side;
- approved volume;
- entry signal time;
- entry reference;
- exit window/reason;
- initial SL/TP;
- expected net P&L;
- spread cost basis.

Call `LaboratoryRun.growth_execution_envelopes()` before opening the native tester output. The
method binds the exact approved growth trades to the same identifiers, volumes and ex-ante net P&L
used by the manifest and laboratory ledger; it refuses unsupported manifest semantics or incomplete
growth traces.

## 6. Run the MT5 fidelity ladder

Use the same frozen strategy and interval through the configured fidelity ladder:

```text
Open Prices
    ↓
1 Minute OHLC
    ↓
Every Tick
    ↓
Real Ticks
```

Lower fidelity is screening evidence. The highest available appropriate fidelity is the final native execution arbiter.

## 7. Export native deals

`DustyResearchEA.mq5` exports the complete normalized deal evidence required for the reference single-position parity contract, including:

- strategy hash;
- position/deal identity;
- millisecond time;
- buy/sell and in/out semantics;
- volume;
- price;
- commission;
- swap;
- profit;
- fee;
- reason;
- SL/TP;
- Dusty trade comment;
- terminal build;
- symbol;
- timeframe.

## 8. Qualify native tester evidence

Call `qualify_native_tester(...)` with tolerances fixed before inspection, including `max_net_pnl_gap`.

Failure reasons are evidence. Do not remove inconvenient rows, change the strategy, alter the interval or widen a tolerance and describe the new run as the old run passing.

## 9. Produce remaining live data probes

Generate and fingerprint successful normalized probes for:

- official/free macro data;
- official/free event/calendar data;
- an authorized/public strategy source.

## 10. Build the trust report

Create `SoftwareProof` from the exact same commit and its successful CI run.

Then build `M75TrustReport` with:

- exact commit;
- exact software proof;
- four required live-source probes;
- native indicator proof;
- native tester proof.

Only a report whose capabilities reach their highest applicable proof level may set
`operationally_trusted=True`:

- data acquisition, market features and the MT5 laboratory must be `OPERATIONALLY_PROVEN`;
- evidence-to-cognition must be `SOFTWARE_PROVEN`, because its defined claim is exhausted by a
  deterministic software transformation and has no separate external observation to relabel as
  operational evidence.

---

# Failure policy

A failed native proof is not a failed project. It identifies which layer is wrong.

Examples:

- `native_symbol_mismatch` → wrong environment identity;
- indicator parity error → feature semantics or native variant disagreement;
- entry/exit gap → execution-clock or tester-behavior disagreement;
- SL/TP mismatch → strategy/runtime/manifest disagreement;
- `net_pnl_gap` → spread, commission, swap, fee, tick economics or fill-model disagreement;
- missing live probe → acquisition capability not operationally demonstrated;
- commit mismatch → stale software evidence.

The remediation rule is:

```text
observe failure
    ↓
identify layer
    ↓
return to last known-good proof
    ↓
repair the smallest responsible boundary
    ↓
rerun all affected proof gates
```

No proof is rescued by deleting adverse observations or weakening the rule after the result is known.

---

# Ten-repository design genetics used by this review

Dusty uses ideas, not dependency trees.

1. **Kronos** — financial K-line specialist and a reminder that a model demo/backtest is not a production trading system.
2. **Chronos-2** — probabilistic and covariate-informed forecasts; model output remains evidence rather than execution authority.
3. **Uni2TS/Moirai** — rolling evaluation, explicit train/test separation and probabilistic scoring.
4. **Vibe-Trading** — exact-symbol identity, units, warm-up/evaluation separation, requested-versus-executed records, fail-closed broker recovery and data-source provenance.
5. **Microsoft Qlib** — point-in-time data and loose separation of data, model, strategy, portfolio and execution layers.
6. **Microsoft RD-Agent** — repeated hypothesis → implementation → benchmark → feedback research loops.
7. **TradingAgents** — point-in-time fixes, durable checkpoint resume and ticker-scoped learning; LLM debate is not broker authority in Dusty.
8. **ai-hedge-fund** — persistent fund/mandate identity separate from the ticker universe.
9. **Automaton** — durable state, heartbeat/resource management and immutable protected laws only; self-funding, replication and sovereign runtime self-modification are rejected.
10. **Microsoft Agent Framework** — durability, restartability, observability, governance, checkpointing and workflow state patterns without requiring its runtime.

---

# Investment-review verdict rule

The correct answer is deliberately two-part.

### What software can prove now

Dusty can prove that the four capability **evaluation mechanisms** are deterministic, fail-closed and capable of rejecting contradictory evidence.

The evidence-to-cognition transformation itself can be fully software-proven because the transformation is the claim.

### What software cannot fabricate

Dusty cannot honestly prove a real terminal build produced matching MT5 indicators, fills and cash economics until the real terminal produces those artifacts.

Likewise, it cannot claim a source is operationally reachable through Dusty's runtime until the live source probe exists.

Therefore the acceptable pre-native verdict is:

> **M0–M75 is software-trustworthy within the tested contracts, and Dusty now knows exactly which operational facts it is not yet entitled to claim.**

The acceptable post-native verdict is stronger:

> **M0–M75 is operationally trusted for these four capabilities only when the exact-commit
> M75TrustReport reaches the maximum applicable proof level for every capability: native/live evidence
> for the three external capabilities and software proof for deterministic evidence-to-cognition.**

M76 should not use missing native evidence as though it were already certified.
