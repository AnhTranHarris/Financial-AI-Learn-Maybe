from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from statistics import fmean
from typing import Any, Iterable, Mapping


class SourceKind(StrEnum):
    FOREX_FACTORY_TRADES = "forex_factory_trades"
    FOREX_FACTORY_CALENDAR = "forex_factory_calendar"
    MYFXBOOK = "myfxbook"
    MT5 = "mt5"
    DUSTY = "dusty"
    OTHER = "other"


class SourceGrade(StrEnum):
    LIVE = "live"
    DEMO = "demo"
    FORWARD_TEST = "forward_test"
    BACKTEST = "backtest"
    UNKNOWN = "unknown"


class TradeSide(StrEnum):
    LONG = "long"
    SHORT = "short"


class ActionKind(StrEnum):
    ENTRY = "entry"
    SCALE_IN = "scale_in"
    SCALE_OUT = "scale_out"
    EXIT = "exit"


@dataclass(frozen=True, slots=True)
class ContextFact:
    key: str
    value: Any
    known_at: datetime
    effective_at: datetime
    source: SourceKind
    source_ref: str
    category: str = "market"
    verified: bool = False


@dataclass(frozen=True, slots=True)
class TradeAction:
    at: datetime
    kind: ActionKind
    side: TradeSide
    price: float
    quantity: float = 1.0


@dataclass(frozen=True, slots=True)
class TradingEpisode:
    episode_id: str
    symbol: str
    source: SourceKind
    source_ref: str
    grade: SourceGrade
    actions: tuple[TradeAction, ...]
    context: tuple[ContextFact, ...] = ()
    verified: bool = False

    @property
    def entry(self) -> TradeAction:
        return next(action for action in self.actions if action.kind is ActionKind.ENTRY)

    @property
    def exit(self) -> TradeAction:
        return next(action for action in self.actions if action.kind is ActionKind.EXIT)

    @property
    def return_fraction(self) -> float:
        return signed_return(self.entry.price, self.exit.price, self.entry.side)

    @property
    def duration_minutes(self) -> float:
        return (self.exit.at - self.entry.at).total_seconds() / 60.0


@dataclass(frozen=True, slots=True)
class ActionContext:
    action: TradeAction
    facts: tuple[ContextFact, ...]


@dataclass(frozen=True, slots=True)
class BehaviorSignature:
    duration_bucket: str
    scale_ins: int
    scale_outs: int
    event_aware: bool


@dataclass(frozen=True, slots=True)
class ArchetypeStats:
    signature: BehaviorSignature
    count: int
    mean_return: float
    win_rate: float
    episode_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarketPrice:
    at: datetime
    price: float


@dataclass(frozen=True, slots=True)
class Counterfactual:
    label: str
    entry_at: datetime | None
    exit_at: datetime | None
    return_fraction: float


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value


def _datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _aware(parsed)
    raise TypeError("timestamp must be datetime or ISO-8601 string")


def _grade(value: object) -> SourceGrade:
    text = str(value or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "real": SourceGrade.LIVE,
        "live": SourceGrade.LIVE,
        "demo": SourceGrade.DEMO,
        "paper": SourceGrade.DEMO,
        "forward": SourceGrade.FORWARD_TEST,
        "forward_test": SourceGrade.FORWARD_TEST,
        "backtest": SourceGrade.BACKTEST,
        "historical": SourceGrade.BACKTEST,
        "test": SourceGrade.BACKTEST,
    }
    return aliases.get(text, SourceGrade.UNKNOWN)


def _side(value: object) -> TradeSide:
    text = str(value).strip().lower()
    if text in {"long", "buy"}:
        return TradeSide.LONG
    if text in {"short", "sell"}:
        return TradeSide.SHORT
    raise ValueError(f"unknown trade side: {value!r}")


def signed_return(entry_price: float, exit_price: float, side: TradeSide) -> float:
    if entry_price <= 0 or exit_price <= 0:
        raise ValueError("prices must be positive")
    direction = 1.0 if side is TradeSide.LONG else -1.0
    return direction * (exit_price - entry_price) / entry_price


