# M196.5 Extension — Persistent Strategy Estate & Quant Titles

## Why this extension exists

The first M196.5 certification proved that an exact reconstructed strategy can move through a SHA-256-pinned library into the Windows strategy selector and resolve again in a fresh research worker. The workstation test then exposed the remaining product gap: the portable test snapshot contained only one synthetic EURUSD reconstruction, so NASUSD and XAUUSD still showed only the two RSI infrastructure benchmarks.

This extension closes that gap without weakening the original M196.5 authority boundary.

## Strategy identity law

A strategy has two deliberately different identities:

- **machine identity** — immutable `strategy_id`, strategy hash, reconstruction fingerprint and evidence lineage;
- **human quant title** — a deterministic display projection used by the PC interface.

The human title is not allowed to become a marketing claim. Ollama cannot output free-form names such as “Ultimate Gold Winner” or “Best Breakout EA.”

Instead, Ollama may classify a reconstruction into Dusty-owned enums:

- archetype: breakout, reversal, momentum, trend continuation, pullback, mean reversion, volatility expansion, session handoff, catalyst runner, range rotation;
- catalyst: none, macro news, tech news, SEC filing, earnings, session open, liquidity event;
- structure: price action, compression, range, trend, Fibonacci, momentum, volatility, gap;
- session profile: unrestricted, Asia, London, New York, Asia→London, Asia→London/NY, London/NY overlap.

Dusty independently validates those enums and renders the final title in deterministic code. Examples include:

- `Asian Compression → London/NY Expansion · EURUSD · M15 · Long`
- `Tech-News Volatility Breakout · NASUSD · M15 · Long`
- `SEC Filing Momentum Runner · US Equities · M15 · Long`
- `Fibonacci Asia→London/NY Continuation · EURUSD · M15 · Long`
- `Exhaustion Reversal · XAUUSD · M15 · Short`

A quant title carries no alpha, promotion, risk, Guardian, Demo, Live, or broker authority.

## Persistent Strategy Estate

Research reconstructions now have a default local estate path:

`%LOCALAPPDATA%\DustyDragon\strategy-estate\reconstructions.json`

The estate is an atomic snapshot of immutable `StrategyReconstruction` records. It reuses the original strict reconstruction-library schema and SHA-256 validation. A duplicate reconstruction is idempotent. A repeated `strategy_id` with a different executable or reconstruction identity is rejected rather than overwritten.

The estate contains **research candidates only**. Trading Skills remain projections of M185/M194 evidence and cannot be self-declared by editing this file.

The normal `dusty-dragon` launcher now auto-loads the estate when it exists. An explicit `--strategy-library` or `--strategy-estate` can still pin a particular snapshot. `--no-strategy-estate` preserves the old benchmark-only behavior for diagnosis.

## Population path

The common population path is:

`StrategyProposal -> bounded Ollama reconstruction -> bounded quant classification -> immutable reconstruction -> Strategy Estate -> PC selector`

Every proposal source enters the same builder after the existing source firewall:

- approved/manual external website research;
- Vibe research;
- user/Carson strategy intent;
- Dusty's own research hypotheses.

No source receives special execution authority.

The first local population command intentionally supplies six Dusty-authored **concept-only research hypotheses** so EURUSD, XAUUSD and NASUSD have meaningful candidates immediately while source-acquisition work continues:

- EURUSD Asian compression/session-handoff continuation;
- EURUSD momentum pullback continuation;
- XAUUSD volatility-expansion breakout;
- XAUUSD momentum exhaustion reversal;
- NASUSD trend-momentum continuation;
- NASUSD range exhaustion reversal.

These are seeds for falsification, not certified alpha and not reconstructions of hidden third-party rules.

The local command is:

`python -m dusty.strategy_estate_cli --seed-core --model qwen3:1.7b`

It discovers the exact installed Ollama model digest, uses only localhost HTTP, supplies the existing executable feature aliases, requires M5+ timeframes, permits only bounded sessions, and records reconstruction/classifier provenance before writing the estate.

## Ollama classification boundary

Structured output is treated as a transport convenience, not a trust boundary. The classifier:

- accepts localhost HTTP only;
- binds the exact installed model digest;
- requests exactly four enum fields;
- rejects missing or extra JSON keys;
- rejects out-of-taxonomy values;
- hashes the raw model response;
- stores classifier model and response provenance as research-hypothesis metadata;
- never accepts a free-form model-supplied display title.

If classification is unavailable, that reconstruction is not registered in the persistent estate. Dusty does not invent a quant classification merely to fill a menu.

## Workstation behavior

Once the estate is populated, normal UI startup no longer needs the temporary PowerShell validation file. The launcher reads the default estate and symbol-filters its candidates.

The intended first-PC result is therefore approximately:

- EURUSD: two or more quant-titled research candidates plus RSI benchmarks;
- XAUUSD: two or more quant-titled research candidates plus RSI benchmarks;
- NASUSD: two or more quant-titled research candidates plus RSI benchmarks.

The exact titles may differ because Ollama performs bounded classification, but the underlying strategy IDs, rules, hashes and provenance remain authoritative.

## Authority boundary remains unchanged

This extension cannot:

- send or modify an MT5 order;
- unlock Demo or Live;
- manufacture M194 evidence;
- promote a Champion;
- change risk fractions;
- bypass M195 or M196;
- override Guardian;
- allocate capital.

M197 remains the next official numbered milestone after this extension is software- and workstation-certified.
