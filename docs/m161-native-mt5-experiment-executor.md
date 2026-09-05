# M161 — Native MT5 Experiment Executor

M161 turns one immutable Dusty Dragon experiment manifest into one bounded, local MetaTrader 5 Strategy Tester execution and returns cryptographically identified native evidence.

M161 is **research only**. It does not authorize broker writes, live trading, demo promotion, Champion promotion, strategy profitability claims, forecast skill, risk overrides, or entry vetoes.

## Purpose

The experiment factory already has deterministic experiment identity (M155), feature identity (M156), typed strategy genomes (M157), failure-directed evolution (M158), family/novelty/exhaustion controls (M159), and a durable research-value loop governor (M160).

M161 adds the native execution boundary:

```text
immutable M155 manifest
        +
M157 strategy/execution identity
        +
terminal + compiled EA identity
        ↓
content-addressed M161 job package
        ↓
local MT5 Strategy Tester only
        ↓
verified report + native deal ledger
        ↓
M161 evidence object
        ↓
separate evaluator verdict
        ↓
M158 outcome
```

A process exit code is never accepted as trading evidence by itself.

## Native job identity

`compile_native_mt5_job()` binds the native test to:

- M155 manifest fingerprint,
- execution fingerprint,
- experiment ID,
- strategy fingerprint,
- exact `terminal64.exe` SHA-256,
- exact compiled `DustyResearchEA.ex5` SHA-256,
- broker-native symbol,
- timeframe,
- one explicitly named manifest window,
- MT5 tick/model fidelity,
- emulated execution delay,
- manifest deposit/currency/leverage,
- bounded wall-clock budget.

Changing any of the execution-relevant identities produces another deterministic M161 package fingerprint.

## Strategy Tester authority boundary

The generated tester configuration is fixed to:

```ini
[Experts]
AllowLiveTrading=0
AllowDllImport=0
Enabled=1

[Tester]
Optimization=0
UseLocal=1
UseRemote=0
UseCloud=0
Visual=0
ShutdownTerminal=1
```

The MQL5 research EA also refuses initialization outside the Strategy Tester by checking `MQLInfoInteger(MQL_TESTER)`.

Therefore M161 exposes no broker-order surface. The EA's `CTrade` calls exist solely inside a native Strategy Tester simulation.

## Local tester artifact bridge

MetaTrader local testing agents use isolated sandboxes. M161 therefore uses the documented `FILE_COMMON` bridge for the dynamic research manifest and native deal export.

The shared paths are content addressed:

```text
<Terminal Common Files>\DustyDragon\M161\<binding fingerprint>\manifest.csv
<Terminal Common Files>\DustyDragon\M161\<binding fingerprint>\deals.csv
```

Remote and MQL5 Cloud agents are disabled because they do not share the workstation's local common-file namespace.

The M161 design deliberately does not use `#property tester_file`: Dusty's experiment manifests are generated dynamically after compilation, while tester-file resource declarations are a compile-time transport mechanism.

## Expert input SET contract

The generated `ExpertParameters` file is placed in the terminal instance's `MQL5\Profiles\Tester` directory.

String inputs use MetaTrader's plain SET syntax:

```text
InpManifestFile=<value>
InpDealsFile=<value>
InpStrategyHash=<value>
```

Numeric inputs use fixed, non-optimizing SET rows:

```text
InpMagic=<v>||<v>||1||<v>||N
InpDeviationPoints=<v>||<v>||1||<v>||N
```

The final `N` explicitly disables parameter optimization. M161 also sets `Optimization=0` at the tester configuration layer.

## Terminal process isolation

A native test may request `ShutdownTerminal=1`, so Dusty must never run the M161 job through a terminal executable that is already the user's interactive MT5 process.

`PowerShellTerminalIsolationVerifier`:

1. queries `Win32_Process` for `terminal64.exe`,
2. requires readable executable paths,
3. compares normalized full executable paths,
4. fails closed if the exact target terminal binary is already running,
5. also fails closed when terminal process identity cannot be verified.

This follows MetaTrader's own multi-instance model: independent simultaneously running terminals require separate installation directories.

For timeout cleanup, Dusty terminates only the PID it created and that PID's descendants:

```text
taskkill /PID <owned pid> /T /F
```

M161 never uses a broad `taskkill /IM terminal64.exe` operation.

