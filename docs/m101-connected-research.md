# M101 — Connect the desktop to honest, bounded research

## Investment-review verdict

The M100 Windows installation report (`52b29a94c18fe61af5665d09bffc7e94d183316f`,
326 tests reported passing by the user) established installation, a functioning UI, and a read-only
MT5 account/symbol connection. It did **not** establish an autonomous trader. Inspection confirmed
that the default catalog was empty and the runtime port unconfigured. Start could not run research.

M101 corrects that missing vertical path. It is one integration milestone, not a claim to have
completed ten new milestones or re-certified every earlier capability operationally.

| Claim | Evidence / limit |
|---|---|
| UI selection reaches executable rules | Two built-in, code-reviewed RSI momentum hypotheses; exact metadata and package validation |
| MT5 history reaches deterministic cognition and simulation | Real read-only adapter, existing feature/cognition/V2/laboratory engines; fixture end-to-end regression tests |
| Point and tick units remain distinct | Removed a residual `trade_tick_size or point` fallback in the older adapter; regression tests reject missing tick size |
| Results are inspectable | Frozen request, raw bars, full laboratory traces, economics, config and hashes; explicit terminal/account/source-code identity |
| Desktop remains responsive | Spawned research process; main-thread Tk event queue for connection/Codex work; bounded research timeout |
| Profitable strategy / forecasting accuracy | NOT PROVEN; seed hypotheses are infrastructure baselines, not trained forecasts or online discoveries |
| Native MT5 indicators / Strategy Tester parity | NOT PROVEN by this workflow; existing native proof campaign still required |
| Demo / Live readiness | NOT PROVEN; no desktop broker write path or promotion proof produced |

## What changed

`reviewed_strategies.py` owns immutable executable packages. The long seed requires RSI 55–70 and a
positive one-bar return; the short seed requires RSI 30–45 and a negative return. Both use M15,
ATR(14) × 2 initial stop, 2R target, 16-step maximum hold, four-step cooldown, no scaling, no trailing
or breakeven mutation. The package fingerprint binds feature periods and cognition policy as well
as the V2 strategy hash. These are newly authored research hypotheses, not recommended investments.

`--catalog` remains metadata-only. A downloaded strategy cannot become executable by claiming a
hash, a certification stage, or a familiar title. Only an exact known package resolves. Universal
catalog visibility means permission to investigate a symbol, not suitability for deployment.

`local_research.py` owns the read-only adapter, run lifecycle and artifacts. The adapter:

- initializes the exact selected terminal with portable mode and a ten-second initialization limit;
- verifies executable directory, data directory, build, full-account identity fingerprint, account
  mode/currency and exact broker symbol specification before and after acquisition;
- requests one UTC M15 history interval, default seven days, maximum thirty days / 3,000 rows;
- requires at least fifty bars and rejects malformed prices, negative spread/volume, duplicate,
  out-of-order, misaligned and out-of-window timestamps, including a malformed final confirmation bar;
- drops the last unconfirmed bar through the existing completed-bar engine; never synthesizes gaps;
- preserves source-bar spread and the following-bar availability proxy separately;
- obtains a native **read-only** current margin estimate; labels this and present symbol economics
  as historical proxies, not historically observed economics;
- currently rejects custom symbols, profit/account currency mismatch and non-linear or converted
  tick economics. It does not assume all MT5 instruments fit the existing linear cash ledger.

Historical spread price remains `max(user_floor, decision_proxy × point_size)`. Sizing/P&L still use
trade tick size and value independently. Commission and total round-trip slippage are explicit user
assumptions, default zero and **unverified**; zero is not a verified claim that trading is free.
Swaps, fees, historical specification changes and exact intrabar tick paths are not fully modeled.
Changing assumptions creates a new run; it does not revise an old result.

The existing laboratory runs constant broker-minimum-lot research with hypothetical 100,000 starting
equity and a separate 0.25%-risk growth simulation initialized from the connection's balance snapshot.
These are two simulations over the **same** history, not independent validation. Snapshot balance
is not a current portfolio allocation. A low balance or risk veto may legitimately yield no growth
trades. Guardian's existing 50-point normal spread ceiling is preserved in the package; it is not
silently relaxed to generate more trades. Indices with wider point spreads may produce no entries.

## Lifecycle and storage

Each Start creates a new random run directory below `%LOCALAPPDATA%\DustyDragon\research`, outside
Git. It contains:

