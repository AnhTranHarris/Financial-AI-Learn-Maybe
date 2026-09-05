# M157 — Strategy Genome Compiler v2

## Scope

M157 compiles the reviewed M143-M152 `StrategyGenome` into one strict, author-neutral strategy intermediate representation. User/Carson, Vibe, external, and Dusty-created ideas enter the same compiler. The compiler does not infer missing rules from prose, place orders, grant live authority, or promote Champions.

The compiled representation exists so later evolution, scheduling, native MT5 execution, caching, and artifact storage can all operate on the same typed strategy semantics.

## Research basis

The design intentionally borrows patterns rather than whole dependency trees:

- Dusty's M143-M152 layer already provides origin, ancestry, LOCKED/RESEARCHABLE/FORBIDDEN constraints, bounded descendants, and source provenance. M157 preserves those contracts instead of rebuilding them.
- M156 provides the exact feature identities, dependency closure, warm-up, point-in-time eligibility, repaint status, and resolved feature fingerprints. M157 binds strategy clauses to those identities.
- Microsoft Qlib separates strategy decision generation from the executor that later consumes the decision. Dusty therefore keeps M157 strategy semantics independent from MT5 broker execution.
- AI Hedge Fund treats mandates/specifications as data and rejects unknown configuration fields rather than silently accepting typos. Dusty's compiler likewise accepts only explicitly declared genome sources.
- Vibe-Trading's current strategy-development/discovery machinery preserves hypothesis and reproducible run identities and treats validation as a staged research lifecycle rather than a marketing-performance parser.
- Microsoft RD-Agent's quantitative loop turns an explicit hypothesis into an experiment and then uses result feedback for the next bounded R&D action. M157 therefore keeps unresolved strategy questions explicit instead of allowing the compiler to guess an answer.
- Microsoft Agent Framework demonstrates why explicit inspectable workflow structure is preferable when deterministic ordering matters, while its checkpoint model reinforces keeping durable state separate from free-form agent conversation.
- MetaQuotes Strategy Tester requires explicit Expert Advisor inputs and optimization ranges. M157 therefore creates stable typed clauses/parameters that M161 can later package deterministically for native testing.

External projects remain research references, not mandatory Dusty runtime dependencies.

## Typed clause vocabulary

M157 defines the following strategy clause families:

- `universe`
- `context`
- `regime`
- `setup`
- `trigger`
- `invalidation`
- `management`
- `exit`
- `session`
- `forecast`
- `risk`

A compiled strategy must contain at least a trigger, exit, and risk clause. Those clauses may remain explicitly unresolved when the source genome says research is still required.

## Source binding

Every clause binds to a key already declared by the source genome.

The compiler may consume:

1. a resolved genome rule;
2. a locked constraint;
3. a researchable unresolved constraint.

It may not:

- invent a new source key;
- rewrite a resolved or locked value;
- turn a forbidden policy into a positive strategy rule;
- claim a researchable unknown is resolved before a child genome actually carries the resolved rule.

This ensures the compiler is a type-checking boundary, not a hidden strategy generator.

## Feature binding

Feature references use exact `name@version` identities from a validated, frozen M156 registry.

Resolved decision-affecting clauses may only consume feature dependency closures that are:

- lookahead-free;
- stable/non-repainting;
- availability-known.

A resolved entry trigger must reference at least one versioned M156 feature. This prevents a free-form string such as `RSI` from becoming executable research semantics without saying which RSI definition the experiment actually used.

Unresolved research clauses may name prospective registered features, but those clauses cannot make the compiled strategy fully specified until a bounded descendant resolves the source rule.

## Two strategy identities

M157 intentionally separates two hashes.

### Execution fingerprint

Hashes evidence-producing semantics:

- symbols
- timeframes
- typed clauses
- clause parameters
- exact resolved feature fingerprints
- explicit no-authority contract

Authorship, title, and marketing provenance are excluded. Two independently sourced strategies with truly identical semantics can therefore share later M163 execution evidence.

### Research-record fingerprint

Adds:

- source genome identity
- source family identity
- origin
- source provenance
- ancestry
- generation
- mutation/constraint policy

The Artifact Vault can therefore preserve who/what produced a strategy without forcing M163 to recompute identical market evidence merely because the idea arrived from a different source.

## Legacy unresolved-alias repair

The audit uncovered a pre-M157 compatibility edge case: external/Vibe genomes historically represent an unresolved name such as `entry_logic` while the generated research constraint is `unresolved.entry_logic`. A bounded descendant can carry the resolved prefixed rule while retaining the older bare unresolved marker.

M157 repairs this only at the compilation boundary: when an actual resolved rule exists, the rule is treated as semantic truth and the stale alias does not falsely keep the compiled strategy unresolved. The original historical genome remains immutable for provenance. This is safer than rewriting old research records in place.

## Readiness

`fully_specified` means every required compiled clause has a resolved source rule.

`manifest_ready` additionally requires at least one bound M156 feature so an M155 execution manifest can carry exact feature provenance.

Neither property grants trading authority.

M157 permanently reports:

- broker write authority = false
- risk override authority = false
- promotion authority = false

## M157 certification gates

M157 is accepted only when tests prove:

- exact M156 feature binding
- deterministic identity independent of clause input order
- locked/resolved rules cannot be silently rewritten
- researchable unknowns remain unresolved
- premature resolution is rejected
- future/repainting/unknown feature dependencies fail closed for resolved decision clauses
- legacy unresolved aliases are safely repaired at the compiler boundary
- forbidden policies cannot become positive clauses
- trigger/exit/risk families are required
- explicit symbol/timeframe is required
- semantically identical strategies share execution identity while retaining different provenance records
- unfrozen feature registries cannot become compiler dependencies
- no operational authority is introduced

The dedicated gate must pass on Python 3.11 and 3.12 on both Ubuntu and Windows, followed by the complete repository CI on the exact head.

## Next milestone

M158 will consume this typed IR to create controlled descendants. It may mutate only source fields that remain `RESEARCHABLE`, must explain every semantic change, preserve the parent, and distinguish research failure from infrastructure failure before creating any Challenger.
