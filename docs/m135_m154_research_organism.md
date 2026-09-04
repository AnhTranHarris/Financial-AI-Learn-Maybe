# M135-M154 — Dusty Dragon Research Organism

This tranche turns Dusty's already-certified research organs into one restartable research organism. It is intentionally **research-only**. No code in this tranche grants broker-write authority, live trading authority, entry-veto authority, risk override, or Champion promotion authority.

## Certified base

M135 is built on the locally certified M115-M134 research brain. The repaired M135 checkpoint is `529193094fb2b00b508876842d67261cd6ab1870`; GitHub Actions run #477 passed Ubuntu and Windows on Python 3.11 and 3.12, including the full repository suite.

## Milestones

| Milestone | Capability | Proof boundary |
| --- | --- | --- |
| M135 | Integrated Research Cycle | Real MT5-bar contract → PIT → three independent forecast contractors → disagreement → blackboard/checkpoint. No skill certification while using a nominal future clock. |
| M136 | MT5 Research Data Service | Reuses Dusty's read-only MT5 worker, explicit broker-symbol binding, terminal path hashed, no order surface. Existing terminal discovery remains the control-plane source; this milestone does not duplicate it. |
| M137 | Market/Session Clock | Reuses broker `BrokerMarketSchedule`; future forecast timestamps skip scheduled closures and reject schedules captured after historical T. |
| M138 | Historical PIT Campaign Factory | Replays actual completed-bar timestamps and actual later observations. It does not synthesize weekend bars. |
| M139 | Forecast Realization Engine | Realizations require exact symbol/timeframe/as-of/horizon/context binding and an exact target observation. |
| M140 | Provider Skill Memory | Append-only SQLite snapshots of independent provider reliability by provider/model/revision/symbol/timeframe/horizon/regime/session. |
| M141 | Forecast Disagreement Intelligence | Agreement/disagreement is measured as a research feature. It remains explicitly non-authoritative. |
| M142 | Deterministic Quant Scorecard | Statistical dossier is produced before LLM review; no hidden ensemble or trade vote. |
| M143 | Vibe Strategy Factory | Certified bounded Vibe research evidence becomes research-only strategy genomes. No Vibe agent loop or broker tools. |
| M144 | External Strategy Genome Intake | Approved/user-supplied external proposals preserve provenance. Claimed performance is not fitness or authority. |
| M145 | User Strategy Lab | Carson-refined user theses are preserved as immutable `UserStrategyIntent` records. |
| M146 | Strategy Intent Compiler | Structured intent → genome with locked rules and unresolved research variables. The compiler performs no NLP guessing. |
| M147 | Constraint & Experiment Resolver | Only RESEARCHABLE variables may change; variants are bounded to one or two variables. LOCKED/FORBIDDEN constraints fail closed. |
| M148 | Dusty In-House Strategy Composer | Dusty creates descendants from measured hypotheses/lessons while preserving parent ancestry. Parent/Champion is never rewritten. |
| M149 | A1 Edge Discovery Factory | Existing A1 gate must prove positive OOS expectancy, samples, parameter stability, and constitutional compliance. |
| M150 | A2 Quant Profitability Lab | A1 survivors must also survive cost stress, drawdown limits, and walk-forward transfer. |
| M151 | A3 Profit-Velocity Lab | A2 survivors may optimize robust research efficiency and MFE capture, without violating entry-frequency/risk constraints. |
| M152 | Failure Intelligence & Strategy Redesign | Failure diagnosis creates bounded redesign experiments tied to evidence; causal proof is explicit and defaults false. |
| M153 | Local Qwen Quant Reviewer | Direct localhost Ollama adapter verifies exact model digest, requests schema-constrained JSON at temperature 0, exposes no tools, and degrades to UNAVAILABLE on faults/drift. |
| M154 | Autonomous Research Heartbeat | One authoritative `ResearchOrganism` advances ACQUIRE→FORECAST→SCORE→INTAKE→SCREEN→EXPERIMENT→ATTRIBUTE→REMEMBER→CHECKPOINT→COMPLETE with persistent board payloads and append-only checkpoints; restart resumes from the last successful stage. |

## Permanent strategy constraints

Every user/Dusty strategy genome carries permanent prohibitions against martingale, revenge sizing, stop widening, future leakage, HFT, and scalping. Small capital and weak evidence cause NO TRADE later; they never relax the Constitution.

## User strategy workflow

The normal workflow is:

`User → Carson → reviewed UserStrategyIntent → Dusty Strategy Lab → bounded experiments → A1 → A2 → A3`

The user may speak naturally to Carson. Carson resolves intent with the user before the structured object enters Dusty. Ollama/Qwen is not the primary user strategy intake and cannot silently reinterpret locked thesis components.

## Research genetics used

The ten repositories remain references, not required runtime dependencies:

1. Kronos — financial forecasting provider patterns.
2. Amazon Chronos-2 — probabilistic forecast evidence.
3. Salesforce Uni2TS/Moirai — rolling/distributional evaluation patterns.
4. Vibe-Trading — bounded finance/strategy research surfaces.
5. Microsoft Qlib — data/model/experiment/history separation.
6. Microsoft RD-Agent — propose/evaluate/feedback/knowledge loop.
7. TradingAgents — constructive versus adversarial reasoning.
8. ai-hedge-fund — one authoritative run-cycle pattern.
9. Conway Automaton — heartbeat/durable state/resource/recovery patterns.
10. Microsoft Agent Framework — checkpoint/resume/observability patterns.

Dusty does not merge their dependency trees into the sacred core.

## Research findings applied

- MetaTrader 5 bar timestamps represent bar opening times. Dusty therefore continues to treat a historical bar as fully knowable only when a later bar proves completion.
- Broker/symbol sessions are used for market-time research instead of assuming every elapsed 15 minutes is a tradable observation.
- Checkpoint state is treated as a trust boundary; M154 persists both the checkpoint and the content-addressed blackboard payload required to resume deterministically.
- Ollama structured outputs are used only as a schema-constrained reviewer surface; exact local model digest is verified before review.
- Qlib/RD-Agent patterns reinforce preserving model/research history and separating iterative research feedback from genuine holdout evidence.
- Community reports about overfitting, OOS contamination, parameter fragility, and resource exhaustion are treated as adversarial hypotheses, not proof.

## QC

`tests/test_m136_m154_research_organism.py` maps each milestone to at least one invariant-focused test. CI also runs the repaired M135 tests and the complete legacy suite on Ubuntu/Windows Python 3.11/3.12.

`tools/validate_m135_m154.ps1` performs the supported Windows software validation and writes permanent JSON/log evidence under `%LOCALAPPDATA%\DustyDragon\validation`.

The local validator deliberately does **not** claim real MT5/provider/Qwen hardware proof. Those are separate local hardware gates after software CI is green.

## Non-claims

M154 does not prove profitable trading, model skill, native MT5 execution parity, Demo qualification, or live readiness. It proves that Dusty now has a bounded, persistent research organism capable of orchestrating those research activities without giving research components operational trading authority.
