from __future__ import annotations

"""Ephemeral compatibility launcher for Vibe-Trading 0.1.14 + Ollama.

This does not modify the installed Vibe package.  It replaces only the in-memory
Ollama capability record for this one process so Vibe may forward the top-level
``reasoning_effort`` field that current Ollama's OpenAI-compatible endpoint
accepts.  It is diagnostic/research-only and exits with the wrapped Vibe CLI.
"""

import argparse
from dataclasses import replace
from importlib.metadata import version
import os
import sys


EXPECTED_VIBE_VERSION = "0.1.14"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args(argv)

    installed = version("vibe-trading-ai")
    if installed != EXPECTED_VIBE_VERSION:
        print(
            f"VIBE_SHIM_REFUSED: expected vibe-trading-ai=={EXPECTED_VIBE_VERSION}, got {installed}",
            file=sys.stderr,
        )
        return 3

    provider = (os.getenv("LANGCHAIN_PROVIDER") or "").strip().lower()
    if provider != "ollama":
        print(f"VIBE_SHIM_REFUSED: LANGCHAIN_PROVIDER must be ollama, got {provider!r}", file=sys.stderr)
        return 3

    effort = (os.getenv("LANGCHAIN_REASONING_EFFORT") or "").strip().lower()
    if effort != "none":
        print(
            f"VIBE_SHIM_REFUSED: LANGCHAIN_REASONING_EFFORT must be none, got {effort!r}",
            file=sys.stderr,
        )
        return 3

    from src.providers import capabilities as capabilities_module

    current = capabilities_module._PROVIDERS.get("ollama")
    if current is None:
        print("VIBE_SHIM_REFUSED: Vibe Ollama capability record missing", file=sys.stderr)
        return 3

    if not current.top_level_reasoning_effort:
        capabilities_module._PROVIDERS["ollama"] = replace(
            current,
            top_level_reasoning_effort=True,
        )

    from cli import main as vibe_main

    sys.argv = ["vibe-trading", "run", "-p", args.prompt]
    result = vibe_main()
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