def reconstruct_episode(
    episode_id: str,
    symbol: str,
    source: SourceKind,
    source_ref: str,
    grade: SourceGrade,
    actions: Iterable[TradeAction],
    *,
    context: Iterable[ContextFact] = (),
    verified: bool = False,
) -> TradingEpisode:
    """Build one validated behavior episode from already acquired observations."""
    ordered = tuple(actions)
    if not episode_id or not symbol or not source_ref:
        raise ValueError("episode_id, symbol, and source_ref are required")
    if len(ordered) < 2:
        raise ValueError("an episode requires at least entry and exit")
    if tuple(sorted(ordered, key=lambda action: action.at)) != ordered:
        raise ValueError("trade actions must be chronological")
    entries = [action for action in ordered if action.kind is ActionKind.ENTRY]
    exits = [action for action in ordered if action.kind is ActionKind.EXIT]
    if len(entries) != 1 or len(exits) != 1:
        raise ValueError("an episode requires exactly one entry and one exit")
    if ordered[0].kind is not ActionKind.ENTRY or ordered[-1].kind is not ActionKind.EXIT:
        raise ValueError("entry must be first and exit must be last")
    side = entries[0].side
    for action in ordered:
        _aware(action.at)
        if action.side is not side:
            raise ValueError("all actions in one episode must share direction")
        if action.price <= 0 or action.quantity <= 0:
            raise ValueError("action price and quantity must be positive")
    if exits[0].at <= entries[0].at:
        raise ValueError("exit must occur after entry")
    return TradingEpisode(
        episode_id=episode_id,
        symbol=symbol.upper(),
        source=source,
        source_ref=source_ref,
        grade=grade,
        actions=ordered,
        context=tuple(context),
        verified=verified,
    )


def normalize_human_trade(
    source: SourceKind,
    record: Mapping[str, object],
    *,
    source_ref: str,
) -> TradingEpisode:
    """Normalize a structured human-trade record without coupling to page HTML."""
    opened_at = _datetime(record["opened_at"])
    closed_at = _datetime(record["closed_at"])
    side = _side(record["side"])
    quantity = float(record.get("quantity", 1.0))
    grade_value = record.get("account_type", record.get("test_type", "unknown"))
    return reconstruct_episode(
        episode_id=str(record["episode_id"]),
        symbol=str(record["symbol"]),
        source=source,
        source_ref=source_ref,
        grade=_grade(grade_value),
        actions=(
            TradeAction(opened_at, ActionKind.ENTRY, side, float(record["entry_price"]), quantity),
            TradeAction(closed_at, ActionKind.EXIT, side, float(record["exit_price"]), quantity),
        ),
        verified=bool(record.get("verified", False)),
    )


def forex_factory_trade(record: Mapping[str, object], *, source_ref: str) -> TradingEpisode:
    return normalize_human_trade(SourceKind.FOREX_FACTORY_TRADES, record, source_ref=source_ref)


def myfxbook_trade(record: Mapping[str, object], *, source_ref: str) -> TradingEpisode:
    return normalize_human_trade(SourceKind.MYFXBOOK, record, source_ref=source_ref)


def forex_factory_calendar_fact(
    *,
    event_id: str,
    scheduled_at: datetime,
    known_at: datetime,
    currency: str,
    impact: str,
    source_ref: str,
    actual: object = None,
    forecast: object = None,
    previous: object = None,
    verified: bool = False,
) -> ContextFact:
    return ContextFact(
        key=f"calendar:{event_id}",
        value={
            "currency": currency.upper(),
            "impact": impact,
            "actual": actual,
            "forecast": forecast,
            "previous": previous,
        },
        known_at=_aware(known_at),
        effective_at=_aware(scheduled_at),
        source=SourceKind.FOREX_FACTORY_CALENDAR,
        source_ref=source_ref,
        category="calendar",
        verified=verified,
    )


