# M156 — Feature Intelligence Registry

## Scope

M156 gives Dusty a versioned catalog of research features before M157 begins compiling strategy genomes. The registry is metadata and identity infrastructure; it does not grant trading authority, create entries, or certify profitability.

The governing question is not merely "what is RSI?" It is:

> What exactly is this feature, what does it depend on, when is it knowable, how much history does it require, can it repaint, where is it applicable, what can mutate it safely, and what evidence must change if any dependency changes?

## Research basis

The design incorporates patterns from several independent sources:

1. Dusty's existing `FeatureBar` already separates MT5 source-bar open time from the later timestamp when the completed OHLC becomes knowable. M156 preserves that conservative point-in-time contract.
2. Microsoft Qlib groups feature generation into explicit families/configuration (for example K-bar, price, volume, and rolling features) instead of treating every produced column as an anonymous value.
3. Vibe-Trading now explicitly separates warm-up bars from the evaluation window so indicator initialization history cannot silently contaminate reported performance.
4. MetaQuotes indicator APIs are handle-based. Indicator values may not be ready immediately after handle construction; `BarsCalculated()` and `CopyBuffer()` semantics therefore matter for native/custom-indicator readiness. `CopyBuffer(..., start_pos=0, ...)` refers to the current bar, so decision-time use must distinguish current-bar values from completed-bar values.
5. Feast's feature-store architecture demonstrates why a central registry, dependency DAG, versioned definitions, and point-in-time retrieval matter. Its recent as-of-known-time work also reinforces that event time alone is insufficient when values can be backfilled or revised later.
6. Community failure reports repeatedly show that subtle feature leakage can manufacture excellent-looking backtests. M156 therefore makes lookahead, repaint behavior, and unknown availability explicit fail-closed metadata rather than undocumented assumptions.

External projects remain references, not mandatory Dusty runtime dependencies.

## Definition identity

Each `FeatureDefinition` records:

- name and semantic version
- feature family
- source class
- availability policy
- lookahead policy
- repaint policy
- warm-up observations
- exact versioned dependencies
- market applicability
- compatible mutation families
- known limitations
- provenance references
- relative compute cost
- whether later native MT5 parity proof is required

The feature's direct fingerprint hashes that semantic definition.

The registry also computes a **resolved fingerprint** that recursively includes the resolved fingerprints of every dependency. A downstream feature therefore changes identity when any upstream semantic dependency changes, even if the downstream label itself does not.

M155 `ExperimentManifest.FeatureRef` can be created directly from this resolved identity.

## Dependency discipline

Dependencies are exact `name@version` references.

The registry rejects:

- missing dependencies
- dependency cycles
- duplicate feature definitions
- silent mutation after the registry is frozen

This prepares M157 to compile only strategies whose feature graph is fully known.

## Decision eligibility

A feature is eligible for point-in-time decision research only when its entire dependency closure is:

- `lookahead = none`
- `repaint = stable`
- availability is known

A future label, unknown opaque indicator, or repainting dependency contaminates every feature derived from it.

This is intentionally stricter than "the value exists in a dataframe."

Native MT5 parity is a separate later certification question. A Python-derived ATR can be point-in-time legal yet still require native terminal parity before operational certification.

## Canonical standard features

`standard_feature_registry()` currently catalogs the canonical period-specific outputs produced by `compute_standard_features`:

- open
- high
- low
- close
- spread_points
- tick_volume
- return_1
- `sma_<period>`
- `ema_<period>`
- `atr_<period>`
- `rsi_<period>`

The old convenience aliases `sma`, `ema`, `atr`, and `rsi` remain available in the legacy feature vectors but are deliberately not canonical M156 identities. An experiment must bind the period-specific feature it actually used.

## Important limitations encoded now

`spread_points` is historical source-bar spread information; it is not represented as an executable quote.

`tick_volume` is broker tick activity and must not be described as centralized exchange volume.

ATR/EMA/RSI definitions can be legal research features while still requiring terminal-native parity evidence before later operational certification.

Opaque custom indicators with unknown repaint or availability behavior remain non-decision-eligible until empirically characterized.

## M156 certification gates

M156 is accepted only if tests prove:

- canonical standard registry construction
- deterministic identity independent of registration order
- dependency-aware fingerprints
- missing-dependency rejection
- cycle rejection
- fail-closed lookahead/repaint/unknown handling
- immutable-after-freeze definitions
- market applicability checks
- warm-up propagation
- native-parity requirement propagation
- M155 manifest-reference binding

The dedicated gate must pass on Python 3.11 and 3.12 on both Ubuntu and Windows, followed by the full repository CI on the exact head.

## Next milestone

M157 will use this registry as the ingredient contract for Strategy Genome Compiler v2. Strategy clauses must reference exact feature identities instead of free-form indicator names, and unresolved or non-decision-eligible feature dependencies must fail compilation.
