# M105 — Research comparison and entry-only abstention

Baseline: M104 `915ee55a5b6385a68f55f6eab72c103bf0fc16a5`.
The original branch, numerical feature policy, two seed packages and prospective receipt
protocol are preserved. This is a bounded research improvement, not completion of autonomous
learning, reliable forecasting or broker-native certification.

## What the user can do

The Start dialog adds **Preview dates** and **Compare strategies (no orders)**. Ordinary runs
also require a date confirmation; a zero-day holdout prominently says **WHOLE-WINDOW
EXPLORATION — NO HOLDOUT**. Comparison requires an explicit completed fixed UTC window,
nonzero holdout days, and a cost-source/assumption note. It uses the selected broker symbol
but tests BOTH built-in seeds, regardless of which seed is selected in the main dropdown.

Every candidate uses the same acquired bars, account starting balance and broker economics.
The existing completed-bar availability timestamps, next-observable execution reference,
historical/proxy spread separation, past-only feature warm-up and max-hold entry-tail guards
apply. The final unconfirmed raw bar is discarded. Missing bars are not filled and observed
gaps do not by themselves distinguish a market closure from missing history.

The fixed matrix contains:

| Candidate | Additional entry restriction |
| --- | --- |
| Long RSI seed | None; original rules unchanged |
| Long RSI seed + trend | Completed close > SMA(20), and EMA(20) > SMA(20) |
| Short RSI seed | None; original rules unchanged |
| Short RSI seed + trend | Completed close < SMA(20), and EMA(20) < SMA(20) |
| No-trade control | Deny every entry |

Each candidate is run under configured costs and configured costs **plus 10 broker points
of total round-trip adverse slippage allowance**. This stress is a fixed hypothetical
assumption, not a broker fee estimate or native execution observation. Points are converted
with broker `point_size`, not `tick_size`. Missing point size rejects the comparison.
Both costs are tested in development and holdout: **5 × 2 × 2 = 20 cases**. Capital and
positions reset between cases. There is no automatic parameter search or winner selection.

The simple trend restriction is an **untrained hypothesis**, not an inferred market regime
or forecast. It can only veto an entry already supported by the strategy/cognition chain.
Missing, invalid or equal trend values deny permission. It never changes exits, risk limits,
stop rules, position size, health checks or broker permissions. Occupancy and cooldown may
change after an entry is blocked, so filtered executed trades need not be a subset of seed
trades. The report distinguishes blocked qualifying cognition signals from actual fills.

## Evidence and authority

The complete matrix contract (candidate packages, features, cognition, trend rule, cost
scenarios and limited screen) is frozen in `request.json` before acquisition. The worker
rejects changed requests before reading history. The existing exact-clean-commit checks,
account/terminal identity checks, cancellable spawned worker and timeout remain in force.

The usual request/bars/report/result files remain private, outside Git. `report.json` adds:

- all 20 cases, without suppressing losses, rejections or zero-trade cases;
- contract and completed-data fingerprints, plus distinct case identities binding the
  candidate, actual costs, economics, symbol, segment and declared dates;
- entry-veto decisions/reasons by availability timestamp;
- minimum-lot and growth P&L, marked drawdown, trades, sizing decisions and cash ledgers;
- a fixed limited screen (20 closed growth trades, positive net P&L, marked drawdown <= 2%);
- `selected_winner=null`, `deployment_decision=ABSTAIN_UNQUALIFIED`, `promotion_eligible=false`.

Any unfinished/failed case fails the comparison; partial success is not published. Completed
reports are bound by the existing artifact hashes. Hashes provide consistency checks, not
independent attestations against a privileged same-user rewrite.

The main window's preferred-balance explanation remains for the **selected original seed's
holdout only**, explicitly not a chosen comparison winner. It is a sizing calculation, not
a deposit recommendation, margin guarantee or validated capital target.

Filtered/control laboratory runs cannot export legacy native manifests or expected execution
envelopes, because those identities do not bind the additional veto. The comparison itself
does not export manifests. Seed-only native proposals remain proposals, never evidence.

