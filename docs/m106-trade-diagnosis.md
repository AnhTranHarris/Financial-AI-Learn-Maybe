# M106 — Post-run trade diagnosis and cost attribution

Baseline: M105 `6f3c20d23e0b423ccb438194d80ec58735626cec`.
This milestone explains existing research results. It does not tune a strategy,
train a forecast, generate orders, or certify account growth.

## What changes

After **Compare strategies (no orders)**, **Last Research Results** contains two
read-only tabs with scrollbars:

- **Summary:** existing qualification results plus price/cost totals, win/loss
  counts, recorded exit counts, and fixed-volume cost attribution.
- **Trade diagnosis:** every potential trade's entry features/rule checks, side,
  initial stop/target, recorded exit, observed holding steps, elapsed minutes,
  minimum-lot cash, growth volume/cash, and growth rejection reasons.

A results window opened during research refreshes automatically on the Tk main
thread. It stays bound to its original run, does not switch to another run, and
cancels its scheduled callback on closure. Ordinary research remains viewable;
the diagnosis tab explains that diagnosis requires a completed M106 comparison.
Older saved reports are not rewritten or retrospectively certified.

## Diagnostic contract

`recorded-entry-exit-cash-attribution-v1` is frozen in comparison requests before
acquisition. Each comparison case carries a diagnostic object tied to its case
fingerprint. The existing final report hash covers this object and the matched
cost attribution. No new broker request, package dependency, manifest, public
artifact, or prospective-registry write is introduced.

The diagnostic layer is downstream of simulation. It never supplies entry,
exit, sizing, promotion, or model-training inputs. The original M105 candidate
contract, strategy rules, feature numerics, cost assumptions, laboratory,
evaluation, and risk code remain unchanged. Ordinary and prospective request
payloads do not gain a diagnostic field.

Each potential trade must match its sizing trace, strategy identity, direction,
entry rule, entry policy, and segment boundaries. Entry price and spread are
checked against the same availability-bar reference/proxy used by research.
Features are read only at the recorded entry's availability timestamp; the source
bar opening time is retained separately. Exit data are retrospective outcomes,
not ex-ante knowledge. Observed steps and elapsed minutes are distinct across gaps.

For the supported linear tick economics:

`gross = direction × (exit − entry) / tick_size × tick_value × volume`

Spread and total round-trip slippage are converted from price to cash with tick
economics; commission is cash per lot. The broker point is used when converting
point-denominated spread or the existing ten-point stress, not as a replacement
for tick size. The report labels fees/swaps as incomplete, not verified zero.

Diagnostic net totals must reconcile to minimum-lot and growth backtest P&L,
ending cash, counts and case metrics. Approved growth cash must also reconcile to
the individual sizing trace. A mismatch prevents publication of a successful
comparison. Rejected growth entries retain minimum-lot counterfactual evidence
but contribute zero growth cash. No-trade cases remain empty, not fabricated wins.

Outcome labels distinguish price losses before costs, nonnegative gross turned
negative by modeled costs, positive net outcomes, flat trades, and growth
rejections. These are arithmetic descriptions, **not causal explanations** of
market behavior or proof that a particular indicator is invalid.

## Cost versus sizing

Each candidate/segment pairs configured and stressed costs. If potential trade
paths differ, matched attribution is explicitly unavailable. Otherwise:

| Component | Meaning |
| --- | --- |
| Baseline net | Original growth results |
| Direct cost effect | Additional cost on each original approved volume |
| Fixed-volume stressed net | Baseline plus direct cost effect |
| Sizing/selection effect | Stressed re-sized net minus fixed-volume stressed net |

The components reconcile to the stressed growth result. When approvals change,
the residual includes selection as well as volume changes. Per-trade components
are retained in JSON. A higher-cost simulation can lose less because it takes
smaller positions; that does not demonstrate improved forecasting.

The fixed-volume overlay is arithmetic, **not a new risk-feasible backtest**.
Those original volumes are not reapproved for stressed costs, margin or capital.
No winner, rank, optimal exit, confidence interval, or trading approval is produced.

## Verification and reference scope

The named M106 test gate covers deterministic/additive diagnosis, entry timing,
rule observations, long/short cash, costs and units, failed reconciliation,
malformed inputs, rejected entries, zero controls, matched-path restrictions,
request/report integrity, partial failures, and the real Windows Tk lifecycle.
It also runs in the governance category and full Windows/Ubuntu Python 3.11/3.12
matrix. Linux without a display skips the Windows-only tests; CI does not claim
native MT5 execution or indicator parity.

The user's private M105 comparison was replayed without changing its inputs.
All 20 preexisting case objects, excluding additive diagnostic fields, matched
exactly. Its raw data, account details, results and receipts are not committed.
Reusing that history establishes reproducibility, not fresh validation.

This is a focused continuation of the pinned [ten-repository comparison](m101-connected-research.md),
not a new exhaustive ten-repository audit. Revisited source boundaries include
[Qlib execution results and trade information](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/qlib/backtest/executor.py)
and [RD-Agent's separate hypothesis, workspace and experiment result](https://github.com/microsoft/RD-Agent/blob/2c878f9d2453dced35061165786d1f31bbff0ab6/rdagent/core/experiment.py).
These support separating diagnosis from execution and retaining failed evidence;
no upstream runtime code is imported.

Official references rechecked for this slice:
[MetaQuotes history timestamps/fields](https://www.mql5.com/en/docs/python_metatrader5/mt5copyratesrange_py),
[broker point and tick properties](https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants),
and [Python Tk event/thread ownership](https://docs.python.org/3.11/library/tkinter.html#threading-model).

## Safe Windows update

Keep the original `DustyDragon-M100` checkout and its virtual environment intact
for the M104 future receipt. Do not change that receipt's dates, clock, code or
environment. Do not upgrade dependencies while preserving that evidence.

Close all Dusty windows. In PowerShell, start with:

```powershell
cd "C:\Users\lord1\DustyDragon-M105"
git status --short
```

If any files are listed, stop and preserve them. If no files are listed:

```powershell
git remote set-branches --add origin carson/m106-trade-diagnosis
git fetch origin
git switch --track origin/carson/m106-trade-diagnosis
git rev-parse HEAD
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

No dependency reinstall is required for the existing editable installation. Use
a separate new output directory:

```powershell
.\.venv\Scripts\python.exe -m dusty.basic_ui --repository . --research-directory "$env:LOCALAPPDATA\DustyDragon\research-m106"
```

Connect the demo terminal, select the intended symbol, and use the same fixed
historical comparison settings to check the new diagnostic display. Repeated
history is already exposed and is not untouched evaluation data. Preserve all
cost uncertainty notes. A new terminal snapshot or broker-history revision may
change results; do not edit evidence to force equality.

To return to the preserved M105 code, close Dusty, check that the worktree is
clean, and use `git switch carson/m105-research-abstention` in this M105 folder.
No reset, deletion or receipt migration is needed. Demo/Live stay locked.
