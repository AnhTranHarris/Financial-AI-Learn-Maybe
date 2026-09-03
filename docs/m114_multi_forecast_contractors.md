# M114 — Multi-provider forecast contractors and UI selection

M114 extends the M113 persistent isolated-worker boundary to the three installed forecast contractors:

- Amazon Chronos-2
- Kronos-small
- TimesFM 2.5

## Operator selection

The M114 desktop exposes four explicit choices:

1. Amazon Chronos-2
2. Kronos-small
3. TimesFM 2.5
4. Use all three — independent evidence

Selecting a choice does not start a model. Startup, synthetic testing and shutdown require separate operator actions.

`Use all three` is not an ensemble. Dusty preserves one independent evidence result per provider. M114 defines no averaging, voting, ranking, confidence weighting, winner selection or trade policy from provider disagreement.

## Process and resource boundary

Each model runs under the Python interpreter in its own external provider directory. Dusty's core environment does not import provider ML packages. Persistent workers are started sequentially and forecast requests are issued sequentially so the four-logical-CPU reference Windows machine is not intentionally saturated by three simultaneous inference jobs.

A failed or resource-blocked provider remains unavailable. It cannot make the deterministic Dusty lane fail and does not gain an automatic restart loop.

All workers remain CPU-only and Hugging Face offline/local-cache-only where applicable.

## Provenance

Provider/model identities are pinned rather than following mutable `main` revisions.

Chronos-2 returns native probabilistic quantiles.

TimesFM 2.5 returns its native quantile forecast channels. Dusty extracts q0.1, q0.5 and q0.9 at the requested terminal horizon.

Kronos-small is not represented as a native quantile model. Its official predictor is sampling-based and averages its internal `sample_count`; therefore M114 issues five deterministic single-path samples using request-derived seeds and labels the resulting terminal-close distribution `empirical_5_seed_paths`. Dusty derives empirical p10/p50/p90 from those five paths and retains the method/sample-count provenance separately from the common immutable `ForecastEvidence` record.

## Kronos point-in-time input

Kronos receives only completed historical OHLC, historical tick volume and explicit future timestamps. The future timestamps are calendar coordinates, not future market observations. No future price, spread, execution quote, account field or outcome is supplied.

Chronos-2 and TimesFM 2.5 receive completed timestamps and closes only.

## Authority boundary

Every M114 forecast remains research evidence only:

- broker write authority: false
- entry veto authority: false
- promotion authority: false

Contractors receive no MT5 credentials or order surface. Provider output cannot manufacture a strategy setup or bypass Guardian/risk gates.

## UI scope

The M114 UI can discover, select, start, test and stop the forecast contractors. The synthetic `Test selected` action uses generated EURUSD-like M15 bars and does not connect to MT5.

M114 does **not** silently replace the existing historical 30-case forecast campaign with these external models. Wiring selected external providers into a controlled point-in-time historical tournament is a later milestone and requires its own evidence contract and computational budget.

## Local certification

`tools/validate_m114.ps1` verifies the branch, runs provider gates and the complete Dusty regression suite, then starts all three providers sequentially and runs two synthetic forecast rounds through the same persistent PIDs before clean shutdown. Logs and a compact report are written under `%LOCALAPPDATA%\DustyDragon\validation`.
