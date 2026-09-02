# M103 — Fixed-window evaluation and cost-evidence foundation

## Scope and investment boundary

Parent: M102 `dd5f3cc7bb708de4d96fae499b97d9198580ea43`. The verified M102 branch and the user's
original research artifacts remain unchanged. This milestone connects one additional research
workflow; it does not implement model training, automatic optimization or broker execution.

The source audit covered the desktop controller/worker, feature and V2 execution paths, laboratory,
capital explanation, statistical trial registry, forecast split/evaluation tools and regression
contracts. Existing forecast splitters operate on labeled forecast examples; the desktop uses
trade episodes, so applying a row split after generating complete trades would leak boundary
outcomes. The new path instead runs separate flat-start episodes, with a prespecified entry-tail
guard and past-only feature warm-up. Unrelated forecast/governance modules are retained: they are
not dead code merely because this small desktop workflow does not yet call them.

This is a focused source audit plus whole-project regression QC, not a claim to have independently
re-certified all previous milestones operationally. The ten-repository comparison in
[M101](m101-connected-research.md#ten-repository-source-comparison) remains the broader design
baseline: PIT data and separation of execution from evaluation, bounded experiments and preserved
failed evidence. No external runtime or downloaded trading code was imported for this correction.

## What the desktop can now do

| Control / evidence | Contract |
|---|---|
| Fixed end UTC | Optional `YYYY-MM-DD HH:MM`, aligned to 15 minutes; bounds stop moving when the clock advances |
| History days | Still bounded to 1–30; at most 3,000 raw MT5 bars |
| Holdout days | Zero preserves exploratory mode; otherwise strictly less than history days and requires a fixed end |
| Cost source / assumptions note | One printable line, at most 400 characters; stored as user provenance, never fetched or executed |
| Development / holdout results | Separate minimum-lot and growth simulations, no optimizer, no capital or position carryover |
| Main preferred-balance display | Explicitly `HISTORICAL HOLDOUT ONLY` when the completed run is a split evaluation |
| Recent broker cost observation | Separately recorded read-only execution costs; never substituted for frozen simulation inputs |

A future fixed end refuses to run until the window has elapsed. That is **not** prospective
preregistration: this milestone does not persist a sealed future plan before that date or prove
that its data was unseen. A completed historical holdout is always labeled prior exposure UNKNOWN
and `RESEARCH_ONLY_NOT_QUALIFIED`. Repeated runs are not independent evidence, and no statistical
confidence or multiple-testing correction is claimed. The prior trial registry is not populated
with invented scores or fabricated trial counts.

## Chronology and censoring

All segment boundaries use completed-feature **availability timestamps**. Development covers
`[start, holdout_start)`; holdout covers `[holdout_start, end)`. The acquisition endpoint may return
a raw bar starting at `end`, but that observation does not create a scored decision at the excluded
end. It remains in the saved raw acquisition so the original API response is inspectable.

Each segment requires at least 64 observed completed bars, and more than the finite maximum holding
horizon. An inadequate segment fails; Dusty does not expand dates, fill gaps or move the split to
manufacture a result. First/last observed times and counts are reported; this is not a broker-calendar
coverage certificate. The last `max_hold_steps` observations of each segment prohibit new entries.
For the present RSI seeds that is 16 observations, not necessarily four wall-clock hours when a
market closes. This declared offline sample-tail guard allows all admitted trades to exit within
their own segment instead of silently dropping unresolved positions or borrowing future prices.
It is not a live session-close policy.

Holdout indicator calculations may use earlier development bars as warm-up because those bars
were already available. Warm-up bars do not create holdout cognition, positions or ledger rows.
Both segments use the same frozen strategy, feature periods, risk policy and cost assumptions.
Minimum-lot capital and growth capital each reset to their respective configured starting value;
the holdout does not inherit development gains or losses. The existing non-split laboratory's
default calculations remain unchanged. Its exploratory results are not out-of-sample evidence.

## Costs: observations versus assumptions

The reader now also requests recent account deals over a bounded 30-day interval, with an accepted
response cap of 10,000 rows. The spawned worker's existing deadline/cancellation still bounds a
stalled native call. Only exact-symbol native BUY/SELL execution rows contribute. Deposits, credits,
other symbols and standalone commission/balance records are excluded. Thus the result is not a
complete account statement. Commission, fee and swap retain their broker-reported cash signs.
Missing/nonfinite fields remain incomplete, never zero; aggregate amounts include complete rows only.
No rate per round-trip lot is inferred from partial fills or incomplete positions.

Possible statuses include UNAVAILABLE, NO_MATCHING_EXECUTIONS, INCOMPLETE_COST_FIELDS,
BOUNDED_READ_LIMIT_EXCEEDED and OBSERVED_NOT_VERIFIED. Empty history or a demo funding deposit
cannot establish free trading. Account/terminal/symbol identity is checked again after this read.
No login, order-send, symbol-selection or account mutation is added.

These recent observations are contemporary context, not fees known at the historical strategy's
decision time. They never change the costs chosen before the simulation. Cost notes cannot promote
themselves into a verified fee schedule, even if the note says "verified". Current spread proxies,
tick economics and margin estimates retain their existing limitations. Swaps and other fees remain
incomplete in the simulated cash model. Actual fee verification still requires evidence applicable
to this broker/account/symbol and a reconciled native execution or tester comparison.

## Evidence and QC

New request/report schema 3 retains the four files: request, bars, report and result. The request
contains fixed boundaries/protocol fingerprint and cost provenance before acquisition. The worker
compares the complete frozen request with its actual configuration before reading data. The primary
laboratory in a holdout report is the holdout; the complete development laboratory is stored under
`evaluation.development_laboratory`, alongside segment metadata and explicit limitations.
Old artifacts are never rewritten or upgraded with guessed provenance. Files stay outside Git;
hashes detect accidental alteration, not a malicious same-user rewrite of the entire evidence set.

Tests attack future/naive/misaligned dates, sliding windows, invalid holdout lengths, changed frozen
costs/configuration, boundary-crossing trades, missing segments, future warm-up, end-of-sample entry
censoring, future-price perturbations, capital resets, deposit-only history, absent fee fields,
account drift during the extra read, and the actual spawned-process result path. The existing full
suite, numerical golden fingerprint and Windows Tk checks remain. CI runs Python 3.11 and 3.12 on
Windows and Ubuntu; verify the exact published commit's Actions result rather than assuming a
configured test matrix has passed. Native MT5 observations and GUI acceptance still need the user's PC.

Primary references consulted:

- [MetaQuotes deal history API](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py): bounded-date history access and execution/deposit examples.
- [MetaQuotes deal properties](https://www.mql5.com/en/docs/constants/tradingconstants/dealproperties): distinct execution types and commission/fee/swap properties.
- [MetaQuotes bar history API](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py): UTC and available terminal history boundaries.
- [TimeSeriesSplit documentation](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html): ordered evaluation and gap separation; its equal-spacing assumptions do not justify filling broker closures or applying row splits to complete trade episodes.

## Beginner update and acceptance test

Close Dusty. In the existing PowerShell project folder, first run `git status --short`. If it prints
filenames, preserve them and stop for review. With a clean worktree:

```powershell
git remote set-branches --add origin carson/m103-fixed-window-evaluation
git fetch origin
git switch --track origin/carson/m103-fixed-window-evaluation
git rev-parse HEAD
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m dusty.basic_ui --repository .
```

Keep MT5 on the Demo account, with other EAs/automatic trading off for this read-only test. Connect
and select the same broker symbol/research seed. The new inputs are in Start's assumptions dialog;
the main UI is still small. For a functional historical split, choose a fixed past UTC end,
14 history days and 7 holdout days. Enter broker-supported cost assumptions when known; otherwise
state explicitly in the note that the values are unverified functional-test assumptions. Do not
guess a broker fee or call zero costs realistic. A split already studied by a human is not untouched.

Run once and inspect separate Development / Holdout rows, the holdout-only sizing label, fee evidence
status, and continued Demo/Live locks. Save the new four JSON files for review. Insufficient history
or no execution-cost evidence is a valid limitation to report, not a reason to change the rules until
a profitable answer appears. Next stages remain verified cost modeling, genuinely preregistered
prospective evaluation, native indicator/tester parity and independently qualified demo execution.