## What this still cannot establish

Historical windows may already have been examined. Repeating comparisons is not independent
validation, and this screen does not correct multiple testing or establish statistical confidence.
No-trade earns zero in this control, before interest/opportunity cost; it is not a profitable
strategy. Passing any/all limited screens does not qualify Demo or Live.

Costs, swaps, fees and historical economics are incomplete/unverified. The current long-side
margin proxy is also reused for short research and is not empirical short-margin parity.
Growth resizing changes trade weights, so differences under stress are not a fixed-volume
attribution of cost drag. Native indicator, tick/deal, cash and execution parity still require
actual MT5 evidence. There is no new order sender, native custom-indicator runner, learned
forecaster, portfolio allocator or self-modifying production strategy in this milestone.

This focused review follows the [M101 pinned ten-repository design comparison](m101-connected-research.md#ten-repository-source-comparison),
not a new exhaustive audit of all ten repositories. The relevant lessons are Qlib's separation
of research and execution, RD-Agent's explicit experiment/workspace results, Vibe-Trading's
unit/timing failure catalogue, and the durable-evidence boundaries examined in Automaton and
Agent Framework. RD-Agent's experiment source was rechecked for this slice. No downloaded
strategy code or reference framework is imported or executed.

Relevant primary documentation:

- [MetaQuotes copy_rates_range](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py):
  UTC dates, raw bar open times and terminal history availability. Dusty's scored interval uses
  **availability timestamps**, with inclusive start and exclusive end.
- [Python Tkinter threading model](https://docs.python.org/3.11/library/tkinter.html#threading-model):
  UI operations remain on the UI thread; MT5/research stays in the existing separate worker.

## Preserve a frozen M104 plan: install side by side

Do NOT switch, pull, reinstall or upgrade packages in the checkout/virtual environment that
registered the old plan. M104 and M105 must not share an editable virtual environment. Keep
the old private plan registry as well. Even preserved code cannot guarantee that a changed MT5
build or broker symbol specification will still match the frozen receipt later.

Close Dusty's windows, open PowerShell and install into a NEW directory:

```powershell
cd "C:\Users\lord1"
git clone --single-branch --branch carson/m105-research-abstention https://github.com/AnhTranHarris/Financial-AI-Learn-Maybe.git DustyDragon-M105
cd "C:\Users\lord1\DustyDragon-M105"
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e . MetaTrader5
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

If the clone directory already exists, stop and inspect it; do not overwrite it. On test
failure, retain the output and do not launch the new UI. To launch after tests pass:

```powershell
.\.venv\Scripts\python.exe -m dusty.basic_ui --repository . --research-directory "$env:LOCALAPPDATA\DustyDragon\research-m105"
```

That distinct research directory keeps the older future-plan registry separate. M105 cannot
register a comparison as a prospective plan or consume a prospective receipt with comparison
settings. Ordinary original-seed future plans remain supported, bound to their own exact code.
Do not attempt to edit an old receipt to match a new commit.

## Acceptance checks

Automated tests cover symmetric trend logic, invalid inputs, future perturbations, unchanged
default lab results, unchanged original request shape, entry-only authority, no-trade cash,
native-manifest rejection, segment boundaries, point-vs-tick stress, exact matrix coverage,
trade/sizing/ledger reconciliation, frozen-request tampering, failed-matrix publication,
prospective isolation, spawned completion/polling and Windows Tk confirmation/cancel behavior.
Mode buttons and Start are disabled at construction; only a rendered backend gate can enable
them. The Windows test deliberately pauses discovery to check this initial state, then verifies
that rendering enables eligible Backtest while Demo/Live remain disabled.

The CI matrix remains Windows/Ubuntu on Python 3.11/3.12, with an explicit M105 gate and the
full suite. CI uses synthetic fixtures, not a native terminal. A real local acceptance run
still needs the user to select their demo terminal/symbol, enter a historical split and cost
note, preview it, click Compare, and inspect the resulting 20-case report. No orders should
appear; Demo/Live should remain disabled. Preserve the report even if every candidate loses.