| File | Meaning |
|---|---|
| `request.json` | Ex-ante settings, exact code/strategy/package identity, time window and masked/hashed environment identity |
| `bars.json` | Actual adapter-returned raw rows, including the final confirmation-only row |
| `report.json` | Full feature/cognition/sizing traces, minimum-lot and growth ledgers, economics/config and proposed native manifests |
| `result.json` | Last-published completion/failure record; a successful run binds request/data/report hashes |

Files are written to unique temporary names, flushed and atomically replaced on the same filesystem.
Incomplete files and orphaned requests are not successes. No automatic resume is attempted; a new
run retains old evidence. Hashes detect accidental edits, not a malicious same-user rewrite of the
entire artifact set. The directory contains private account-related research; review it before sharing.

The spawned worker owns its own MT5 bridge. Stop Entries or Emergency Halt cancels **that research
worker only**, not the MT5 terminal or broker positions. Cancellation and the 180-second deadline
cannot unlock trading. Start remains latched off after an operator halt until Dusty restarts.
Selection, duplicate Start and Codex are blocked while work is active. Codex development locks
research and requires restart even if the developer process fails: it might have edited files first.
Dirty or changed Git code refuses an exact-commit research run. The installed module must belong to
the selected repository. Other programs and manual MT5 activity are not controlled by this UI.

Connection/discovery and Codex run off the Tk thread and deliver results through a queue. Research
uses a spawn process so a stalled native history call can be abandoned without blocking Tk or
terminating MT5. The application has no privileged OS sandbox; isolation here is lifecycle and
connection ownership, not a guarantee against malicious local code.

## Ten-repository source comparison

Reviewed on 2026-09-02. These are pinned **focused source-level comparisons** of relevant paths,
not a line-by-line security audit of all ten repositories. No reference runtime or generated trading
code was imported. The source comparisons informed boundaries, not profitability claims.