def facts_as_of(facts: Iterable[ContextFact], at: datetime) -> tuple[ContextFact, ...]:
    """Return only information that was actually knowable by ``at``."""
    at = _aware(at)
    available = (fact for fact in facts if _aware(fact.known_at) <= at)
    return tuple(
        sorted(
            available,
            key=lambda fact: (fact.effective_at, fact.key, fact.source.value, fact.source_ref),
        )
    )


def context_timeline(
    episode: TradingEpisode,
    facts: Iterable[ContextFact],
) -> tuple[ActionContext, ...]:
    facts = tuple(facts)
    return tuple(ActionContext(action, facts_as_of(facts, action.at)) for action in episode.actions)


def behavior_signature(episode: TradingEpisode) -> BehaviorSignature:
    minutes = episode.duration_minutes
    if minutes < 5:
        bucket = "micro"
    elif minutes < 30:
        bucket = "short"
    elif minutes < 240:
        bucket = "intraday"
    else:
        bucket = "extended"
    return BehaviorSignature(
        duration_bucket=bucket,
        scale_ins=sum(action.kind is ActionKind.SCALE_IN for action in episode.actions),
        scale_outs=sum(action.kind is ActionKind.SCALE_OUT for action in episode.actions),
        event_aware=any(
            fact.category == "calendar" and fact.known_at <= episode.entry.at
            for fact in episode.context
        ),
    )


def discover_archetypes(
    episodes: Iterable[TradingEpisode],
    *,
    min_count: int = 2,
) -> tuple[ArchetypeStats, ...]:
    """Group outcome-free behavior signatures, then summarize their observed outcomes."""
    if min_count < 1:
        raise ValueError("min_count must be positive")
    groups: dict[BehaviorSignature, list[TradingEpisode]] = defaultdict(list)
    for episode in episodes:
        groups[behavior_signature(episode)].append(episode)

    result: list[ArchetypeStats] = []
    for signature, group in groups.items():
        if len(group) < min_count:
            continue
        returns = [episode.return_fraction for episode in group]
        result.append(
            ArchetypeStats(
                signature=signature,
                count=len(group),
                mean_return=fmean(returns),
                win_rate=sum(value > 0 for value in returns) / len(returns),
                episode_ids=tuple(sorted(episode.episode_id for episode in group)),
            )
        )
    return tuple(
        sorted(
            result,
            key=lambda item: (
                -item.count,
                item.signature.duration_bucket,
                item.signature.scale_ins,
                item.signature.scale_outs,
                item.signature.event_aware,
            ),
        )
    )


def evaluate_counterfactuals(
    episode: TradingEpisode,
    prices: Iterable[MarketPrice],
) -> tuple[Counterfactual, ...]:
    """Compare nearby observed choices; never invent or interpolate market prices."""
    path = tuple(sorted(prices, key=lambda point: point.at))
    if len({point.at for point in path}) != len(path):
        raise ValueError("market path timestamps must be unique")
    index = {point.at: offset for offset, point in enumerate(path)}
    if episode.entry.at not in index or episode.exit.at not in index:
        raise ValueError("market path must contain exact entry and exit timestamps")

    entry_index = index[episode.entry.at]
    exit_index = index[episode.exit.at]
    side = episode.entry.side
    candidates: list[tuple[str, int, int]] = [("actual_path", entry_index, exit_index)]
    if entry_index > 0:
        candidates.append(("enter_one_observation_earlier", entry_index - 1, exit_index))
    if entry_index + 1 < exit_index:
        candidates.append(("enter_one_observation_later", entry_index + 1, exit_index))
    if exit_index - 1 > entry_index:
        candidates.append(("exit_one_observation_earlier", entry_index, exit_index - 1))
    if exit_index + 1 < len(path):
        candidates.append(("exit_one_observation_later", entry_index, exit_index + 1))

    results = [
        Counterfactual(
            label,
            path[start].at,
            path[end].at,
            signed_return(path[start].price, path[end].price, side),
        )
        for label, start, end in candidates
    ]
    results.append(Counterfactual("no_trade", None, None, 0.0))
    return tuple(results)
