from __future__ import annotations

"""Bounded strategy-discovery orchestration for the M196.5 PC UI.

The discovery lane is research-only. It may inspect Dusty's already allowlisted
Vibe-Trading research surface, reconstruct previously unseen single-symbol
strategy hypotheses through the bounded Ollama adapters, and archive cross-symbol
web leads for later review. It cannot place orders, promote Champions, loosen
risk, bypass Guardian, or hot-swap a running strategy snapshot.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import json
import os
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError

from .ollama_quant_reviewer import Transport, _urllib_transport
from .source_intake import StrategyProposal, deduplicate_proposals, proposals_from_vibe
from .strategy_estate import default_strategy_estate_path, load_strategy_estate
from .strategy_estate_builder import EstatePopulationResult, StrategyEstateBuilder
from .vibe_research_contract import VibeResearchEvidence, VibeResearchResult
from .vibe_research_service import VibeResearchContractor


STATE_SCHEMA = "dusty-strategy-discovery-state-v1"
REPORT_SCHEMA = "dusty-strategy-discovery-report-v1"
DEFAULT_MODEL_TAG = "qwen3:1.7b"
DEFAULT_SUNDAY_HOUR_CENTRAL = 8
SCHEDULE_RETRY_COOLDOWN = timedelta(hours=1)

DEFAULT_SYMBOLS = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "XAUUSD",
)
DEFAULT_TIMEFRAMES = ("M15", "M30", "H1", "H4")
DEFAULT_FEATURES = (
    "open",
    "high",
    "low",
    "close",
    "return_1",
    "sma",
    "ema",
    "atr",
    "rsi",
    "spread_points",
    "tick_volume",
)
DEFAULT_SESSIONS = ("ASIA", "LONDON", "NEW_YORK")

CROSS_SYMBOL_QUERIES = (
    "cross asset trading strategy forex gold dollar intermarket",
    "currency pairs relative value cointegration pairs trading strategy",
    "lead lag cross symbol strategy forex index yields intermarket",
)
CROSS_SYMBOL_MARKERS = (
    "cross asset",
    "cross-asset",
    "cross symbol",
    "cross-symbol",
    "cross sectional",
    "cross-sectional",
    "pairs trading",
    "pair trading",
    "cointegration",
    "relative value",
    "intermarket",
    "lead lag",
    "lead-lag",
    "correlation",
)


class DiscoveryMode(StrEnum):
    NEW_STRATEGIES = "new_strategies"
    CROSS_SYMBOL = "cross_symbol"
    BOTH = "both"


class DiscoveryTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class DiscoveryStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class StrategyDiscoveryState:
    last_attempt_utc: datetime | None = None
    last_completed_utc: datetime | None = None
    last_status: DiscoveryStatus | None = None
    last_report_path: str = ""

    def __post_init__(self) -> None:
        for value, label in (
            (self.last_attempt_utc, "last attempt"),
            (self.last_completed_utc, "last completed"),
        ):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError(f"strategy discovery {label} must be timezone-aware")
        if self.last_completed_utc is not None and self.last_attempt_utc is None:
            raise ValueError("strategy discovery completed time requires attempt time")
        if self.last_report_path and self.last_status is None:
            raise ValueError("strategy discovery report path requires status")


@dataclass(frozen=True, slots=True)
class StrategyDiscoveryConfig:
    vibe_root: Path
    work_root: Path
    estate_path: Path
    state_path: Path
    report_directory: Path
    model_tag: str = DEFAULT_MODEL_TAG
    catalog_limit: int = 100
    max_reconstructions: int = 6
    allowed_symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    allowed_timeframes: tuple[str, ...] = DEFAULT_TIMEFRAMES
    allowed_features: tuple[str, ...] = DEFAULT_FEATURES
    allowed_sessions: tuple[str, ...] = DEFAULT_SESSIONS

    def __post_init__(self) -> None:
        if not self.model_tag.strip() or "\n" in self.model_tag or "\r" in self.model_tag:
            raise ValueError("strategy discovery model tag invalid")
        if type(self.catalog_limit) is not int or not 1 <= self.catalog_limit <= 500:
            raise ValueError("strategy discovery catalog limit must be 1..500")
        if type(self.max_reconstructions) is not int or not 1 <= self.max_reconstructions <= 25:
            raise ValueError("strategy discovery reconstruction cap must be 1..25")
        for values, label in (
            (self.allowed_symbols, "symbols"),
            (self.allowed_timeframes, "timeframes"),
            (self.allowed_features, "features"),
        ):
            if not values or len(set(values)) != len(values):
                raise ValueError(f"strategy discovery {label} must be unique and nonempty")
        if len(set(self.allowed_sessions)) != len(self.allowed_sessions):
            raise ValueError("strategy discovery sessions must be unique")

    @classmethod
    def default(cls, *, estate_path: Path | None = None) -> "StrategyDiscoveryConfig":
        root = _local_data_root() / "strategy-discovery"
        vibe_override = os.environ.get("DUSTY_VIBE_ROOT", "").strip()
        vibe_root = Path(vibe_override).expanduser() if vibe_override else Path.home() / "DustyProviders" / "VibeTrading"
        model_tag = os.environ.get("DUSTY_STRATEGY_MODEL_TAG", DEFAULT_MODEL_TAG).strip() or DEFAULT_MODEL_TAG
        return cls(
            vibe_root=vibe_root,
            work_root=root / "vibe-work",
            estate_path=(estate_path or default_strategy_estate_path()),
            state_path=root / "state.json",
            report_directory=root / "reports",
            model_tag=model_tag,
        )


@dataclass(frozen=True, slots=True)
class DiscoveryScheduleSnapshot:
    due: bool
    most_recent_slot_utc: datetime
    next_slot_utc: datetime
    last_attempt_utc: datetime | None
    last_completed_utc: datetime | None


@dataclass(frozen=True, slots=True)
class StrategyDiscoveryResult:
    status: DiscoveryStatus
    mode: DiscoveryMode
    trigger: DiscoveryTrigger
    attempted_at_utc: datetime
    proposals_seen: int
    new_single_symbol_candidates: int
    deferred_cross_symbol_candidates: int
    added_to_estate: int
    cross_symbol_web_leads: int
    report_path: Path
    errors: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.attempted_at_utc.tzinfo is None or self.attempted_at_utc.utcoffset() is None:
            raise ValueError("strategy discovery result time must be timezone-aware")
        for value in (
            self.proposals_seen,
            self.new_single_symbol_candidates,
            self.deferred_cross_symbol_candidates,
            self.added_to_estate,
            self.cross_symbol_web_leads,
        ):
            if type(value) is not int or value < 0:
                raise ValueError("strategy discovery counts must be nonnegative integers")
        if self.status is DiscoveryStatus.COMPLETED and self.errors:
            raise ValueError("completed strategy discovery cannot carry errors")
        if self.status is DiscoveryStatus.UNAVAILABLE and not self.errors:
            raise ValueError("unavailable strategy discovery requires errors")

    @property
    def restart_required(self) -> bool:
        return self.added_to_estate > 0

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False


DigestResolver = Callable[[str], str]
ContractorFactory = Callable[[Path, Path], VibeResearchContractor]


def _local_data_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    if local:
        return Path(local) / "DustyDragon"
    data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return root / "DustyDragon"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("strategy discovery clock must be timezone-aware")
    return value.astimezone(timezone.utc)


def _nth_sunday(year: int, month: int, occurrence: int) -> date:
    first = date(year, month, 1)
    days_to_sunday = (6 - first.weekday()) % 7
    return first + timedelta(days=days_to_sunday + 7 * (occurrence - 1))


def _central_dst_date(local_day: date) -> bool:
    """Current U.S. Central DST rule, with no tzdata dependency on Windows.

    Sunday 08:00 local is outside transition ambiguity. Python's zoneinfo is not
    guaranteed to ship an IANA database on Windows, so this bounded scheduler
    uses the post-2007 U.S. rule directly: second Sunday in March through the
    first Sunday in November.
    """

    start = _nth_sunday(local_day.year, 3, 2)
    end = _nth_sunday(local_day.year, 11, 1)
    return start <= local_day < end


def central_slot_utc(local_day: date, hour: int = DEFAULT_SUNDAY_HOUR_CENTRAL) -> datetime:
    if local_day.weekday() != 6:
        raise ValueError("strategy discovery schedule slot must be Sunday")
    if type(hour) is not int or not 0 <= hour <= 23:
        raise ValueError("strategy discovery schedule hour invalid")
    offset_hours = -5 if _central_dst_date(local_day) else -6
    local_zone = timezone(timedelta(hours=offset_hours))
    return datetime(local_day.year, local_day.month, local_day.day, hour, tzinfo=local_zone).astimezone(timezone.utc)


def _central_date_from_utc(now_utc: datetime) -> date:
    now_utc = _utc(now_utc)
    # Determine the Central civil date using both possible offsets; the only
    # potentially different hour is around midnight, far from the 08:00 slot.
    provisional = (now_utc + timedelta(hours=-6)).date()
    offset = -5 if _central_dst_date(provisional) else -6
    return (now_utc + timedelta(hours=offset)).date()


def most_recent_sunday_slot_utc(now: datetime) -> datetime:
    now_utc = _utc(now)
    local_day = _central_date_from_utc(now_utc)
    days_since_sunday = (local_day.weekday() + 1) % 7
    sunday = local_day - timedelta(days=days_since_sunday)
    slot = central_slot_utc(sunday)
    if now_utc < slot:
        sunday -= timedelta(days=7)
        slot = central_slot_utc(sunday)
    return slot


def next_sunday_slot_utc(now: datetime) -> datetime:
    now_utc = _utc(now)
    recent = most_recent_sunday_slot_utc(now_utc)
    next_day = _central_date_from_utc(recent) + timedelta(days=7)
    return central_slot_utc(next_day)


def format_central(value: datetime) -> str:
    value = _utc(value)
    local_day = _central_date_from_utc(value)
    offset = -5 if _central_dst_date(local_day) else -6
    local = value + timedelta(hours=offset)
    zone = "CDT" if offset == -5 else "CST"
    return f"{local:%Y-%m-%d %H:%M} {zone}"


def resolve_local_ollama_model_digest(
    model_tag: str,
    *,
    base_url: str = "http://127.0.0.1:11434",
    transport: Transport = _urllib_transport,
) -> str:
    response = transport("GET", f"{base_url.rstrip('/')}/api/tags", None, 30.0)
    models = response.get("models")
    if not isinstance(models, list):
        raise ValueError("Ollama model list missing")
    matches: list[str] = []
    for row in models:
        if isinstance(row, dict) and model_tag in {str(row.get("name", "")), str(row.get("model", ""))}:
            matches.append(str(row.get("digest", "")).strip().lower())
    if len(matches) != 1:
        raise ValueError("Ollama strategy model tag missing or ambiguous")
    digest = matches[0]
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise ValueError("Ollama strategy model digest is not SHA-256")
    return digest


def _serialize_time(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def _parse_time(value: object, label: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"strategy discovery {label} must be an ISO timestamp or null")
    parsed = datetime.fromisoformat(value)
    return _utc(parsed)


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_discovery_state(path: Path) -> StrategyDiscoveryState:
    if not path.exists():
        return StrategyDiscoveryState()
    raw = json.loads(path.read_text(encoding="utf-8"))
    expected = {"schema", "last_attempt_utc", "last_completed_utc", "last_status", "last_report_path"}
    if not isinstance(raw, dict) or set(raw) != expected or raw.get("schema") != STATE_SCHEMA:
        raise ValueError("strategy discovery state schema mismatch")
    status_raw = raw.get("last_status")
    status = None if status_raw is None else DiscoveryStatus(str(status_raw))
    report = raw.get("last_report_path")
    if not isinstance(report, str):
        raise ValueError("strategy discovery state report path invalid")
    return StrategyDiscoveryState(
        _parse_time(raw.get("last_attempt_utc"), "last attempt"),
        _parse_time(raw.get("last_completed_utc"), "last completed"),
        status,
        report,
    )


def write_discovery_state(path: Path, state: StrategyDiscoveryState) -> None:
    _atomic_json(
        path,
        {
            "schema": STATE_SCHEMA,
            "last_attempt_utc": _serialize_time(state.last_attempt_utc),
            "last_completed_utc": _serialize_time(state.last_completed_utc),
            "last_status": None if state.last_status is None else state.last_status.value,
            "last_report_path": state.last_report_path,
        },
    )


def _bounded_error(value: object, limit: int = 1000) -> str:
    rendered = " ".join(str(value or "").strip().split())
    return rendered[:limit] or "strategy_discovery_unknown_error"


def _looks_cross_symbol(proposal: StrategyProposal) -> bool:
    haystack = " ".join(
        (
            proposal.title,
            *proposal.components,
            *(value for _, value in proposal.declared_rules),
            *proposal.tags,
        )
    ).casefold()
    return any(marker in haystack for marker in CROSS_SYMBOL_MARKERS)


def _evidence_payload(result: VibeResearchResult) -> dict[str, object] | None:
    evidence = result.evidence
    if not result.available or evidence is None:
        return None
    return {
        "tool": evidence.tool,
        "fingerprint": evidence.fingerprint,
        "surface_sha256": evidence.surface_sha256,
        "request_sha256": evidence.request_sha256,
        "response_sha256": evidence.response_sha256,
        "result_text": evidence.result_text,
        "authority": {
            "broker_write": False,
            "promotion": False,
            "entry_veto": False,
        },
    }


class StrategyDiscoveryService:
    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    def __init__(
        self,
        config: StrategyDiscoveryConfig | None = None,
        *,
        contractor_factory: ContractorFactory | None = None,
        builder: StrategyEstateBuilder | None = None,
        digest_resolver: DigestResolver | None = None,
    ) -> None:
        self.config = config or StrategyDiscoveryConfig.default()
        self._contractor_factory = contractor_factory or (
            lambda vibe_root, work_root: VibeResearchContractor(vibe_root, work_root)
        )
        self._builder = builder or StrategyEstateBuilder()
        self._digest_resolver = digest_resolver or resolve_local_ollama_model_digest

    def state(self) -> StrategyDiscoveryState:
        return load_discovery_state(self.config.state_path)

    def schedule_snapshot(self, now: datetime | None = None) -> DiscoveryScheduleSnapshot:
        current = _utc(now or datetime.now(timezone.utc))
        state = self.state()
        recent = most_recent_sunday_slot_utc(current)
        next_slot = next_sunday_slot_utc(current)
        completed = state.last_completed_utc is not None and state.last_completed_utc >= recent
        cooling_down = (
            state.last_attempt_utc is not None
            and state.last_completed_utc is None or False
        )
        # Cooldown applies only when the current Sunday slot is still unsatisfied.
        if state.last_attempt_utc is not None and not completed:
            cooling_down = current - state.last_attempt_utc < SCHEDULE_RETRY_COOLDOWN
        else:
            cooling_down = False
        return DiscoveryScheduleSnapshot(
            due=not completed and not cooling_down,
            most_recent_slot_utc=recent,
            next_slot_utc=next_slot,
            last_attempt_utc=state.last_attempt_utc,
            last_completed_utc=state.last_completed_utc,
        )

    def scheduled_due(self, now: datetime | None = None) -> bool:
        return self.schedule_snapshot(now).due

    def status_line(self, now: datetime | None = None) -> str:
        current = _utc(now or datetime.now(timezone.utc))
        snapshot = self.schedule_snapshot(current)
        state = self.state()
        last = "never" if state.last_completed_utc is None else format_central(state.last_completed_utc)
        if snapshot.due:
            schedule = "Sunday 08:00 Central scan is due"
        else:
            schedule = f"next Sunday scan {format_central(snapshot.next_slot_utc)}"
        return f"Strategy discovery: last completed {last} · {schedule}"

    def discover(
        self,
        mode: DiscoveryMode = DiscoveryMode.BOTH,
        *,
        trigger: DiscoveryTrigger = DiscoveryTrigger.MANUAL,
        now: datetime | None = None,
    ) -> StrategyDiscoveryResult:
        if not isinstance(mode, DiscoveryMode) or not isinstance(trigger, DiscoveryTrigger):
            raise ValueError("strategy discovery mode/trigger invalid")
        attempted = _utc(now or datetime.now(timezone.utc))
        prior_state = self.state()
        write_discovery_state(
            self.config.state_path,
            StrategyDiscoveryState(
                attempted,
                prior_state.last_completed_utc,
                prior_state.last_status,
                prior_state.last_report_path,
            ),
        )

        errors: list[str] = []
        catalog_evidence: list[dict[str, object]] = []
        cross_evidence: list[dict[str, object]] = []
        population_payload: dict[str, object] = {"rows": [], "estate_update": None}
        proposals: tuple[StrategyProposal, ...] = ()
        new_single: tuple[StrategyProposal, ...] = ()
        deferred_cross: tuple[StrategyProposal, ...] = ()
        added = 0
        any_source_available = False

        contractor = self._contractor_factory(self.config.vibe_root.resolve(), self.config.work_root.resolve())

        if mode in {DiscoveryMode.NEW_STRATEGIES, DiscoveryMode.BOTH, DiscoveryMode.CROSS_SYMBOL}:
            catalog = contractor.invoke("list_strategies", {"limit": self.config.catalog_limit, "offset": 0})
            payload = _evidence_payload(catalog)
            if payload is None:
                errors.append("vibe_catalog_unavailable:" + _bounded_error(catalog.error))
            else:
                any_source_available = True
                catalog_evidence.append(payload)
                try:
                    proposals = deduplicate_proposals(proposals_from_vibe(catalog.evidence))  # type: ignore[arg-type]
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"vibe_catalog_parse_failed:{type(exc).__name__}:{_bounded_error(exc)}")

        if proposals:
            deferred_cross = tuple(row for row in proposals if _looks_cross_symbol(row))
            existing_fingerprints = {row.proposal_fingerprint for row in load_strategy_estate(self.config.estate_path)}
            eligible = tuple(
                row for row in proposals
                if row.fingerprint not in existing_fingerprints and row not in deferred_cross
            )
            new_single = eligible[: self.config.max_reconstructions]

        if mode in {DiscoveryMode.NEW_STRATEGIES, DiscoveryMode.BOTH} and new_single:
            try:
                model_digest = self._digest_resolver(self.config.model_tag)
                population = self._builder.populate(
                    new_single,
                    model_tag=self.config.model_tag,
                    model_digest=model_digest,
                    allowed_symbols=self.config.allowed_symbols,
                    allowed_timeframes=self.config.allowed_timeframes,
                    allowed_features=self.config.allowed_features,
                    allowed_sessions=self.config.allowed_sessions,
                    estate_path=self.config.estate_path,
                    created_at=attempted,
                )
                added = population.added_count
                population_payload = self._population_payload(population)
                for row in population.rows:
                    if row.status.value != "added":
                        errors.append(f"estate_population_{row.status.value}:{row.proposal_id}:{row.reason}")
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"strategy_reconstruction_failed:{type(exc).__name__}:{_bounded_error(exc)}")

        if mode in {DiscoveryMode.CROSS_SYMBOL, DiscoveryMode.BOTH}:
            for query in CROSS_SYMBOL_QUERIES:
                result = contractor.invoke("web_search", {"query": query, "max_results": 5})
                payload = _evidence_payload(result)
                if payload is None:
                    errors.append("cross_symbol_search_unavailable:" + _bounded_error(result.error))
                    continue
                any_source_available = True
                payload["query"] = query
                cross_evidence.append(payload)

        status = self._result_status(any_source_available, errors)
        stamp = attempted.strftime("%Y%m%dT%H%M%SZ")
        report_path = self.config.report_directory / f"strategy-discovery-{stamp}.json"
        _atomic_json(
            report_path,
            {
                "schema": REPORT_SCHEMA,
                "created_at_utc": attempted.isoformat(),
                "mode": mode.value,
                "trigger": trigger.value,
                "status": status.value,
                "schedule": {
                    "default": "Sunday 08:00 U.S. Central",
                    "most_recent_slot_utc": most_recent_sunday_slot_utc(attempted).isoformat(),
                },
                "catalog": {
                    "evidence": catalog_evidence,
                    "proposals_seen": len(proposals),
                    "new_single_symbol_candidates": [row.proposal_id for row in new_single],
                    "deferred_cross_symbol_candidates": [row.proposal_id for row in deferred_cross],
                },
                "estate_population": population_payload,
                "cross_symbol_research": {
                    "policy": (
                        "Web-search results are archived as untrusted research leads only. "
                        "They are not executable because arbitrary web snippets are not a certified strategy source "
                        "and the current StrategySpecV2 lane does not represent multi-symbol execution semantics."
                    ),
                    "evidence": cross_evidence,
                },
                "errors": errors,
                "authority": {
                    "broker_write": False,
                    "live_write": False,
                    "promotion": False,
                    "risk_override": False,
                    "guardian_override": False,
                    "hot_swap_running_snapshot": False,
                },
            },
        )

        completed_time = attempted if status is not DiscoveryStatus.UNAVAILABLE else prior_state.last_completed_utc
        write_discovery_state(
            self.config.state_path,
            StrategyDiscoveryState(attempted, completed_time, status, str(report_path.resolve())),
        )
        return StrategyDiscoveryResult(
            status=status,
            mode=mode,
            trigger=trigger,
            attempted_at_utc=attempted,
            proposals_seen=len(proposals),
            new_single_symbol_candidates=len(new_single),
            deferred_cross_symbol_candidates=len(deferred_cross),
            added_to_estate=added,
            cross_symbol_web_leads=len(cross_evidence),
            report_path=report_path.resolve(),
            errors=tuple(errors),
        )

    @staticmethod
    def _result_status(any_source_available: bool, errors: list[str]) -> DiscoveryStatus:
        if not any_source_available:
            return DiscoveryStatus.UNAVAILABLE
        if errors:
            return DiscoveryStatus.PARTIAL
        return DiscoveryStatus.COMPLETED

    @staticmethod
    def _population_payload(population: EstatePopulationResult) -> dict[str, object]:
        update = population.estate_update
        return {
            "rows": [
                {
                    "proposal_id": row.proposal_id,
                    "status": row.status.value,
                    "reconstruction_fingerprint": row.reconstruction_fingerprint,
                    "reason": row.reason,
                }
                for row in population.rows
            ],
            "estate_update": None if update is None else {
                "path": str(update.path),
                "sha256": update.sha256,
                "added": update.added,
                "total": update.total,
            },
        }
