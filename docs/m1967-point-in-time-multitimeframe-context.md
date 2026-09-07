# M196.7 — Point-in-Time Multi-Timeframe Context Binding

M196.7 turns the bounded timeframe profiles introduced by M196.6 into executable **research evidence** without changing trading authority.

## Contract

A strategy has one primary decision timeframe and zero to two context timeframes. At each primary decision timestamp:

- the primary feature vector must exist at that exact timestamp;
- each context timeframe contributes only its latest completed feature vector whose availability timestamp is less than or equal to the primary decision timestamp;
- a primary vector is never silently backfilled;
- missing context fails closed for a single binding and is skipped only for the naturally incomplete prefix of a historical campaign;
- future context is impossible by construction and is rejected again by `AnalysisSnapshot` known-time validation;
- all analytical keys are namespaced by timeframe (`d1.close`, `h1.sma`, `m15.rsi`, etc.);
- internal feature keys such as the primary execution reference are excluded from the analytical snapshot, so a higher timeframe cannot inject an alternate fill price;
- M1 may be context but remains outside the M196.6 primary-decision ladder;
- appending future context data cannot rewrite an already-bound historical snapshot or its content fingerprint.

## Existing contracts reused

M196.7 deliberately reuses rather than replaces:

1. `completed_feature_bars_from_mt5()` — MT5 bars are stamped at the later bar-open instant that proves the source bar is complete. The final raw bar is dropped when no later bar proves completion.
2. `compute_standard_features()` — feature rows use only completed observations available at or before their timestamp.
3. `AnalysisSnapshot` — any supplied evidence-known timestamp later than the decision clock is rejected.
4. `MarketAnalysisGraph` — existing deterministic analysis graphs consume the new namespaced inputs without a new agent framework or second strategy runtime.
5. `TimeframeProfile` — M196.6 remains the sole owner of the bounded primary/context selection policy.

## Human-style example

A profile may bind:

`D1 regime → H1 structure → M15 decision`

At an M15 decision at 12:15 UTC, the D1/H1 values are the most recent **completed** D1/H1 observations that were actually knowable by 12:15. An H1 bar that opened at 12:00 but does not close until 13:00 cannot contribute its final OHLC at 12:15.

## Authority boundary

`BoundTimeframeContext` has no broker-write, live-write, promotion, risk-override, or Guardian-override authority. M196.7 is an analytical/PIT alignment layer only.

## Reference-architecture lessons

The implementation follows the established Dusty reference set without importing those projects as runtime dependencies:

- Qlib: rolling/as-of datasets and clear data/feature/model separation;
- RD-Agent: explicit research/evaluation boundaries and reproducible workspaces;
- Kronos, Chronos-2 and Moirai: multivariate/context evidence belongs in typed model inputs, not hidden global state;
- Vibe-Trading: inspectable research stages and explicit execution boundaries;
- TradingAgents: state passed explicitly between reasoning stages;
- AI Hedge Fund: data/analysis/risk/execution remain separate layers;
- Automaton and Microsoft Agent Framework: durable state/checkpoint ideas rather than implicit mutable process memory.

M196.7 does not add another scheduler, agent framework, database, MT5 worker, or execution adapter.
