# M196.5 — Trading Skills Library, Strategy Reconstruction & AUTO Routing

## Purpose

M196.5 is an off-cycle deployment-completeness checkpoint between certified M196 and official M197. It does **not** renumber the M195–M204 production roadmap.

It connects the research organism, immutable Champion custody, and the basic Windows strategy selector without creating a second strategy compiler, promotion system, risk system, or execution engine.

The target lifecycle is:

`source theory -> explicit reconstruction -> manual backtest -> quant qualification -> Demo evidence -> Trading Skill -> AUTO selection -> downstream governance -> trade or abstain`

A source theory may originate from an approved website, Vibe, the user/Carson workflow, or Dusty's own research. Source origin never grants quantitative authority.

## Reconstruction law

A website can create a proposal, never a Champion.

An incomplete source may still become an executable **research hypothesis**. Every reconstructed rule is labelled either:

- `SOURCE_DECLARED` — exactly present in the archived source proposal; or
- `RESEARCH_HYPOTHESIS` — supplied by a deterministic translator, Ollama, Carson/user review, or Dusty research for falsification.

Unknown source rules remain recorded as unknown even when the candidate fills them with hypotheses. Dusty must never rewrite "we hypothesized this rule" into "the source used this rule."

Reconstructed strategies continue to use the existing `StrategySpecV2` execution semantics and existing eligibility constitution. M196.5 cannot bypass the minimum signal/horizon, HFT/scalping, martingale, or other strategy restrictions.

## Ollama / LLM boundary

Structured output is useful transport, not truth. LLM output has zero broker, promotion, risk, Guardian, or capital-allocation authority. Every reconstruction must be post-validated by deterministic Dusty types.

M196.5 deliberately adds no agent framework and no LLM trade-selection agent.

## PC strategy library transport

The existing basic UI already owns terminal, symbol, strategy, and mode selection. The existing local research runtime already owns bounded read-only MT5 backtests.

M196.5 therefore does not duplicate either subsystem.

The installed `dusty-dragon` launcher gains an optional `--strategy-library` argument. A reconstruction library is:

1. serialized as strict JSON;
2. SHA-256 pinned by exact bytes;
3. validated before display;
4. exposed to the existing UI as temporary **metadata** catalog rows; and
5. independently resolved again by the existing research package resolver.

The path and digest are inherited by the fresh Windows worker process. If the file changes after the digest is frozen, resolution fails closed.

An ordinary `--catalog` JSON file remains metadata-only and cannot execute a reconstructed strategy.

Trading Skills are intentionally **not** accepted from a self-described JSON snapshot. Their lifecycle state must be projected from authoritative M185/M194 evidence rather than trusted from a portable file.

## Trading Skill

A `TradingSkill` is an immutable, non-authoritative projection over one frozen Champion and its certified evidence identities. It retains at least:

- Champion/deployment/strategy/family identity;
- Champion source commit;
- lifecycle state;
- compatible symbols/timeframes;
- selection and robustness evidence;
- M184 evidence when the Champion uses forecasting; and
- M194 evidence only when accompanied by matching `REAL_DEMO_RUNTIME` evidence.

Lifecycle projection is conservative:

- ACTIVE without real M194 proof -> `BACKTEST_CERTIFIED`
- ACTIVE with matching real M194 proof -> `DEMO_CERTIFIED`
- SUSPENDED -> `RESTRICTED`
- RETIRED/SUPERSEDED -> `RETIRED`

M196.5 creates no `LIVE_ELIGIBLE` state. Actual M194 operational evidence remains pending until it is produced by the target Coinexx Demo workstation.

## AUTO router

The AUTO router is deterministic and advisory. It does not invent market-regime confidence or make an LLM vote.

Upstream evidence supplies a context-specific competency containing:

- exact skill identity;
- symbol;
- timeframe;
- session;
- regime;
- explicit eligibility;
- rank score;
- evidence fingerprint; and
- validity interval.

M196.5 uses the score only to order **already eligible** ACTIVE skills.

Rules:

- wrong symbol/timeframe/session/regime -> ignore;
- future or expired competency -> ignore;
- suspended/retired skill -> ignore;
- Demo mode -> real Demo-qualified skill required;
- no surviving skill -> `NO_MATCH` / abstain;
- equal highest scores -> `AMBIGUOUS` / abstain;
- Live non-shadow mode -> `LIVE_LOCKED`;
- Live shadow evaluation may consider only Demo-qualified skills.

A selected skill still must pass:

`Guardian -> M195 Portfolio Risk -> M196 Concentration -> M197 Feasibility`

M197 is referenced only as the next mandatory gate; M196.5 implements none of M197's minimum-lot, margin, or small-account economics.

## Authority boundary

M196.5 has no authority to:

- call `order_send()`;
- import an MT5 execution adapter;
- authorize broker or live writes;
- promote a Champion;
- alter M195 risk capacity;
- override M196 concentration;
- override Guardian;
- allocate capital; or
- manufacture M194 operational evidence.

## Reference architecture lessons

M196.5 uses patterns, not wholesale dependencies:

- Qlib: strategy selection remains separate from execution/account state.
- RD-Agent: reconstruction is hypothesis -> experiment -> feedback, not one-shot LLM truth.
- Vibe-Trading: strategy/evidence storage is staged and evidence-gated.
- TradingAgents: adversarial reasoning is useful, but agent output is not deterministic authority.
- AI Hedge Fund: deterministic risk limits remain outside LLM control.
- Conway Automaton: durable state/policy concepts without importing its runtime.
- Microsoft Agent Framework: the application owns routing/checkpoint state.
- Kronos, Chronos-2, Moirai: forecast evidence providers only; never skill-routing authority.

## Certification standard

M196.5 is software-certified only after:

- reconstruction provenance tests;
- hostile/invalid snapshot tests;
- fresh-process worker transport test;
- UI projection tests;
- synthetic-Demo-proof rejection tests;
- AUTO abstention/lifecycle/context tests;
- static authority-boundary checks;
- full M195/M196 regression; and
- every repository exact-head CI check completes successfully on the same SHA.
