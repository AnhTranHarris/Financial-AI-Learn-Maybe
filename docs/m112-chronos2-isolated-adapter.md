# M112 — Isolated Chronos-2 Forecast Adapter

M112 introduces the first executable external forecast-contractor boundary for Dusty Dragon. It is deliberately a research-only infrastructure milestone, not a forecast-quality claim and not a trading authorization.

## Scope

M112 supports one provider only:

- provider: `chronos2`
- model: `amazon/chronos-2`
- Hugging Face revision: `29ec3766d36d6f73f0696f85560a422f50e8498c`
- provider runtime: `chronos-forecasting==2.3.1`
- license metadata: `Apache-2.0`

The provider is executed by its own isolated Python interpreter discovered under the external provider root. Dusty's core environment does not import `torch`, `transformers`, or `chronos` to use this boundary.

## Point-in-time input contract

The M112 Chronos request contains only:

- protocol and provider/model identity;
- symbol and timeframe labels;
- the availability timestamp of the latest completed observation;
- a forecast horizon in observations;
- requested quantiles `0.1`, `0.5`, `0.9`;
- a SHA-256 digest of the context;
- chronological context rows containing exactly `at` and `close`.

The context must contain 32–2048 completed observations and must be strictly chronological. The latest context timestamp must equal `as_of`. The supported horizon is 1–64 observations.

M112 intentionally does **not** pass any of the following to Chronos:

- MT5 terminal or session objects;
- account number, balance, equity, margin, or credentials;
- broker credentials or API keys;
- orders, positions, or trade history;
- execution prices or order intents;
- spread or commission data;
- strategy promotion state;
- Guardian state;
- future observations or realized outcomes.

The external worker accepts an exact request-field set. Any missing or unexpected field fails closed before model inference.

## Isolation and runtime rules

The provider subprocess is started by absolute path to the provider's isolated `python.exe`. Its environment is rebuilt from a small allow-list rather than inheriting Dusty's entire environment. Credential-like environment variables are not forwarded.

The worker is run with:

- CPU-only model placement;
- `CUDA_VISIBLE_DEVICES` blank;
- Hugging Face offline mode enabled;
- Transformers offline mode enabled;
- `local_files_only=True` when loading the pinned model revision;
- a default hard timeout of 180 seconds.

M112 runtime inference therefore does not silently download or update model files. If the exact pinned revision is absent from the local Hugging Face cache, the provider is unavailable until that revision is explicitly populated outside the runtime path.

## Output contract

A successful call returns immutable `ForecastEvidence` containing:

- provider/model/revision/runtime/license identity;
- symbol, timeframe, `as_of`, origin timestamp, and horizon;
- origin close;
- final-horizon `p10`, `p50`, and `p90` close-price forecasts;
- context SHA-256;
- request SHA-256;
- response SHA-256;
- an evidence fingerprint.

The output target is explicitly `completed_close_after_horizon_observations`. A horizon is an observation count, not guaranteed elapsed wall-clock time; market closures can make the elapsed duration longer.

M112 does not manufacture a directional probability from the quantiles. It exposes the median predicted return as a convenience property only.

## Authority

All M112 evidence is permanently constructed with:

- `broker_write_authority = false`
- `promotion_authority = false`
- `entry_veto_authority = false`

Chronos cannot send an order, size a position, unlock Demo/Live, promote a strategy, or veto an entry through M112.

Provider launch errors, timeouts, non-zero exit codes, malformed JSON, identity mismatches, crossed/non-positive quantiles, or other provider faults are converted into a provider result with status `unavailable`. They do not disable the deterministic Dusty lane.

## Synthetic smoke test

M112 exposes one command-line hardware test:

```powershell
.\.venv\Scripts\python.exe -m dusty.provider_forecast_adapter `
  --provider-root "C:\Users\lord1\DustyProviders" `
  --smoke-test
```

The smoke test generates synthetic completed M15 bars inside Dusty's process, then launches the external Chronos interpreter and requests one 16-observation forecast. It does not connect to MT5 and does not read a broker account.

A successful result has `"status":"available"` and an evidence object containing the three quantiles and hashes. An unavailable result exits non-zero and contains a bounded error description.

## What M112 does not establish

M112 does not establish:

- Chronos forecast accuracy on Dusty's markets;
- profitability;
- entry-veto usefulness;
- broker-native execution fidelity;
- fee/slippage correctness;
- an independent model-file checksum beyond the pinned repository revision;
- actual MT5 historical-data integration;
- OHLC, multivariate, or covariate-informed Chronos input;
- automatic model startup;
- fallback ranking or ensemble logic;
- any Demo or Live authorization.

The intentionally narrow M112 objective is only to prove that Dusty can invoke a pinned local forecasting contractor through a deterministic, hashed, fail-closed subprocess contract without granting that contractor operational authority.
