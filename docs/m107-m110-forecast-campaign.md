# M107–M110 — connected forecasting, queued research, readable evidence

One integrated research-only release from M106 `cc4fa2b0d455d409e4da2a8d1e4423a22427d0e8`.
The four labels identify delivered engineering components, not four certifications of trading skill.
The ordinary seed and M105/M106 comparison semantics remain unchanged. This release does not
modify, migrate, evaluate or rebind old prospective receipts.

## Delivered scope

| Component | Implemented | Not claimed |
|---|---|---|
| M107 fitted forecast | Direct ridge regression from a completed four-observation return to a 16-observation future close return; fitted from earlier labels | Foundation model, LLM trader, proven alpha |
| M108 chronological evaluation | Three expanding-past training windows; parameters frozen within each test fold; score against no-change and training-mean baselines | Untouched data, independent folds, statistical certification |
| M109 research queue | Thirty prespecified cases from one symbol/acquisition; progress, individual case checkpoints, fail-stop/cancel/timeout retention | Multi-symbol portfolio, automatic optimization, automatic retries or resume |
| M110 readable results | Resizable case selector, scrollable trade table, selectable entry/cost detail; full precision remains in JSON | Causal explanations or native MT5 fills |

### Forecast model and evidence

Let `x = 10000 * (close[t] / close[t-4] - 1)` and
`y = 10000 * (close[t+16] / close[t] - 1)`. A training pair is included only if
the **target observation's availability timestamp is strictly before the fold start**.
At least 64 pairs are required. Mean and population scale of `x`, mean of `y`, and a single slope
are fitted on those pairs. With `z=(x-mean(x))/scale(x)`, the slope is
`mean(z*(y-mean(y))) / (mean(z*z)+1)`. The penalty is fixed against mean squared error, not tuned.
Constant features use scale one. Predictions are capped at ±20% as a fixed numerical guard,
not as a learned confidence interval. No hyperparameters are chosen from test results.

The contract, actual training inputs, coefficients, pair count, training cutoff and each issued
forecast are fingerprinted. Inference uses only completed closes up through the issue timestamp.
The coefficients remain frozen for that fold. Scoring labels are assembled separately and never
passed into inference. The last 16 test observations remain forecast records but are unscored
because their targets do not mature within the fold. All three forecast methods use identical
scoring origins. MAE is in symbol price units, **not account-currency P&L**. Skill is
`1 - model_MAE / no_change_MAE`, or unavailable when no-change MAE is zero. Direction includes a
flat class; no-change predicts flat and is not a 50%-chance directional classifier.

Horizons count actual observations. Session gaps are not filled, and 16 observations are not
promised to equal four wall-clock hours. Targets overlap. Later folds train on observations that
may have been test data in earlier folds; this is expanding historical research, not independent
unseen validation. There are no fitted/calibrated uncertainty intervals in this baseline.

The existing laboratory's forecast-to-cognition interface is reused. Only the ridge forecast is
connected to the seed's conflict veto. It cannot create an RSI setup, override a risk/health/spread
veto, change exits, increase risk, or authorize broker execution. A forecast-required run blocks
new entries when a forecast is absent. Issue time and origin close must match the current bar.
Forecast-assisted runs cannot emit native manifests/envelopes under the unmodified seed identity.

### Campaign contract

The assumption sheet retains its fields. In campaign mode, **holdout days means days per test fold**.
History must cover at least `3 * holdout_days + 2` days, with a maximum acquisition of 30 days.
A past, M15-aligned fixed UTC end and a nonempty cost note are mandatory. For example, 28 history
days and seven days per fold leave seven initial training days. Actual observed training pairs
and test bars are checked; unavailable history fails without silently expanding or sliding dates.

The final three test folds each run these five candidates under configured costs and configured
costs plus ten broker points of total round-trip slippage:

1. Long RSI seed, without forecast.
2. Long RSI seed with fitted forecast conflict veto.
3. Short RSI seed, without forecast.
4. Short RSI seed with fitted forecast conflict veto.
5. No-trade control.

This is 30 cases. Every case starts flat at the same frozen hypothetical balance, independently.
The final 16 observations prohibit new entries; trades cannot cross the declared fold boundary.
All cases, no-trade results, rejections and failures are retained. The prior ≥20 trades, positive
net P&L and ≤2% marked-drawdown screen stays unchanged and is never deployment approval.
The campaign always emits `selected_winner: null` and `promotion_eligible: false`.

One identity-checked MT5 acquisition supplies all cases. Per-case queue state and case files are
written atomically. A failed fit or simulation halts the remaining queue; no model fallback or
retry occurs. Cancellation/timeout kills only Dusty's owned research worker, retains completed
case checkpoints, and marks unfinished work cancelled/timed-out/not-run. Orphaned checkpoints
never count as a successful result. Progress text is advisory; completion still requires hashed
request, bars and report artifacts. Hashes are integrity checks, not independent attestation.

The result's top-level laboratory/capital summary remains the **selected seed baseline**, not a
campaign winner or a forecast recommendation. This is labeled explicitly. Preferred balance remains
a risk-sizing estimate only, not a deposit recommendation. Current broker economics and margin
remain historical proxies; fee schedules, swaps and exact execution paths remain unverified.

## Reference repository review

These are focused code-path comparisons at the previously established commits, rechecked for this
release. They are not a claim to have exhaustively audited each entire repository. No upstream
implementation was copied or imported, and their existence does not prove Dusty's capabilities.

