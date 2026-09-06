from __future__ import annotations

"""Local research-only command for populating Dusty's Strategy Estate.

This command deliberately begins with already-governed StrategyProposals. The
initial ``--seed-core`` set is Dusty-authored research, not copied third-party
rules. External/Vibe/User proposals can use the same StrategyEstateBuilder once
their source-intake artifacts exist. No MT5 terminal or broker API is imported.
"""

import argparse
from pathlib import Path
import sys

from .ollama_quant_reviewer import _urllib_transport
from .strategy_estate import default_strategy_estate_path, load_strategy_estate
from .strategy_estate_builder import StrategyEstateBuilder
from .strategy_seed_proposals import starter_strategy_proposals
from .strategy_taxonomy import quant_title_for_reconstruction


DEFAULT_MODEL = "qwen3:1.7b"
DEFAULT_SYMBOLS = ("EURUSD", "XAUUSD", "NASUSD")
DEFAULT_TIMEFRAMES = ("M15", "M30", "H1")
# These names are exact runtime aliases emitted by compute_standard_features.
DEFAULT_FEATURES = (
    "return_1",
    "rsi",
    "sma",
    "ema",
    "atr",
    "spread_points",
    "tick_volume",
)
DEFAULT_SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "LONDON_NY_OVERLAP")


broker_write_authority = False
live_write_authority = False
promotion_authority = False
risk_override_authority = False
guardian_override_authority = False


def installed_model_digest(
    model_tag: str,
    *,
    base_url: str = "http://127.0.0.1:11434",
) -> str:
    response = _urllib_transport("GET", f"{base_url.rstrip('/')}/api/tags", None, 30.0)
    models = response.get("models")
    if not isinstance(models, list):
        raise ValueError("Ollama model list missing")
    matches = []
    for row in models:
        if isinstance(row, dict) and model_tag in {str(row.get("name", "")), str(row.get("model", ""))}:
            matches.append(str(row.get("digest", "")).strip().lower())
    if len(matches) != 1:
        raise ValueError(f"Ollama model {model_tag!r} is missing or ambiguous")
    digest = matches[0]
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("installed Ollama model digest is not SHA-256")
    return digest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Populate Dusty Dragon's research-only Strategy Estate")
    parser.add_argument("--seed-core", action="store_true", help="reconstruct Dusty's governed EURUSD/XAUUSD/NASUSD starter hypotheses")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="exact local Ollama model tag")
    parser.add_argument("--ollama", default="http://127.0.0.1:11434", help="localhost Ollama base URL")
    parser.add_argument("--estate", type=Path, default=default_strategy_estate_path(), help="persistent reconstruction estate path")
    parser.add_argument("--list", action="store_true", help="list the current estate without invoking Ollama")
    return parser


def _print_estate(path: Path) -> int:
    rows = load_strategy_estate(path)
    print(f"Strategy Estate: {path}")
    print(f"Candidates: {len(rows)}")
    for row in rows:
        print(f"- {quant_title_for_reconstruction(row)} [{row.candidate_spec.strategy_id}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    estate = args.estate.expanduser().resolve()

    if args.list:
        return _print_estate(estate)
    if not args.seed_core:
        parser.error("select --seed-core or --list")

    try:
        digest = installed_model_digest(args.model, base_url=args.ollama)
    except Exception as exc:
        print(f"Ollama model discovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    builder = StrategyEstateBuilder(
        reconstructor=__import__("dusty.ollama_strategy_reconstruction", fromlist=["OllamaStrategyReconstructor"]).OllamaStrategyReconstructor(base_url=args.ollama),
        classifier=__import__("dusty.ollama_strategy_classifier", fromlist=["OllamaStrategyClassifier"]).OllamaStrategyClassifier(base_url=args.ollama),
    )
    result = builder.populate(
        starter_strategy_proposals(),
        model_tag=args.model,
        model_digest=digest,
        allowed_symbols=DEFAULT_SYMBOLS,
        allowed_timeframes=DEFAULT_TIMEFRAMES,
        allowed_features=DEFAULT_FEATURES,
        allowed_sessions=DEFAULT_SESSIONS,
        estate_path=estate,
    )

    print(f"Ollama model: {args.model}")
    print(f"Ollama digest: {digest}")
    for row in result.rows:
        suffix = row.reconstruction_fingerprint if row.reconstruction_fingerprint else row.reason
        print(f"{row.status.value}: {row.proposal_id}: {suffix}")

    if result.estate_update is not None:
        print(f"Estate: {result.estate_update.path}")
        print(f"Estate SHA-256: {result.estate_update.sha256}")
        print(f"Added this run: {result.estate_update.added}")
        print(f"Total candidates: {result.estate_update.total}")
    else:
        print("No reconstruction was added; existing estate was not modified.")

    _print_estate(estate)
    return 0 if result.added_count > 0 or bool(load_strategy_estate(estate)) else 3


if __name__ == "__main__":
    raise SystemExit(main())