| Repository / exact revision / inspected path | Design consequence for Dusty |
|---|---|
| [Kronos](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/run_backtest_kronos.py) `67b630e67f6a18c9e9be918d9b4337c960db1e9a` | Its example distinguishes predictions, trade simulation and metrics. Dusty's desktop must not imply a Kronos forecast exists merely because a backtest completes. Keep brokerage cost/risk validation independent. |
| [Chronos](https://github.com/amazon-science/chronos-forecasting/blob/8589d1988e9676817548e9626738ff06b6ca6370/ci/evaluate/backtest_config.yaml) `8589d1988e9676817548e9626738ff06b6ca6370` | Dataset offsets, prediction lengths and roll counts are explicit. Our same-window seed run must not masquerade as rolling forecast evaluation; that remains the next integration phase. |
| [Uni2TS/Moirai](https://github.com/SalesforceAIResearch/uni2ts/blob/cfd46d4510ed8896f263116f32928eede05b0a75/src/uni2ts/model/moirai/forecast.py) `cfd46d4510ed8896f263116f32928eede05b0a75` | Past/future fields, observed masks and prediction lengths are distinct. Retain explicit availability/confirmation timing and package-bound feature periods; do not import a heavy model to fill a UI gap. |
| [Vibe-Trading](https://github.com/HKUDS/Vibe-Trading/blob/333a3fc5f9a54b2d6eb47b4e9d54e141295d44f6/agent/backtest/engines/forex.py) `333a3fc5f9a54b2d6eb47b4e9d54e141295d44f6` | Symbol-specific metal/FX units and spread conventions matter. Avoid universal pip/lot assumptions; reject unsupported economics. Its simplified commission behavior is not broker evidence. |
| [Qlib](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/qlib/backtest/executor.py) `79633dd9506ea689e5400dea0197717b5b3d74b7` | Separate calendar, decisions, exchange and execution accounting. Reuse the existing Dusty laboratory behind a coordinator, rather than calculating signals/P&L inside Tk callbacks. |
| [RD-Agent](https://github.com/microsoft/RD-Agent/blob/2c878f9d2453dced35061165786d1f31bbff0ab6/rdagent/core/experiment.py) `2c878f9d2453dced35061165786d1f31bbff0ab6` | Experiments have hypotheses, workspaces, results and recovery boundaries. A fresh run does not inherit success from its parent; preserve old evidence and evaluate changed packages independently. |
| [TradingAgents](https://github.com/TauricResearch/TradingAgents/blob/9dee508c44662702281a8dbaad1f7b42179b5ba7/tradingagents/graph/checkpointer.py) `9dee508c44662702281a8dbaad1f7b42179b5ba7` | Checkpoint identity includes an analysis signature; ticker path traversal is rejected. Use generated run IDs and fixed artifact names; no arbitrary symbol-derived paths or cross-configuration result reuse. |
| [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund/blob/eff8a7320fcf0b473b135690fa1a5b0d9b022a83/hedge_fund/backtesting/fund.py) `eff8a7320fcf0b473b135690fa1a5b0d9b022a83` | Fund-level historical cycles carry positions/cash and distinguish the mandate from the universe. Our single-symbol simulation is not a multi-symbol capital allocator or deployed fund. |
| [Automaton](https://github.com/Conway-Research/automaton/blob/d8f816881fd24b6f5e3d616e59edec387a447667/src/state/database.ts) `d8f816881fd24b6f5e3d616e59edec387a447667` | Transactional state and bounded task accounting matter. Retain atomic completed-state publication and a wall-clock budget; do not import replication, self-funding or sovereign code modification. |
| [Microsoft Agent Framework](https://github.com/microsoft/agent-framework/blob/baf0ea5252eb3faa232b811c1c4d95771afd10ed/python/packages/core/agent_framework/_workflows/_checkpoint.py) `baf0ea5252eb3faa232b811c1c4d95771afd10ed` | Committed checkpoint state and atomic replacement provide a useful persistence pattern. Keep the implementation small and typed; unfinished work is not a certificate. |

## Official technical references

- [MetaQuotes history API](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py): UTC, available-chart history limits and named OHLC/volume/spread fields.
- [Symbol properties](https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants): point and trade tick economics are distinct.
- [Native margin calculation](https://www.mql5.com/en/docs/python_metatrader5/mt5ordercalcmargin_py): current account/environment estimate, not historical margin evidence.
- [Terminal information](https://www.mql5.com/en/docs/python_metatrader5/mt5terminalinfo_py) and [initialization](https://www.mql5.com/en/docs/python_metatrader5/mt5initialize_py): explicit path, portable mode, build and data-directory identity.
- [Python Tk threading](https://docs.python.org/3.11/library/tkinter.html#threading-model) and [multiprocessing](https://docs.python.org/3.11/library/multiprocessing.html): main-thread event ownership and spawn-safe worker entry points.

Primary documentation resolved this phase's technical questions; no Reddit/community advice was
needed to override an official API contract. No claim of having read unrelated OpenAI/Claude docs
or imported a forecasting model is made.

## Beginner test steps after updating

Keep the existing MT5 Demo account open. Leave other EAs/automatic trading off for this read-only
test. Close the old Dusty window before changing branches. In the existing project PowerShell:

```powershell
git status --short
```

If this prints modified or untracked files, stop and preserve them; do not reset or overwrite them.
If it prints nothing, fetch the new branch explicitly (the original clone was single-branch):

```powershell
git remote set-branches --add origin carson/m101-connected-research
git fetch origin
git switch --track origin/carson/m101-connected-research
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m dusty.basic_ui --repository .
```

No dependency reinstall is needed for an existing editable M100 installation. Select the terminal,
Connect, select an exact broker symbol, then one of the RESEARCH ONLY strategies. Use Start to review
the date-window/cost assumptions. Research Results shows the saved outcome. A zero-trade result is
valid evidence, not a reason to bypass Guardian. If history is unavailable, open that exact symbol's
M15 chart in MT5, load more chart history, reconnect and retry. This UI does not alter Market Watch.
After a halt, restart Dusty before another run. Save the result/error and exact Git SHA for review.

## Remaining integration order

1. Obtain a real Windows run of this exact path and inspect saved artifacts; do not substitute CI
   fixtures for broker history evidence.
2. Connect authorized public-strategy acquisition/quarantine and reviewed declarative translation.
3. Connect forecast datasets/models and prespecified rolling, purged out-of-sample scoring, ablation,
   multiple-testing accounting and untouched evaluation periods. Repeatedly testing until a win is
   not evidence of improvement.
4. Obtain exact-environment native indicator and Strategy Tester trade/cash parity using the existing
   probes and ex-ante tolerances; do not auto-relax a failed comparison.
5. Integrate immutable qualified demo sessions, market-clock scheduling, portfolio/risk allocation,
   broker reconciliation and durable forward outcomes. Live remains a separate authorization gate.

Software tests can establish deterministic transformations and failure handling. They cannot
establish profitable forecasting, complete historical economics, native parity, autonomous
self-improvement, or future capital growth. That is the investment committee's boundary.