| Reference / pinned source | Design decision |
|---|---|
| [Kronos backtest example](https://github.com/shiyu-coder/Kronos/blob/67b630e67f6a18c9e9be918d9b4337c960db1e9a/examples/run_backtest_kronos.py) | Keep predictions separate from realized prices; never substitute predicted prices into missing execution history. Kronos inference is not installed. |
| [Chronos evaluation configuration](https://github.com/amazon-science/chronos-forecasting/blob/8589d1988e9676817548e9626738ff06b6ca6370/ci/evaluate/backtest_config.yaml) | Freeze horizon and rolling evaluation boundaries before results. No Chronos runtime/download introduced. |
| [Moirai forecast interface](https://github.com/SalesforceAIResearch/uni2ts/blob/cfd46d4510ed8896f263116f32928eede05b0a75/src/uni2ts/model/moirai/forecast.py) | Separate past input and future target paths. Avoid importing its device/dependency and sample-calibration requirements as if already validated locally. |
| [Vibe-Trading Forex engine](https://github.com/HKUDS/Vibe-Trading/blob/333a3fc5f9a54b2d6eb47b4e9d54e141295d44f6/agent/backtest/engines/forex.py) | Explicit symbol units and friction matter; do not inherit generic pip spreads or assume ECN commissions are zero. Preserve Dusty's broker point/tick distinction. |
| [Qlib executor](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/qlib/backtest/executor.py) | Preserve separation of decisions, execution/cost accounting and account state; flat reset every research case. |
| [RD-Agent experiment](https://github.com/microsoft/RD-Agent/blob/2c878f9d2453dced35061165786d1f31bbff0ab6/rdagent/core/experiment.py) | Separate frozen experiment, result and feedback. Do not carry previous results into a new hypothesis or expose automatic self-rewriting in the trading runtime. |
| [TradingAgents checkpointer](https://github.com/TauricResearch/TradingAgents/blob/9dee508c44662702281a8dbaad1f7b42179b5ba7/tradingagents/graph/checkpointer.py) | Bind evidence to run/configuration identity. Dusty retains checkpoints without automatic resume or deleting losing cases. |
| [ai-hedge-fund backtest](https://github.com/virattt/ai-hedge-fund/blob/eff8a7320fcf0b473b135690fa1a5b0d9b022a83/hedge_fund/backtesting/fund.py) | Keep fund-level risk and rebalance state distinct from predictions. This release remains a single-symbol laboratory, not a multi-asset fund. |
| [Automaton state database](https://github.com/Conway-Research/automaton/blob/d8f816881fd24b6f5e3d616e59edec387a447667/src/state/database.ts) | Explicit state transitions and durable boundaries. Existing atomic JSON is sufficient for the bounded single-worker queue; autonomous spending/agent spawning is not imported. |
| [Microsoft Agent Framework checkpoint](https://github.com/microsoft/agent-framework/blob/baf0ea5252eb3faa232b811c1c4d95771afd10ed/python/packages/core/agent_framework/_workflows/_checkpoint.py) | Configuration/topology compatibility matters for recovery; preserve old prospective code/environment bindings instead of migrating them implicitly. |

Also checked the primary references on [time-series cross-validation](https://otexts.com/fpp3/tscv.html),
[MT5 UTC/history limits](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py), and
[Tk's threading model](https://docs.python.org/3/library/tkinter.html#threading-model).
Evidence file reading is off the Tk thread; widget updates return through a queue on the Tk thread.

## Verification and remaining limits

- New regression coverage: strict past-label cutoff, future mutation, constant series, target alignment,
  insufficient history, identical scoring origins, missing/conflicting forecasts, no native export,
  complete 30-case matrix, cash reconciliation, fold boundaries, no-trade retention, contract tamper,
  fail-stop queue, cancel/timeout/crash, completion race, spawn-worker path, file hashes and case browser.
- The existing M106 regression suite remains part of the full suite. Windows/Python 3.11 and 3.12 CI
  exercises actual Tk widgets; Linux tests use fixtures, not a native MetaTrader terminal.
- An offline replay of the privately supplied NASUSD bars independently reconciled 12,880 ledger rows
  across 30 cases. No private account data or research files are included in this repository. The
  data were already exposed; this replay checks software arithmetic, not new out-of-sample performance.
- No new package dependency, network model call, broker write, LLM forecast, native Strategy Tester
  parity or profitable-strategy claim is introduced. The research worker retains its bounded timeout.

## Windows use — one update, one local check

Keep `DustyDragon-M100` and its original virtual environment/September receipt untouched. Install
this branch in the separate `DustyDragon-M105` development folder, after closing Dusty's windows
and confirming a clean worktree. Verify the exact published commit, run the full test suite, then:

```powershell
.\.venv\Scripts\python.exe -m dusty.basic_ui --repository . --research-directory "$env:LOCALAPPDATA\DustyDragon\research-m110"
```

Connect the same demo MT5 terminal, select the desired symbol and a seed, leave Backtest selected.
Start opens the assumption sheet. Choose your past fixed UTC end, history/test-fold days and explicit
cost assumptions, then **Run forecast campaign (30 cases; no orders)**. Review the date preview before
accepting. On completion, open **Last Research Results → Cases & trades**. Select a case and a trade.
The Summary tab separates forecasting scores from simulated trading results. One campaign runs all
cases; there is no need to click Run repeatedly or keep sending entire PowerShell histories.

If a test or installation check fails, do not run Dusty from the changed environment. Preserve the
error and local files; the previous M106 branch remains available as the known working baseline.
Do not reset/delete user data or substitute this environment for the original prospective one.
