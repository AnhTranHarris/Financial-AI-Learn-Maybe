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
from urllib.parse import urlparse

from .bounded_strategy_discovery import bounded_ollama_transport
from .ollama_quant_reviewer import _urllib_transport
from .ollama_strategy_classifier import OllamaStrategyClassifier
from .ollama_strategy_reconstruction import OllamaStrategyReconstructor
from .strategy_estate import default_strategy_estate_path, load_strategy_estate
from .strategy_estate_builder import StrategyEstateBuilder
from .strategy_seed_proposals import starter_strategy_proposals
from .strategy_taxonomy import quant_title_for_reconstruction


DEFAULT_MODEL = "qwen3:1.7b"
DEFAULT_SYMBOLS = ("EURUSD", "XAUUSD", "NASUSD")
DEFAULT_TIMEFRAMES = ("M15", "M30", "H1")
DEFAULT_MAX_SEEDS = 2
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
# Nonempty by design: older Ollama structured-output backends can reject an
# empty enum even though the post-parser would otherwise accept no sessions.
DEFAULT_SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "LONDON_NY_OVERLAP")


broker_write_authority = False
live_write_authority = False
promotion_authority = False
risk_override_authority = False
guardian_override_authority = False


def _local_ollama_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("strategy estate Ollama endpoint must be localhost HTTP")
    return base_url.rstrip("/")


def installed_model_digest(
    model_tag: str,
    *,
    base_url: str = "http://127.0.0.1:11434",
) -> str:
    model_tag = str(model_tag).strip()
    if not model_tag or "\n" in model_tag or "\r" in model_tag:
        raise ValueError("Ollama model tag invalid")
    endpoint = _local_ollama_url(base_url)
    response = _urllib_transport("GET", f"{endpoint}/api/tags", None, 30.0)
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
    parser.add_argument("--max-seeds", type=int, default=DEFAULT_MAX_SEEDS, help="maximum missing governed seeds to reconstruct in one invocation (1..6)")
    parser.add_argument("--list", action="store_true", help="list the current estate without invoking Ollama")
    return parser


def _print_estate(path: Path) -> int:
    rows = load_strategy_estate(path)
    print(f"Strategy Estate: {path}")
    print(f"Candidates: {len(rows)}")
    for row in rows:
        print(f"- {quant_title_for_reconstruction(row)} [{row.candidate_spec.strategy_id}]")
    return 0


def _missing_seed_proposals(path: Path, *, limit: int):
    existing = load_strategy_estate(path)
    existing_proposal_fingerprints = {row.proposal_fingerprint for row in existing}
    all_seeds = starter_strategy_proposals()
    missing_all = tuple(
        proposal for proposal in all_seeds
        if proposal.fingerprint not in existing_proposal_fingerprints
    )
    return missing_all[:limit], len(all_seeds) - len(missing_all), max(0, len(missing_all) - limit)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    estate = args.estate.expanduser().resolve()

    if args.list:
        return _print_estate(estate)
    if not args.seed_core:
        parser.error("select --seed-core or --list")
    if type(args.max_seeds) is not int or not 1 <= args.max_seeds <= 6:
        parser.error("--max-seeds must be an integer from 1 through 6")

    proposals, skipped_existing, deferred_by_budget = _missing_seed_proposals(estate, limit=args.max_seeds)
    if skipped_existing:
        print(f"Existing governed seed proposals skipped: {skipped_existing}")
    if deferred_by_budget:
        print(f"Missing governed seeds deferred by per-run budget: {deferred_by_budget}")
    if not proposals:
        print("All governed starter proposals are already represented in the Strategy Estate.")
        return _print_estate(estate)

    try:
        endpoint = _local_ollama_url(args.ollama)
        digest = installed_model_digest(args.model, base_url=endpoint)
    except Exception as exc:
        print(f"Ollama model discovery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    builder = StrategyEstateBuilder(
        reconstructor=OllamaStrategyReconstructor(base_url=endpoint, transport=bounded_ollama_transport),
        classifier=OllamaStrategyClassifier(base_url=endpoint, transport=bounded_ollama_transport),
    )
    result = builder.populate(
        proposals,
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
    print(f"Per-run governed-seed budget: {args.max_seeds}")
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
