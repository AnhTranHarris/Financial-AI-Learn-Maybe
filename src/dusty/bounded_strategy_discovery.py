from __future__ import annotations

"""Workstation-friendly M196.5 strategy-discovery policy.

This layer intentionally stays small. It reuses the certified discovery service,
Strategy Estate builder, and Vibe contractor while adding only the resource
constraints that the native workstation demonstrated it needs:

* one local Ollama generation at a time (the builder is sequential);
* at most four Ollama CPU threads and at most half the visible logical CPUs;
* a 2048-token context floor/ceiling for calls that did not already choose one;
* two reconstruction proposals per discovery pass;
* a smaller Vibe catalog and a bounded provider subprocess timeout;
* deterministic symbol diversification for source proposals that do not name a
  symbol themselves.

No source claim is rewritten: an unspecified source symbol remains unspecified in
the StrategyProposal. Dusty merely narrows the *research universe* supplied to
Ollama for that experiment. The model receives no broker, risk, promotion,
Guardian, or live-trading authority.
"""

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import os
from pathlib import Path
import re
from typing import Iterable

from .ollama_quant_reviewer import Transport, _urllib_transport
from .ollama_strategy_classifier import OllamaStrategyClassifier
from .ollama_strategy_reconstruction import OllamaStrategyReconstructor
from .source_intake import StrategyProposal, deduplicate_proposals
from .strategy_discovery import StrategyDiscoveryConfig
from .strategy_estate import StrategyEstateUpdate
from .strategy_estate_builder import EstatePopulationResult, StrategyEstateBuilder
from .vibe_research_service import VibeResearchContractor


# Put the user's currently exercised broker symbols first, then broaden into a
# conservative liquid research universe. These strings are research identities,
# not aliases: runtime compatibility still requires an exact terminal symbol.
DEFAULT_RESEARCH_SYMBOLS = (
    "EURUSD",
    "XAUUSD",
    "NASUSD",
    "GBPUSD",
    "USDJPY",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "USDCHF",
    "EURJPY",
    "GBPJPY",
    "EURGBP",
    "XAGUSD",
)

OLLAMA_NUM_THREAD_CAP = 4
OLLAMA_DEFAULT_NUM_CTX = 2048
DISCOVERY_CATALOG_LIMIT = 50
DISCOVERY_MAX_RECONSTRUCTIONS = 2
DISCOVERY_PROVIDER_TIMEOUT_SECONDS = 30
_SYMBOL = re.compile(r"^[A-Z0-9._-]{3,32}$")


def _configured_symbols() -> tuple[str, ...]:
    """Optional bounded research-symbol override without adding alias logic."""

    raw = os.environ.get("DUSTY_STRATEGY_SYMBOLS", "").strip()
    if not raw:
        return DEFAULT_RESEARCH_SYMBOLS
    values = tuple(dict.fromkeys(part.strip().upper() for part in raw.split(",") if part.strip()))
    if not values or len(values) > 64 or any(_SYMBOL.fullmatch(value) is None for value in values):
        raise ValueError("DUSTY_STRATEGY_SYMBOLS must be 1..64 comma-separated research symbol names")
    return values


def workstation_discovery_config(*, estate_path: Path | None = None) -> StrategyDiscoveryConfig:
    """Return the smallest resource-bounded policy that still broadens research."""

    base = StrategyDiscoveryConfig.default(estate_path=estate_path)
    return replace(
        base,
        catalog_limit=DISCOVERY_CATALOG_LIMIT,
        max_reconstructions=DISCOVERY_MAX_RECONSTRUCTIONS,
        allowed_symbols=_configured_symbols(),
    )


