# M102 — Explain capital constraints without changing them

## Scope and definition

This correction builds on M101 `30f7aa0c872379efc29b5a677b7f122c77442e72`.
The old branch and artifacts remain intact. It does not add execution authority, an optimizer,
or a profitability claim. The reviewed strategy rules, 0.25% base growth risk and mode gates
are unchanged. The desktop still performs read-only MT5-history / Python simulation, not native
Strategy Tester execution. Demo and Live remain locked.

| Display | Meaning |
|---|---|
| Broker minimum lot / step | Actual selected symbol's `volume_min` / `volume_step` from the broker snapshot; no guessed fallback |
| Current balance | Latest read of `account_info().balance`, separate from equity; timestamped and explicitly not a live feed |
| Refresh Account | Read-only inventory refresh, preserving selections only for the same account and environment; failure clears the old snapshot |
| Preferred balance (risk sizing only) | Highest sampled minimum-lot risk threshold, available only after every candidate in a completed run has been sized |
| Growth rejection explanation | Approved / candidate counts, minimum-lot risk rejections, recorded minimum-loss range and all rejection counts in the detailed report |

For each **sized** candidate:

```
effective requested risk = recorded allowed loss / equity before candidate
minimum-lot planned loss = recorded loss per lot × broker minimum lot
sizing balance threshold = minimum-lot planned loss / effective requested risk
```

Use the recorded sizing result, which already incorporates stop distance, point/tick conversion,
decision-time spread proxy and the run's commission/slippage assumptions. Do not substitute an
approved-volume risk fraction (zero on a rejected trade), reapply spread, or assume point == tick.
The existing sizing engine is not changed. The estimate is explanatory and never fed back into it.

The highest threshold is a **past-sample risk-sizing estimate**, not a desired deposit or a
minimum account size that guarantees trading. It excludes margin constraints, other positions,
unmodeled fees/swaps, gap-through losses and future setups. A larger account can still fail every
other gate or lose money. It is not a hypothetical rerun of the strategy at the displayed balance.
Unsized/risk-vetoed candidates prevent an aggregate preferred estimate; partial ranges remain
labeled as sized-setup ranges. No candidates means unavailable, not zero. Display thresholds round
up to two decimal places; full-precision evidence and risk decisions are not rounded or altered.

Example using synthetic inputs: a 1-lot minimum with planned losses of 60 and 140 account-currency
units at 0.25% risk gives thresholds 24,000 and 56,000. A 1,000-unit starting balance allows 2.50
per candidate, so both trades are rejected. Dusty explains that rejection; it does not increase risk,
move the stop or recommend funding the account.

## Account and selection safety

Start's assumption confirmation refreshes the snapshot in the existing background task queue
before freezing the request. Refresh uses no login, symbol-selection, order-send or position-close
operation. Snapshot acquisition checks account identity/mode/currency and terminal environment
again after inventory, returning the latest observed balance. Account, server, currency, data path,
terminal build or installation drift fails closed and requires reconnection/reselection.
A changed selected symbol specification requires reselection. No refresh runs during research
or Codex development. A failed refresh cannot start a job from the stale snapshot.

The last result's estimate is displayed only for its matching account, terminal, broker symbol
specification, strategy metadata, operating mode and exact source commit. A balance-only refresh
can retain it, but the result still labels its original hypothetical starting balance separately
from the newly observed account balance. Closing/restarting the app does not automatically import
or recertify old runs; saved artifacts remain on disk. Another process can still change MT5 after
a snapshot: the existing research reader independently verifies identity before/after history.

## Numerical reproducibility correction

Python 3.12 changed floating-point built-in `sum()` to a compensated algorithm. M101's EMA/SMMA/RSI
seeds inherited the interpreter's choice. Tiny feature differences could therefore change cognition
fingerprints across Python versions even when all strategy decisions and cash results agreed.

M102 names the policy `sequential-binary64-v1` and explicitly fixes addition order to preserve the
original Python 3.11 seeds. It does not round hash inputs, weaken comparisons or assert native MT5
indicator parity. Package fingerprints now bind this numerical policy in addition to strategy,
feature periods and cognition policy. This is a new package identity, not retroactive certification
of an old one. SMA's existing ordered rolling arithmetic and all entry/exit/cost rules are unchanged.

New schema-2 requests and reports record Python version/implementation, OS/release/architecture,
binary-float properties, numeric policy and installed Dusty/NumPy/MetaTrader5 package versions.
Missing distributions are explicitly `not_installed`; collecting provenance does not connect to MT5.
Old schema-1 artifacts are never rewritten or assigned guessed runtime versions. The four-file
format and hash integrity checks remain. Account artifacts remain private and outside Git.

## QC and proof boundaries

Regression coverage includes fractional broker lots, reduced effective risk, changing simulated
equity, no candidates, partial sizing, margin vetoes, invalid inputs, display-only ceiling, account
refresh/drift/failure/locking, symbol changes, stale result scoping, Tk rendering with fake widgets,
explicit seed arithmetic, an exact complete-feature golden hash and recorded runtime provenance.
The real spawned fixture worker also checks that the structured explanation reaches the view.
Windows additionally constructs real Tk widgets and renders a completed synthetic result; Linux
skips that Windows-only smoke test. This checks widget construction, not interactive usability.

The CI matrix now runs the entire suite and named transparency/connected-research gates on
Windows and Ubuntu with **both Python 3.11 and 3.12**. A private read-only replay of supplied M101
evidence matched the complete original laboratory object, including every cognition fingerprint,
after the numerical fix. That input data is not a committed test fixture. New public tests use only
synthetic inputs. See the exact commit's Actions run for actual CI results; configured jobs alone
are not evidence that they passed. CI/headless Tk tests are not an interactive desktop acceptance
test or native MT5 certification.

The M101 [ten-repository comparison](m101-connected-research.md#ten-repository-source-comparison)
remains the broader architectural reference. This bounded correction uses primary API/language
documentation rather than importing a new agent runtime or claiming another full ten-repository audit:

- [Python 3.12 language changes](https://docs.python.org/3/whatsnew/3.12.html#other-language-changes): floating sum semantics.
- [MetaQuotes account information](https://www.mql5.com/en/docs/python_metatrader5/mt5accountinfo_py): balance and equity are separate fields.
- [MetaQuotes symbol properties](https://www.mql5.com/en/docs/constants/environment_state/marketinfoconstants): minimum/step volume and point/tick units.

## Updating the existing Windows installation

Close Dusty, leave MT5 on the Demo account, and open PowerShell. First:

```powershell
cd "$env:USERPROFILE\DustyDragon-M100"
git status --short
```

If modified/untracked files appear, stop and preserve them. Do not reset or overwrite them.
If the output is empty, the original single-branch clone can add this branch:

```powershell
git remote set-branches --add origin carson/m102-sizing-transparency
git fetch origin
git switch --track origin/carson/m102-sizing-transparency
git rev-parse HEAD
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\.venv\Scripts\python.exe -m dusty.basic_ui --repository .
```

If that local branch already exists, use `git switch carson/m102-sizing-transparency` instead;
do not force it. No reinstall is needed for the existing editable Python 3.11 installation.
Select terminal → Connect → exact symbol → research strategy. Check minimum lot and timestamped
balance. Refresh Account should preserve the selection on the same unchanged account. Start a new
read-only run with documented cost assumptions. The estimate should appear only after completion,
with growth rejection counts. Keep Demo/Live locked and other EAs/automatic trading off for this
acceptance test. Preserve any error message and `git rev-parse HEAD` output for diagnosis.
