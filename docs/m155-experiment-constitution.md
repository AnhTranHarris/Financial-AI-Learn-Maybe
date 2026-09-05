# M155 — Experiment Constitution & Immutable Manifest

## Purpose

M155 defines what a Dusty Dragon experiment **is** before any worker is allowed to execute it. Every experiment begins as an immutable scientific contract. If any scientific input changes, Dusty creates a new manifest rather than editing the old one.

The design follows the user-approved Experiment Factory principle: complexity is justified when it eliminates ambiguity, duplicated computation, unreproducible research, contamination, resource waste, or unsafe autonomy.

## Scientific contract

`ExperimentManifest` records the full research intent and execution boundary:

- experiment display ID
- hypothesis ID and hypothesis text
- origin (`user_carson`, `vibe`, `external`, or `dusty`)
- proposal, strategy, variant and research-context fingerprints
- strategy ancestry and source provenance
- parent manifest lineage
- exact software Git commit
- dataset SHA-256
- versioned feature references and fingerprints
- broker profile and cost-model fingerprints
- account currency, initial balance, leverage and execution model
- deterministic seed
- labeled historical time windows
- symbols and timeframes
- research school and fidelity
- A1/A2/A3 evaluation policy
- required metrics, minimum trade count, walk-forward/cost-stress requirements
- risk-policy fingerprint and explicit risk assumptions
- requested compute class/budget
- expected artifacts
- permanent research-only authority

A manifest cannot authorize broker writes, risk override, entry veto, or Champion promotion.

## Three identities, three jobs

M155 deliberately separates three cryptographic identities.

### `execution_fingerprint`

Hashes fields that can change produced quantitative evidence. It is designed for M163 result-cache reuse.

Changing data, strategy/variant, research context, software commit, feature versions, broker economics, seed, windows, evaluation policy or risk assumptions creates a new execution fingerprint.

Changing only the hypothesis wording or requested hardware budget does **not** force a new execution identity.

### `fingerprint`

Hashes the scientific manifest: hypothesis, provenance, ancestry, execution identity, compute request and expected outputs. This is the immutable contract identity used to bind the queue to the research record.

### `record_fingerprint`

Hashes the manifest fingerprint plus the human-facing experiment ID and creation timestamp. M164 can use this as the append-only ledger-record identity while still recognizing duplicate scientific or execution content.

This separation prevents both false recomputation and false equivalence.

## Queue binding

`ExperimentManifest.to_queue_spec()` creates a durable M155 queue job only for symbols/timeframes declared by the manifest.

The resulting `ExperimentJobSpec.context_fingerprint` is the manifest fingerprint. Therefore the queue cannot silently run a job that has drifted away from the scientific contract.

Infrastructure retry keeps the same immutable manifest. A research-driven redesign later creates a **new** child manifest with explicit parent lineage.

## Reproducibility rule

Years later, Dusty must be able to answer:

> What exact hypothesis, strategy, data, software, features, broker assumptions, seed, windows, evaluation rules, risk assumptions and compute request produced this evidence?

If that question cannot be answered from persisted fingerprints and artifacts, the experiment is not admissible evidence.

## Research-failure vs infrastructure-failure rule

M155 establishes the boundary used by M158-M160:

- **Infrastructure failure**: retry the exact same immutable manifest after recovery.
- **Research failure**: preserve evidence, diagnose mechanism, and only then create a bounded child manifest if a defensible research change exists.

A provider crash, MT5 timeout, missing history, SQLite fault, OOM or resource shortage can never be recorded as a strategy failure.

## Content-addressed rule

The following objects are expected to become content-addressed across M155-M164:

- dataset
- feature
- strategy/genome
- experiment manifest
- execution identity
- provider/forecast evidence
- MT5 tester result
- scorecard
- failure lesson
- artifact-vault record

Any material input change produces a new identity. No silent mutation is allowed.

## Acceptance gates

M155 constitution tests must prove:

- semantic ordering does not change fingerprints
- real execution changes create new evidence identities
- hypothesis/provenance changes can preserve reusable execution identity while creating a new scientific manifest
- compute-budget changes do not poison the evidence cache identity
- human display ID/timestamp do not defeat scientific deduplication
- queue jobs are bound to the manifest fingerprint
- undeclared symbol/timeframe bindings fail closed
- malformed feature/window/fingerprint inputs fail closed
- any attempt to grant operational trading authority is rejected

The existing durable queue remains part of M155 as a supporting runtime and must keep all of its prior lease/retry/integrity gates green.