def ollama_thread_budget() -> int:
    """Reserve at least half the visible logical CPU capacity for the workstation."""

    visible = os.cpu_count() or 2
    return max(1, min(OLLAMA_NUM_THREAD_CAP, visible // 2))


def bounded_ollama_transport(
    method: str,
    url: str,
    payload: dict[str, object] | None,
    timeout: float,
    *,
    delegate: Transport = _urllib_transport,
) -> dict[str, object]:
    """Clamp local inference resources without changing the Ollama service globally.

    Reconstruction already sets ``num_ctx=4096`` and keeps that explicit value.
    The classifier did not previously set a context size, so it could inherit an
    unnecessarily large GUI/server default. Only missing ``num_ctx`` is filled.
    """

    outgoing = payload
    if method.upper() == "POST" and url.rstrip("/").endswith("/api/chat") and payload is not None:
        outgoing = dict(payload)
        options_raw = outgoing.get("options")
        options = dict(options_raw) if isinstance(options_raw, dict) else {}
        options["num_thread"] = ollama_thread_budget()
        options.setdefault("num_ctx", OLLAMA_DEFAULT_NUM_CTX)
        outgoing["options"] = options
    return delegate(method, url, outgoing, timeout)


def bounded_vibe_contractor(vibe_root: Path, work_root: Path) -> VibeResearchContractor:
    """Provider worker timeout bounds a hung website/search call to 30 seconds."""

    return VibeResearchContractor(
        vibe_root,
        work_root,
        timeout_seconds=DISCOVERY_PROVIDER_TIMEOUT_SECONDS,
    )


def bounded_estate_builder() -> "SymbolDiversifyingEstateBuilder":
    transport = bounded_ollama_transport
    return SymbolDiversifyingEstateBuilder(
        StrategyEstateBuilder(
            reconstructor=OllamaStrategyReconstructor(transport=transport),
            classifier=OllamaStrategyClassifier(transport=transport),
        )
    )


class SymbolDiversifyingEstateBuilder:
    """Pin generic hypotheses to one research symbol per Ollama experiment.

    Vibe's alpha catalog often describes a strategy family without claiming a
    particular symbol. Passing the entire symbol universe to a small local model
    makes the prompt/schema larger and permits repeated selection of the same
    asset. This adapter deterministically assigns each such proposal one symbol
    for the current ISO research week, while preserving the immutable source
    proposal unchanged. Multiple proposals in one pass are steered to distinct
    symbols when possible. A failed generic proposal rotates to another symbol in
    a later week instead of monopolising the research frontier forever.
    """

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    def __init__(self, delegate: StrategyEstateBuilder | None = None) -> None:
        self.delegate = delegate or StrategyEstateBuilder()

    @staticmethod
    def _choose_symbol(
        proposal: StrategyProposal,
        universe: tuple[str, ...],
        *,
        week_key: int,
        used: set[str],
    ) -> str:
        seed = sha256(f"{proposal.fingerprint}:{week_key}".encode("utf-8")).digest()
        start = int.from_bytes(seed[:4], "big") % len(universe)
        for offset in range(len(universe)):
            candidate = universe[(start + offset) % len(universe)]
            if candidate not in used:
                return candidate
        return universe[start]

    def populate(
        self,
        proposals: Iterable[StrategyProposal],
        *,
        model_tag: str,
        model_digest: str,
        allowed_symbols: tuple[str, ...],
        allowed_timeframes: tuple[str, ...],
        allowed_features: tuple[str, ...],
        allowed_sessions: tuple[str, ...] = (),
        estate_path: str | Path | None = None,
        created_at: datetime | None = None,
    ) -> EstatePopulationResult:
        now = created_at or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("symbol-diversifying estate time must be timezone-aware")
        now = now.astimezone(timezone.utc)
        universe = tuple(dict.fromkeys(value.strip().upper() for value in allowed_symbols if value.strip()))
        if not universe:
            raise ValueError("symbol-diversifying estate requires a symbol universe")

        rows = []
        latest_update = None
        added_total = 0
        used: set[str] = set()
        week_key = int(now.strftime("%G%V"))

        for proposal in deduplicate_proposals(proposals):
            if proposal.symbols:
                intersection = tuple(value for value in universe if value in {s.upper() for s in proposal.symbols})
                narrowed = intersection or universe
            else:
                selected = self._choose_symbol(proposal, universe, week_key=week_key, used=used)
                used.add(selected)
                narrowed = (selected,)

            result = self.delegate.populate(
                (proposal,),
                model_tag=model_tag,
                model_digest=model_digest,
                allowed_symbols=narrowed,
                allowed_timeframes=allowed_timeframes,
                allowed_features=allowed_features,
                allowed_sessions=allowed_sessions,
                estate_path=estate_path,
                created_at=now,
            )
            rows.extend(result.rows)
            if result.estate_update is not None:
                latest_update = result.estate_update
                added_total += result.estate_update.added

        update = None
        if latest_update is not None:
            update = StrategyEstateUpdate(
                latest_update.path,
                latest_update.sha256,
                added_total,
                latest_update.total,
            )
        return EstatePopulationResult(tuple(rows), update)
