# M104 — Prospective registration and closed-position cost evidence

Baseline: M103 `5cc21d2f331fe206a222e3b1953cc72ce90a7530`. That branch and prior private
research artifacts are preserved. No strategy parameters, indicator calculations, sizing limits,
broker-order permission or trading qualification requirements are changed.

## Delivered scope

The desktop can register a future historical holdout before its availability-time start,
then evaluate that unchanged plan after its end. This is a **delayed historical replay** of a
locally preregistered plan, not real-time paper trading, model training or an automatic scheduler.
The PC need not remain on during the window. Broker history must still be available afterward.

The review covered the desktop controller, worker, evidence serialization, cost observer,
fixed-window evaluator, statistical registry and their regression contracts. The existing
statistical registry accepts scored trials; a registration or an interrupted run has no valid
score. A separate local plan/attempt journal therefore preserves those states without inventing
scores or falsely treating repeated research as independent trials. Existing statistical and
forecast modules are retained, not labeled dead code because this small workflow does not call them.

This is focused source review and whole-suite regression QC, not a fresh line-by-line security
audit of all ten inspiration repositories or operational recertification of M0–M103. M101's
[pinned ten-repository comparison](m101-connected-research.md#ten-repository-source-comparison)
remains the design baseline. RD-Agent's experiment/workspace separation was rechecked for this
slice; SQLite transactional state follows the durable-evidence direction also identified in
Automaton and Agent Framework. No reference runtime or downloaded strategy code was imported.

## Frozen prospective plan

The Start dialog retains all historical research inputs and adds **Freeze future holdout
(no orders)**. Registration requires a fixed UTC end, nonzero holdout days, and a cost-source
note. Holdout start must be strictly after registration and within 30 days; the acquisition
window remains 1–30 days. The UI refreshes the same verified account before freezing.

The receipt binds:

- exact source commit, Python/runtime/package provenance and research package fingerprint;
- terminal/account identity hashes, account mode/currency, terminal build and full broker symbol specification;
- strategy rules, features, cognition policy, dates, costs, numerical policy and initial capital;
- the M103 flat-reset/warm-up/tail-guard protocol;
- a fixed research screen: at least **20 closed growth trades**, **strictly positive net P&L**,
  and **marked drawdown no greater than 2%**.

Those screen thresholds are a versioned engineering filter, **not a statistically sufficient
sample, firm-profit mandate, risk-limit override or trading certificate**. Passing keeps
`promotion_eligible=false`, and cost/native/demo/statistical evidence remains required.

The SQLite journal lives under `%LOCALAPPDATA%\DustyDragon\research\prospective-plans`.
Each receipt is also exported as `<plan_id>.json`. The plan ID is its payload SHA-256. The UI
can copy the receipt for the user to preserve independently before the window begins.
There is no automatic external upload, publication or paid timestamp service.

An identical configuration/window cannot be registered again merely by refreshing its snapshot
timestamp. Different configurations are retained as separate plans, not declared independent tests.
The UI shows the newest 200 plans; older database records remain preserved. There is no deletion UI.

**Saved Future Plans** permits evaluation only after the frozen end. Evaluation rechecks the
exact configuration and native identity. Account balance and snapshot time can refresh, but
simulation starting capital remains the original registered amount; both amounts are labeled.
Code, runtime, broker specification or identity drift blocks reuse. Do not silently migrate a
plan to a newer terminal build or changed code. Retain the invalidated plan and register a new
future window if necessary. This is deliberately strict for the initial proof workflow.

SQLite's immediate transaction and unique attempt key permit only **one evaluation attempt per
plan**, including across desktop processes. A worker crash, cancellation, timeout or failed
acquisition does not release that attempt. The plan and run ID remain available; a failed result
cannot disappear into an automatic retry. Premature or incompatible requests rejected before
claiming do not consume the plan. No implicit resume or automatic timer is implemented.

The four usual completed-run files remain request/bars/report/result. Registered request/report
objects additionally carry the original receipt; the report includes the frozen screen outcome.
The original M103 historical path and complete laboratory numerical results remain unchanged.

### Timestamp and exposure limitations

Registration is based on **the local PC clock**, not an independent timestamp authority. Hashes
detect inconsistent edits, not a privileged same-user rewrite of the clock, database and every
receipt. A manually preserved pre-window receipt supplies a comparison anchor; independent
witnessing is still required before claiming externally proven preregistration.

The software does not claim that the human never examined the window through another tool,
that historical broker data cannot be revised, or that repeated trials have been statistically
corrected. The underlying M103 report retains its historical-exposure limitations. Registering
does not generate forecasts, force sufficient trade counts or guarantee that markets are open.

## Broker cost evidence

The existing bounded 30-day read still separates native signed commission, fee and swap cash
from user simulation assumptions. The extension considers at most **32 position IDs** and
**10,000 full-position history rows**. All native reads remain in the cancellable worker with
its existing deadline; no order sends, order checks, logins or symbol-selection writes are added.

For each candidate, `history_deals_get(position=...)` must match the exact deal fields from the
window query. A balanced recent slice alone is not accepted: older opening fills, truncated
responses, changed values, other symbols or unavailable full-position history exclude the position.
This relies on the native API returning its available position history; it is not an independent
broker statement audit or a guarantee against missing server history.

Supported IN/OUT partial fills must have valid identifiers, unique tickets, finite cash/price/volume,
in-window millisecond times, consistent direction, valid inventory chronology and equal total
opened/closed volume. Reversals, close-by, canceled trade types, over-closes, reused lifecycles,
incomplete identifiers and ambiguous same-millisecond entry/exit ordering are excluded.

For accepted histories the report records opened lots, deal count, signed profit/commission/fee/swap,
their net cash sum, and observed commission/fee charges per round-trip lot. Rebates preserve their
sign. Position references are hashed for correlation; hashing small numeric identifiers is not
strong anonymization. Research artifacts are private and must not be committed to the public repo.

These observations are **not a verified fee schedule**, slippage estimate, historical tariff,
position-price/P&L parity proof or complete account statement. Standalone daily/monthly charges
and other account adjustments are not assigned to positions. Zero or missing evidence cannot
establish free trading. No observed rate is automatically inserted into simulation costs.
Actual fee verification still needs applicable broker/account/symbol documentation and reconciled
native cash evidence. Swaps/fees remain incomplete in the simulation itself.

## QC and user acceptance

New tests cover receipt persistence/tampering, future registration, duplicate prevention,
configuration drift, balance refresh without rebasing capital, concurrent claims, failed-attempt
retention, frozen screen rejection, native full-history matching, partial fills, reversals,
duplicate tickets, rebates, unavailable data, query limits and actual spawned-process artifacts.
Windows CI also constructs the real Tk registration/receipt/saved-plan workflow with fake broker
fixtures. That test is not proof of a native MT5 account or live execution.

After checking a clean worktree and the exact published Windows CI, update to
`carson/m104-prospective-evidence`, run the unittest suite, and launch the existing UI.
Keep MT5 on Demo with Algo Trading off. A normal historical run should retain its old arithmetic
and show the additional cost-evidence status; an empty demo history is a valid unavailable result.

For registration, choose a future holdout start explicitly. For example, **only if registering
before 2026-09-03 00:00 UTC**, history 14 days / holdout 7 days / fixed end
`2026-09-10 00:00` gives a Sep 3–10 holdout. Do not reuse that example after its start.
Supply clearly labeled cost assumptions; do not invent verified broker fees. Use **Freeze**, not
Run, then preserve the receipt. Inspect **Saved Future Plans**: it should show WAITING without
spawning research or placing orders. Do not alter the PC clock to accelerate acceptance.

After the actual end, connect the same environment and evaluate once. Preserve failures as well
as successes. Passing the research screen never unlocks Demo or Live. Next work remains genuine
broker-cost verification, native indicator/tester parity, witnessed prospective evidence and a
separately qualified real-time demo loop.

## Primary references

- [MetaQuotes deal properties](https://www.mql5.com/en/docs/constants/tradingconstants/dealproperties): distinct position, entry/exit, commission, fee, swap and canceled-deal semantics.
- [MetaQuotes deal-history API](https://www.mql5.com/en/docs/python_metatrader5/mt5historydealsget_py): date-window and per-position history queries.
- [Python 3.11 SQLite](https://docs.python.org/3.11/library/sqlite3.html): explicit connection lifetime, parameterized queries and transactions compatible with the supported runtime.
- [SQLite transactions](https://www.sqlite.org/lang_transaction.html): immediate transactions and single-writer behavior for attempt claims.
- [Pinned RD-Agent experiment/workspace source](https://github.com/microsoft/RD-Agent/blob/2c878f9d2453dced35061165786d1f31bbff0ab6/rdagent/core/experiment.py): separate hypotheses, workspaces, results and recovery boundaries.
