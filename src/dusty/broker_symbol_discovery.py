from __future__ import annotations

"""Broker-aware, bounded extension for M196.5 strategy discovery.

The PC terminal already exposes a read-only broker symbol inventory. This module
uses that inventory only as a research universe. It rotates through a small
number of exact broker symbols per scan, performs sequential website scouting
through the existing isolated Vibe contractor, and constrains Ollama strategy
reconstruction to one exact target symbol at a time.

Website search output remains an untrusted lead. It is never converted directly
into executable rules. Ollama still receives only governed StrategyProposals
from the existing source-intake path. Nothing here has MT5 write, broker,
promotion, sizing, Guardian, or live authority.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Iterable

from .source_intake import StrategyProposal
from .strategy_discovery import (
    DiscoveryMode,
    DiscoveryTrigger,
    StrategyDiscoveryResult,
    StrategyDiscoveryService,
)
from .strategy_estate import load_strategy_estate
from .strategy_estate_builder import EstatePopulationResult


SYMBOL_SCAN_STATE_SCHEMA = "dusty-broker-symbol-scan-state-v1"
SYMBOL_SCAN_REPORT_SCHEMA = "dusty-broker-symbol-scan-report-v1"
MAX_BROKER_SYMBOLS = 256
MAX_SYMBOL_WEB_QUERIES_NEW = 3
MAX_SYMBOL_WEB_QUERIES_BOTH = 2
MAX_WEB_RESULTS_PER_SYMBOL = 3
MAX_OLLAMA_RECONSTRUCTIONS_PER_SCAN = 2
MAX_RESULT_TEXT = 20_000
# MetaQuotes ENUM_SYMBOL_TRADE_MODE_FULL. Until the reconstruction request can
# bind one-sided direction constraints, LONGONLY/SHORTONLY are excluded rather
# than allowing Ollama to hypothesize an inexpressible direction. CLOSEONLY and
# DISABLED are never research candidates for a new-entry strategy.
FULL_TRADE_MODE = 4
TARGET_TAG_PREFIX = "dusty_research_target_symbol:"


@dataclass(frozen=True, slots=True)
class BrokerSymbolScanState:
    universe_sha256: str
    cursor: int = 0
    last_symbols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.universe_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in self.universe_sha256):
            raise ValueError("broker symbol scan state requires SHA-256 universe identity")
        if type(self.cursor) is not int or self.cursor < 0:
            raise ValueError("broker symbol scan cursor must be nonnegative integer")
        if len(set(value.casefold() for value in self.last_symbols)) != len(self.last_symbols):
            raise ValueError("broker symbol scan last-symbol set must be unique")


@dataclass(frozen=True, slots=True)
class BrokerSymbolScanSummary:
    universe_size: int
    symbols_scanned: tuple[str, ...]
    reconstruction_target: str
    web_queries_completed: int
    web_queries_failed: int
    report_path: Path | None
    used_broker_inventory: bool

    def __post_init__(self) -> None:
        if type(self.universe_size) is not int or self.universe_size < 1:
            raise ValueError("broker symbol scan summary requires a nonempty universe")
        if min(self.web_queries_completed, self.web_queries_failed) < 0:
            raise ValueError("broker symbol scan query counts must be nonnegative")
        if self.reconstruction_target and self.reconstruction_target not in self.symbols_scanned:
            raise ValueError("reconstruction target must belong to scanned symbols")


class _SymbolTargetingEstateBuilder:
    """Derive one exact-symbol hypothesis without mutating source attribution.

    A generic Vibe strategy concept is not a claim that the source traded every
    Coinexx symbol. The derived proposal therefore adds an explicit Dusty research
    target tag and exact symbol while preserving the original immutable source
    snapshot and source-declared rules. Its fingerprint is symbol-specific, so a
    concept already studied on EURUSD can still be falsified independently on
    GBPUSD. Repeated scans of the same concept/symbol pair are idempotently skipped
    before Ollama is called.
    """

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    def __init__(self, delegate: Any) -> None:
        self._delegate = delegate
        self.last_missing_count = 0

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
        normalized = tuple(dict.fromkeys(_symbol(value).upper() for value in allowed_symbols))
        if len(normalized) != 1:
            raise ValueError("broker-targeted estate population requires exactly one symbol")
        target = normalized[0]

        targeted: list[StrategyProposal] = []
        for proposal in proposals:
            row = _target_proposal(proposal, target)
            if row is not None:
                targeted.append(row)

        existing = {row.proposal_fingerprint for row in load_strategy_estate(estate_path)}
        missing = tuple(row for row in targeted if row.fingerprint not in existing)
        self.last_missing_count = len(missing)
        if not missing:
            return EstatePopulationResult((), None)

        return self._delegate.populate(
            missing,
            model_tag=model_tag,
            model_digest=model_digest,
            allowed_symbols=(target,),
            allowed_timeframes=allowed_timeframes,
            allowed_features=allowed_features,
            allowed_sessions=allowed_sessions,
            estate_path=estate_path,
            created_at=created_at,
        )


class BrokerAwareStrategyDiscoveryService(StrategyDiscoveryService):
    """Adds bounded broker-universe rotation without broadening authority.

    The underlying M196.5 discovery service remains the only component that can
    call the Strategy Estate builder. This extension chooses the symbol universe,
    binds generic hypotheses to one exact research target, and does lead-only
    website scouting. All expensive work is sequential.
    """

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        root = self.config.state_path.parent
        self._symbol_state_path = root / "broker-symbol-scan-state.json"
        self._symbol_report_directory = root / "broker-symbol-reports"
        self._application: Any | None = None
        self._last_symbol_summary: BrokerSymbolScanSummary | None = None

    @property
    def last_symbol_summary(self) -> BrokerSymbolScanSummary | None:
        return self._last_symbol_summary

    def bind_application(self, application: Any) -> None:
        """Bind the already-constructed read-only PC application exactly once."""

        if application is None or not callable(getattr(application, "view", None)):
            raise ValueError("broker-aware strategy discovery requires an application view provider")
        if self._application is not None and self._application is not application:
            raise RuntimeError("broker-aware strategy discovery application already bound")
        self._application = application

    def broker_symbol_universe(self) -> tuple[str, ...]:
        """Return exact fully-tradable broker symbols, else a disconnected fallback.

        Discovery excludes custom, disabled, close-only and one-sided symbols,
        plus economically empty rows. It does not call symbol_select(), subscribe
        to market depth, or mutate Market Watch. The LocalDustyApplication snapshot
        was already obtained read-only. A connected broker inventory that contains
        no eligible full-trade symbol fails closed instead of silently falling back
        to symbols that were not verified against that terminal.
        """

        if self._application is None:
            return _normalize_symbols(self.config.allowed_symbols)
        try:
            view = self._application.view()
            options = tuple(getattr(view, "symbols", ()) or ())
        except Exception:
            return _normalize_symbols(self.config.allowed_symbols)
        if not options:
            return _normalize_symbols(self.config.allowed_symbols)

        selected: list[str] = []
        seen: set[str] = set()
        for row in options:
            try:
                if bool(getattr(row, "custom", False)):
                    continue
                if int(getattr(row, "trade_mode", 0)) != FULL_TRADE_MODE:
                    continue
                if float(getattr(row, "tick_size", 0.0)) <= 0.0:
                    continue
                if float(getattr(row, "volume_min", 0.0)) <= 0.0:
                    continue
                symbol = _symbol(getattr(row, "symbol", ""))
            except (TypeError, ValueError):
                continue
            key = symbol.casefold()
            if key in seen:
                continue
            seen.add(key)
            selected.append(symbol)
            if len(selected) >= MAX_BROKER_SYMBOLS:
                break
        if not selected:
            raise ValueError("connected broker inventory has no eligible full-trade research symbols")
        return tuple(selected)

    def discover(
        self,
        mode: DiscoveryMode = DiscoveryMode.BOTH,
        *,
        trigger: DiscoveryTrigger = DiscoveryTrigger.MANUAL,
        now: datetime | None = None,
    ) -> StrategyDiscoveryResult:
        current = _utc(now or datetime.now(timezone.utc))
        universe = self.broker_symbol_universe()
        used_broker_inventory = self._has_live_broker_inventory()
        scan_limit = _scan_limit(mode)
        symbols_scanned = self._next_symbol_batch(universe, scan_limit) if scan_limit else ()
        reconstruction_target = symbols_scanned[0] if symbols_scanned else ""

        completed, failed, scout_report = self._scout_web_leads(
            symbols_scanned,
            mode=mode,
            attempted_at=current,
            used_broker_inventory=used_broker_inventory,
        )

        # One exact reconstruction target eliminates symbol ambiguity and prevents
        # a broad broker universe from inflating the Ollama schema/context. The
        # next button press / Sunday pass advances the persisted symbol cursor.
        delegate_config = self.config
        if mode in {DiscoveryMode.NEW_STRATEGIES, DiscoveryMode.BOTH} and reconstruction_target:
            delegate_config = replace(
                self.config,
                allowed_symbols=(reconstruction_target,),
                max_reconstructions=min(
                    self.config.max_reconstructions,
                    MAX_OLLAMA_RECONSTRUCTIONS_PER_SCAN,
                ),
            )

        targeting_builder = _SymbolTargetingEstateBuilder(self._builder)
        delegate = StrategyDiscoveryService(
            delegate_config,
            contractor_factory=self._contractor_factory,
            builder=targeting_builder,
            digest_resolver=self._digest_resolver,
        )
        result = delegate.discover(mode, trigger=trigger, now=current)
        if mode in {DiscoveryMode.NEW_STRATEGIES, DiscoveryMode.BOTH} and reconstruction_target:
            result = replace(
                result,
                new_single_symbol_candidates=targeting_builder.last_missing_count,
            )
        self._last_symbol_summary = BrokerSymbolScanSummary(
            universe_size=len(universe),
            symbols_scanned=symbols_scanned,
            reconstruction_target=reconstruction_target,
            web_queries_completed=completed,
            web_queries_failed=failed,
            report_path=scout_report,
            used_broker_inventory=used_broker_inventory,
        )
        return result

    def _has_live_broker_inventory(self) -> bool:
        if self._application is None:
            return False
        try:
            return bool(tuple(getattr(self._application.view(), "symbols", ()) or ()))
        except Exception:
            return False

    def _next_symbol_batch(self, universe: tuple[str, ...], limit: int) -> tuple[str, ...]:
        if limit <= 0:
            return ()
        digest = _universe_sha(universe)
        state = _load_scan_state(self._symbol_state_path, digest)
        start = state.cursor
        if start >= len(universe):
            start = 0
        # Do not wrap inside one batch. This means each broker symbol is scouted
        # at most once per rotation cycle even when the batch size does not divide
        # the universe size (for example 4 symbols with a batch size of 3).
        stop = min(start + limit, len(universe))
        batch = universe[start:stop]
        next_cursor = 0 if stop >= len(universe) else stop
        _write_scan_state(
            self._symbol_state_path,
            BrokerSymbolScanState(digest, next_cursor, batch),
        )
        return batch

    def _scout_web_leads(
        self,
        symbols: tuple[str, ...],
        *,
        mode: DiscoveryMode,
        attempted_at: datetime,
        used_broker_inventory: bool,
    ) -> tuple[int, int, Path | None]:
        if not symbols or mode is DiscoveryMode.CROSS_SYMBOL:
            return 0, 0, None

        contractor = self._contractor_factory(
            self.config.vibe_root.resolve(),
            self.config.work_root.resolve(),
        )
        evidence_rows: list[dict[str, object]] = []
        completed = 0
        failed = 0
        consecutive_failures = 0

        for symbol in symbols:
            # One search per symbol. We intentionally do not call read_url here:
            # website snippets are leads only until source policy/review admits a
            # source. This prevents N search results from becoming N page fetches
            # and N Ollama jobs.
            query = f"{symbol} trading strategy breakout reversal momentum pullback mean reversion session"
            result = contractor.invoke(
                "web_search",
                {"query": query, "max_results": MAX_WEB_RESULTS_PER_SYMBOL},
            )
            if not result.available or result.evidence is None:
                failed += 1
                consecutive_failures += 1
                evidence_rows.append(
                    {
                        "symbol": symbol,
                        "query": query,
                        "status": "unavailable",
                        "error": _bounded(getattr(result, "error", "")),
                    }
                )
                # A dead search backend should not be hammered for every broker
                # symbol in the same pass.
                if consecutive_failures >= 1:
                    break
                continue

            consecutive_failures = 0
            completed += 1
            evidence = result.evidence
            evidence_rows.append(
                {
                    "symbol": symbol,
                    "query": query,
                    "status": "available",
                    "tool": evidence.tool,
                    "fingerprint": evidence.fingerprint,
                    "surface_sha256": evidence.surface_sha256,
                    "request_sha256": evidence.request_sha256,
                    "response_sha256": evidence.response_sha256,
                    "result_text": evidence.result_text[:MAX_RESULT_TEXT],
                }
            )

        stamp = attempted_at.strftime("%Y%m%dT%H%M%SZ")
        report_path = self._symbol_report_directory / f"broker-symbol-scout-{stamp}.json"
        _atomic_json(
            report_path,
            {
                "schema": SYMBOL_SCAN_REPORT_SCHEMA,
                "created_at_utc": attempted_at.isoformat(),
                "mode": mode.value,
                "used_broker_inventory": used_broker_inventory,
                "symbols_requested": list(symbols),
                "query_budget": {
                    "symbol_queries": len(symbols),
                    "results_per_query": MAX_WEB_RESULTS_PER_SYMBOL,
                    "ollama_reconstructions": MAX_OLLAMA_RECONSTRUCTIONS_PER_SCAN,
                    "parallel_requests": 1,
                },
                "policy": (
                    "Website search output is an untrusted research lead only. Dusty does not call read_url for every "
                    "result and does not send arbitrary web snippets directly to Ollama. Ollama reconstruction remains "
                    "restricted to governed StrategyProposals admitted through the existing source-intake boundary."
                ),
                "evidence": evidence_rows,
                "authority": {
                    "broker_write": False,
                    "live_write": False,
                    "promotion": False,
                    "risk_override": False,
                    "guardian_override": False,
                },
            },
        )
        return completed, failed, report_path.resolve()


def _target_proposal(proposal: StrategyProposal, target: str) -> StrategyProposal | None:
    target = _symbol(target).upper()
    source_symbols = {value.strip().upper() for value in proposal.symbols if value.strip()}
    if source_symbols and target not in source_symbols:
        return None

    target_tags = tuple(tag for tag in proposal.tags if tag.startswith(TARGET_TAG_PREFIX))
    expected_tag = TARGET_TAG_PREFIX + target
    if target_tags and target_tags != (expected_tag,):
        return None
    tags = proposal.tags if expected_tag in proposal.tags else (*proposal.tags, expected_tag)
    target_digest = sha256(target.encode("utf-8")).hexdigest()
    return replace(
        proposal,
        proposal_id=f"{proposal.proposal_id}:target-sha256:{target_digest}",
        symbols=(target,),
        tags=tags,
    )


def _scan_limit(mode: DiscoveryMode) -> int:
    if mode is DiscoveryMode.NEW_STRATEGIES:
        return MAX_SYMBOL_WEB_QUERIES_NEW
    if mode is DiscoveryMode.BOTH:
        return MAX_SYMBOL_WEB_QUERIES_BOTH
    return 0


def _symbol(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("broker symbol must be string")
    rendered = value.strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > 64:
        raise ValueError("broker symbol identity invalid")
    return rendered


def _normalize_symbols(values: tuple[str, ...]) -> tuple[str, ...]:
    rows: list[str] = []
    seen: set[str] = set()
    for value in values:
        symbol = _symbol(value)
        key = symbol.casefold()
        if key in seen:
            continue
        seen.add(key)
        rows.append(symbol)
        if len(rows) >= MAX_BROKER_SYMBOLS:
            break
    if not rows:
        raise ValueError("broker symbol universe cannot be empty")
    return tuple(rows)


def _universe_sha(symbols: tuple[str, ...]) -> str:
    payload = json.dumps(list(symbols), ensure_ascii=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _load_scan_state(path: Path, universe_sha256: str) -> BrokerSymbolScanState:
    if not path.exists():
        return BrokerSymbolScanState(universe_sha256)
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = {"schema", "universe_sha256", "cursor", "last_symbols"}
    if not isinstance(raw, dict) or set(raw) != expected or raw.get("schema") != SYMBOL_SCAN_STATE_SCHEMA:
        raise ValueError("broker symbol scan state schema mismatch")
    stored_sha = str(raw.get("universe_sha256", ""))
    if stored_sha != universe_sha256:
        return BrokerSymbolScanState(universe_sha256)
    cursor = raw.get("cursor")
    last_symbols = raw.get("last_symbols")
    if type(cursor) is not int or not isinstance(last_symbols, list) or not all(isinstance(value, str) for value in last_symbols):
        raise ValueError("broker symbol scan state payload invalid")
    return BrokerSymbolScanState(stored_sha, cursor, tuple(last_symbols))


def _write_scan_state(path: Path, state: BrokerSymbolScanState) -> None:
    _atomic_json(
        path,
        {
            "schema": SYMBOL_SCAN_STATE_SCHEMA,
            "universe_sha256": state.universe_sha256,
            "cursor": state.cursor,
            "last_symbols": list(state.last_symbols),
        },
    )


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("broker symbol discovery clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _bounded(value: object, maximum: int = 1000) -> str:
    rendered = " ".join(str(value or "").strip().split())
    return rendered[:maximum] or "broker_symbol_scout_unavailable"
