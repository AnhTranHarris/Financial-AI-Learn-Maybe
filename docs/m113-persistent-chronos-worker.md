# M113 — Persistent Isolated Chronos-2 Worker

M113 changes the Chronos-2 contractor from a one-request cold subprocess into a bounded persistent research worker. It is an infrastructure and performance milestone only. It does not claim forecast skill, profitability, or trading authority.

## Why M113 exists

Local Windows M112 validation established:

- Chronos/PyTorch import time: about 86.6 seconds;
- pinned model load time: about 8.2 seconds;
- one synthetic 16-step forecast: about 40.7 seconds;
- available logical CPUs: 4.

A 180-second cold-start call timed out once, while the same exact isolated adapter succeeded with a 300-second allowance. The expensive part was repeated interpreter/import/model startup, not evidence normalization.

M113 therefore pays startup cost once and reuses the same isolated provider process for subsequent bounded requests.

## Lifecycle

The provider process has explicit runtime states:

- `stopped`
- `starting`
- `ready`
- `busy`
- `resource_blocked`
- `failed`

`resource_blocked` is reserved for bounded startup/request failures whose captured provider stderr contains a recognized memory-allocation condition. It is not a forecast-quality state.

M113 does not automatically retry or restart a failed model. Recovery is explicit through `restart()` or stop/start. Provider failure never restarts, halts, or mutates Dusty's deterministic lane.

## Process ownership

Dusty starts one child process by absolute path:

`<provider-root>\Chronos2\.venv\Scripts\python.exe <worker> --persistent`

The lifecycle manager owns only the exact `Popen` object it created. Shutdown and forced termination act only on that owned child. M113 does not enumerate, scan, or blanket-kill other Python processes.

## Isolation

The child environment preserves the M112 allow-list and continues to enforce:

- Hugging Face offline mode;
- Transformers offline mode;
- CPU-only execution;
- blank `CUDA_VISIBLE_DEVICES`;
- no forwarded OpenAI, broker, MT5, Hugging Face token, or unrelated credential variables.

The provider still receives only the exact M112 point-in-time request schema: completed timestamps, completed close prices, symbol/timeframe labels, horizon, quantiles, and identity/hash metadata.

No account, balance, equity, margin, position, order, spread, commission, credential, Guardian state, or execution interface crosses the contractor boundary.

## Persistent protocol

On startup the worker imports Chronos/Torch, loads the exact pinned model revision once, then emits exactly one JSON-line `ready` event containing provider/model/runtime identity.

Each later request is one canonical JSON line. Each successful response is one canonical JSON line using the M112 forecast-response contract.

Any unexpected startup event, request-schema violation, response identity mismatch, malformed JSON, provider exit, broken pipe, or bounded timeout fails closed.

The persistent worker does not accept a privileged shutdown message. Clean shutdown closes its stdin. EOF causes the provider worker to exit. If a busy child must be halted, Dusty terminates only that owned process.

## Timeouts

M113 uses separate budgets:

- startup timeout: 300 seconds;
- per-request timeout: 180 seconds;
- shutdown timeout: 15 seconds.

The 300-second startup budget is based on the observed Windows cold-start behavior and is not a permission for unbounded model execution. Per-request inference remains separately bounded.

## Evidence authority

Every successful persistent response is normalized into the same immutable `ForecastEvidence` type used by M112.

It remains permanently constructed with:

- `broker_write_authority = false`
- `entry_veto_authority = false`
- `promotion_authority = false`

Persistence changes process lifetime only. It grants no additional cognitive, execution, risk, or promotion authority.

## Local validation

M113 includes `tools/validate_m113.ps1`.

The script:

1. verifies the current branch and clean repository;
2. fetches the current M113 branch and verifies local HEAD equals the remote branch head;
3. runs the persistent-process unit gate;
4. runs the persistent-Chronos unit gate;
5. reruns the M112 adapter regression gate;
6. runs the full Dusty unittest suite;
7. starts one real isolated Chronos process;
8. requests two synthetic forecasts through that same process;
9. verifies both forecasts are available;
10. verifies all authority flags remain false;
11. verifies clean shutdown;
12. writes a compact JSON report and full transcript under `%LOCALAPPDATA%\DustyDragon\validation`.

The validation script does not write generated evidence into the Git repository.

## What M113 does not establish

M113 does not establish:

- Chronos predictive skill on EURUSD or any other market;
- profitability;
- calibration quality;
- directional edge;
- entry-veto usefulness;
- actual MT5 historical-data integration;
- broker-native execution fidelity;
- fee/slippage correctness;
- automatic provider selection;
- forecast ensembling;
- provider ranking;
- Kronos-small integration;
- TimesFM 2.5 integration;
- Demo or Live trading authorization.

The milestone objective is only to prove that one isolated Chronos process can be started once, remain identity-bound, answer multiple bounded point-in-time forecast requests, and shut down without gaining operational authority.