## Native evidence completeness

The upstream research manifest contains already-decided planned trades. Native MT5 therefore measures execution mechanics for those plans; it is not allowed to silently drop a plan and call the remaining output valid research evidence.

Before launch M161 requires:

- the exact seven-column research manifest schema,
- at least one planned `trade_id`,
- non-empty trade IDs,
- unique trade IDs.

After launch M161 requires:

- a non-empty tester report artifact,
- a non-empty native deals artifact,
- a parseable normalized deal ledger,
- the expected strategy fingerprint on every native deal,
- exactly one normalized native trade identity for every planned trade identity,
- no unexpected normalized trade identity.

A missing, extra, partial, malformed, or otherwise unreconciled native trade is a `TESTER_FAIL`, not strategy failure evidence.

## Failure taxonomy

M161 exposes these classifications:

| M161 classification | Meaning | M158 treatment |
| --- | --- | --- |
| `DATA_FAIL` | required market/history data unavailable | infrastructure / DATA |
| `TERMINAL_FAIL` | terminal unavailable, conflicting, or unverifiable | infrastructure / MT5 |
| `TESTER_FAIL` | tester/process artifacts invalid or incomplete | infrastructure / MT5 |
| `RESOURCE_FAIL` | workstation/filesystem resource problem | infrastructure / RESOURCE |
| `TIMEOUT` | bounded tester run exceeded wall-clock budget | infrastructure / PROCESS |
| `CONFIG_FAIL` | immutable binding/hash/configuration invalid | infrastructure / MT5 |
| `STRATEGY_FAIL` | separate evaluator rejected otherwise usable native evidence | research failure |

The executor itself cannot emit `STRATEGY_FAIL`. Only a separate evaluator may attach a strategy verdict after usable evidence exists.

This preserves the M158 rule: infrastructure problems may cause exact retry but cannot create a Challenger mutation.

## Exact retry semantics

Before each execution M161 removes stale output artifacts for the package fingerprint. It then atomically writes:

- the shared research manifest,
- the tester SET file,
- the tester INI file.

The terminal and EA binary SHA-256 values are checked again immediately before launch. This prevents an exact retry from silently running different native code.

## Software QC

M161 is covered by adversarial tests for:

- deterministic package identity,
- undeclared symbol/timeframe rejection,
- path traversal rejection,
- terminal-path conflict rejection before process launch,
- terminal/EA binary drift rejection,
- timeout classification,
- data/infrastructure classification,
- missing report/deals rejection,
- malformed native deals rejection,
- missing planned native trades,
- stale-output deletion before retry,
- separate strategy verdict requirement,
- M158 infrastructure-vs-research mapping,
- exact-PID timeout cleanup contract,
- `FILE_COMMON` MQL5 transport,
- fixed/non-optimizing MetaTrader SET serialization.

The dedicated `.github/workflows/m135_m154.yml` workflow runs M135–M161 gates on:

- Ubuntu / Python 3.11,
- Ubuntu / Python 3.12,
- Windows / Python 3.11,
- Windows / Python 3.12.

The repository-wide CI remains the batch regression gate.

## Hardware certification boundary

Software CI cannot prove the real workstation's:

- Coinexx terminal identity,
- terminal data-directory identity,
- compiled `DustyResearchEA.ex5` binary,
- actual Strategy Tester command-line behavior,
- native EURUSD symbol/history availability,
- report/deal output locations,
- local tester-agent execution.

Those properties require a bounded M161 Windows workstation certification.

The hardware certification must fail closed unless it can identify one intended terminal/data-root pair and verify the terminal is not already running. It must preserve all unrelated MT5 installations/processes and may only terminate a process tree that the certification itself created.

A successful M161 workstation certification establishes only that Dusty can obtain reproducible native MT5 research evidence through this boundary. It does not establish profitability or permission to trade capital.

## Research basis

Design choices were checked against current MetaTrader 5 platform/help and MQL5 documentation/community behavior for:

- command-line `/config` tester startup,
- `[Tester]` configuration parameters,
- local/remote/cloud agent selection,
- tester report generation,
- SET parameter files,
- local tester file sandboxing and `FILE_COMMON`,
- multiple MT5 instances and terminal-directory isolation.

The implementation also preserves Dusty's existing M67 native tester/deal-parity contract instead of introducing a second competing native execution model.
