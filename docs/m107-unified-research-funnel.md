# M107 — Unified Research Funnel

M107 connects the M106 acceleration primitives into one bounded, checkpointed, cheap-to-expensive research path. It is intentionally isolated on `carson/m107-unified-research-funnel` and begins from the certified M106 head `2f90bba6be01663b96cae304b2107d423ba21a33`.

M107 does **not** replace the certified desktop research route yet. The new local adapter is opt-in and exists so the new engine can be proven independently before any UI/coordinator routing change.

## Research flow

`dusty.research_funnel.UnifiedResearchFunnel` runs six content-addressed stages through the existing M106 `ResearchCycle`:

1. `acquisition` — one frozen M15 history/economics acquisition;
2. `features` — completed point-in-time feature bars;
3. `challengers` — frozen parent plus deterministic one-factor V2 challengers;
4. `cheapscreen` — Python configured-cost and stressed-cost evaluation;
5. `diagnostics` — matched-exposure cost decomposition;
6. `fidelityqueue` — proposal-only first MT5 fidelity step for survivors.

The internal stage names are deliberately alphanumeric because the M106 checkpoint contract requires simple path-safe stage identities. Human-facing reports keep readable fields such as `cheap_screen` and `fidelity_queue`.

## Checkpoint and resume behavior

The entire funnel identity includes the caller request, exact code identity supplied by the bound runtime, parent package fingerprint, challenger-plan fingerprint, fixed evaluation plan and funnel policy. A semantic change therefore yields a different cycle fingerprint rather than reusing stale evidence.

Acquisition and every later stage are hash-verified checkpoints. An identical rerun can reuse the complete cycle and does not call the history reader again. The decoder accepts both fresh in-process `datetime` values and ISO timestamps loaded from JSON checkpoints so a resumed run has the same typed bar/economics semantics as a fresh run.

Corrupt or mismatched checkpoints continue to fail closed through M106 `ResearchCycle`; M107 does not weaken that invariant.

## Frozen first-generation challenger neighborhood

`first_generation_challenger_plan()` creates a small code-reviewed neighborhood before any outcomes are observed. The current one-factor candidates cover:

- one directional RSI entry-threshold change;
- 180-minute and 300-minute exit horizons;
- cooldown changes to 0 and 8 steps;
- RSI periods 10 and 21;
- forecast-neutral threshold `0.0002`.

The parent is always retained. Challenger generation remains deterministic, one-factor-at-a-time, budget bounded and `promotion_eligible=False`. M107 never performs a Cartesian parameter search and never uses performance to invent the candidate set.

## Cheap Python screen

Every parent/challenger candidate is evaluated on the same frozen `FixedEvaluationPlan` under:

- configured cost assumptions; and
- a prespecified additional round-trip slippage stress, defaulting to 10 broker points.

Both development and holdout segments are retained. The screen can require a minimum closed-trade count, positive simulated net P&L, maximum marked drawdown, development passage and stress passage. These requirements are part of the cycle identity.

The screen is a rejection mechanism, not a winner selector. It reports all cases and their failure reasons. `ranking_performed` and `promotion_eligible` remain false.

## Matched-exposure diagnostics

For each candidate and development/holdout segment, M107 freezes the configured-cost approved lots and decomposes the stressed result into:

1. original simulated net P&L;
2. pure additional-cost effect at the original exposure;
3. stressed net P&L at that same exposure;
4. residual `exposure_or_sequence_effect` from re-sizing/rejections/occupancy/sequence changes;
5. actual stressed net P&L and total change.

This carries the M106 audit protection into the unified funnel: higher assumed costs cannot receive false credit for an apparent improvement merely because they forced lower exposure.

Forecast-veto attribution is explicitly marked `NOT_RUN_NO_FORECAST_PROVIDER_IN_FUNNEL_V1`. M107 therefore cannot claim forecast selection skill until an actual point-in-time forecast provider is wired into the funnel and the existing veto diagnostic is run against frozen baseline entries.

## Cheap-to-expensive MT5 fidelity boundary

Only candidates that satisfy every prespecified Python gate may reach `fidelityqueue`.

The queue does **not** launch MetaTrader. It only emits proposals for the existing first fidelity level, `OPEN_PRICES`, with:

- `broker_write_authorized=False`;
- `requires_reconciliation_before_advance=True`;
- `promotion_eligible=False`.

The existing `dusty.fidelity` ladder remains authoritative for any later advancement: Open Prices → 1-minute OHLC → Every Tick → Real Ticks, one level at a time after reconciliation.

If more candidates survive than the frozen native-test budget allows, M107 returns `BUDGET_BLOCKED_TOO_MANY_SURVIVORS` and creates **no** proposals. It does not silently rank, truncate or choose a preferred survivor.

## Local MT5 adapter

`dusty.local_research_funnel.run_local_research_funnel()` reuses the certified local research contracts rather than duplicating terminal validation or history acquisition.

It reuses:

- `validate_research_selection()`;
- `SelectedTerminalHistoryReader`;
- the selected terminal/account/symbol binding;
- reviewed strategy package resolution;
- the existing fixed holdout plan and user-supplied cost assumptions.

The adapter requires a fixed holdout and a cost-source note. The cost-source text is represented in the experiment identity by SHA-256 rather than copied into the compact report.

Legacy `settings.comparison=True` is rejected because the unified funnel is an alternative heavy matrix; running both would duplicate expensive simulations without adding independent evidence.

The adapter explicitly records that:

- legacy desktop routing is unchanged;
- broker writes are not authorized;
- the native Strategy Tester is not launched.

## Deliberate boundaries

M107 does **not**:

- change the certified desktop/UI coordinator route;
- place, modify or close broker orders;
- launch an MT5 Strategy Tester campaign;
- rank surviving candidates;
- declare a winner;
- promote a strategy to Demo or Live;
- change constitutional risk limits;
- optimize mutation values from outcomes;
- claim forecast-veto skill without a forecast provider;
- infer broker sessions from missing bars;
- self-modify Dusty application code.

These are intentional separations of authority, not missing shortcuts.

## QC

Dedicated M107 tests verify:

- first-run versus full-cache-hit behavior;
- exactly one acquisition across identical local/funnel reruns;
- checkpoint decoding back into the original typed bars/economics;
- parent plus bounded challenger matrix construction;
- configured and stressed development/holdout evaluation for every candidate;
- matched-exposure decomposition reconciliation;
- fail-closed native candidate budget handling without ranking;
- compact reports that exclude raw bar payloads and never select a winner;
- cycle identity changes when screening policy changes;
- fixed-holdout and cost-provenance requirements in the local adapter;
- refusal to duplicate the legacy comparison matrix;
- unchanged desktop/broker/native-tester authority boundaries.

The pre-certification functional head `578faad11b8a81ccf084d3e5b96dce3c9b1f1090` passed GitHub Actions run #395 on Ubuntu and Windows with Python 3.11 and 3.12, including the full unittest suite. The branch's final certification is the successful CI run attached to the exact commit containing this document and the named `Unified research funnel gate`.

M106 and M105 remain untouched on their prior branches.
