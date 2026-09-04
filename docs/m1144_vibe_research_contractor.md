# M114.4 — Vibe-Trading research contractor

## Why this milestone exists

M114.3 proved the local stack itself is healthy:

- Ollama 0.33.2 responds normally.
- `qwen3:1.7b` completes normal chat.
- `qwen3:1.7b` emits valid tool calls through Ollama native and OpenAI-compatible APIs when thinking is disabled.
- Vibe-Trading 0.1.14 provider diagnostics resolve the same Ollama/model configuration.
- Vibe's full `run -p` ReAct agent still fails to finish within the bounded 180-second test, both stock and with an in-memory reasoning-capability shim.

Vibe's own documentation warns that small/distilled models are unreliable for full agent tool use. Rather than increase timeouts or grant a weak local model a huge tool manifest, Dusty now uses the useful Vibe research surface directly.

## Boundary

Dusty launches `src/dusty/vibe_research_worker.py` with Vibe's own isolated Python environment. The worker imports Vibe's `agent/mcp_server.py` research-tool surface, but it does **not** start Vibe's LLM/ReAct agent and it does not start an MCP network listener.

Allowlisted tools are:

- `alpha_zoo`
- `list_strategies`
- `query_strategies`
- `get_strategy_evidence`
- `get_market_data`
- `technical_indicators`
- `pattern_recognition`
- `factor_analysis`
- `backtest`
- `web_search`
- `read_url`

Explicitly absent are shell/background tools, file-write tools, broker connector tools, order placement/cancellation, MT5 control, champion promotion, Guardian bypass, sizing and entry-veto authority.

## Filesystem and environment isolation

File-bearing calls (`backtest`, `pattern_recognition`, `factor_analysis`) are confined to the Dusty-owned contractor work root. Path traversal or absolute paths outside that root fail before the child process starts.

The child environment is an allowlist. Dusty sets an isolated HOME / USERPROFILE / `VIBE_TRADING_HOME`, disables Vibe shell tools, and does not pass LangChain/OpenAI/Anthropic/Ollama/MT5/broker credentials into the Vibe contractor process.

The worker redirects third-party stdout chatter to stderr so stdout remains a single machine-readable JSON response.

## Evidence semantics

Every successful call returns immutable `VibeResearchEvidence` containing:

- Vibe version (exactly `0.1.14` for this milestone)
- tool name
- SHA-256 of Vibe's `mcp_server.py` surface
- SHA-256 of the exact request
- SHA-256 of the exact worker response
- result text
- operational authority flags, permanently false

Vibe research failure becomes `UNAVAILABLE`; it does not disable Dusty's deterministic lane.

## Local smoke

The M114.4 hardware smoke intentionally avoids the LLM agent. It invokes two bundled Alpha Zoo research operations through the contractor:

1. `alpha_zoo(action="health")`
2. `alpha_zoo(action="list_alphas", zoo="alpha101", limit=3)`

This proves that Dusty can execute useful Vibe strategy-research functionality locally without Ollama, MT5, broker credentials or order authority.

## What is not claimed

M114.4 does not claim that `qwen3:1.7b` is suitable to operate Vibe's full agent. It is not treated as a senior quant. Ollama/Qwen remains a separate optional reasoning/review contractor for later milestones.

M114.4 also does not certify any Vibe-discovered strategy as profitable. Vibe output remains research material that must enter Dusty's normal challenger, backtest, walk-forward, demo and promotion gates.
