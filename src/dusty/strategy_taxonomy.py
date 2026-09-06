from __future__ import annotations

"""Deterministic quant taxonomy for human-facing strategy titles.

Strategy IDs and executable hashes remain the authority. A title is only an
inspectable projection of bounded strategy metadata; it never carries alpha,
promotion, risk, Guardian, or broker authority. Ollama may choose values from
these enums, but application code validates them and renders the final title.
Special classifications such as SEC filing, Fibonacci, or a session handoff must
also be supported by explicit reconstruction/source metadata.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable

from .strategy_catalog import StrategyCatalogEntry, StrategyStage


class StrategyArchetype(StrEnum):
    BREAKOUT = "breakout"
    REVERSAL = "reversal"
    MOMENTUM = "momentum"
    TREND_CONTINUATION = "trend_continuation"
    PULLBACK = "pullback"
    MEAN_REVERSION = "mean_reversion"
    VOLATILITY_EXPANSION = "volatility_expansion"
    SESSION_HANDOFF = "session_handoff"
    CATALYST_RUNNER = "catalyst_runner"
    RANGE_ROTATION = "range_rotation"


class StrategyCatalyst(StrEnum):
    NONE = "none"
    MACRO_NEWS = "macro_news"
    TECH_NEWS = "tech_news"
    SEC_FILING = "sec_filing"
    EARNINGS = "earnings"
    SESSION_OPEN = "session_open"
    LIQUIDITY_EVENT = "liquidity_event"


class StrategyStructure(StrEnum):
    PRICE_ACTION = "price_action"
    COMPRESSION = "compression"
    RANGE = "range"
    TREND = "trend"
    FIBONACCI = "fibonacci"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    GAP = "gap"


class StrategySessionProfile(StrEnum):
    UNRESTRICTED = "unrestricted"
    ASIA = "asia"
    LONDON = "london"
    NEW_YORK = "new_york"
    ASIA_TO_LONDON = "asia_to_london"
    ASIA_TO_LONDON_NY = "asia_to_london_ny"
    LONDON_NY_OVERLAP = "london_ny_overlap"


@dataclass(frozen=True, slots=True)
class QuantStrategyIdentity:
    archetype: StrategyArchetype
    catalyst: StrategyCatalyst = StrategyCatalyst.NONE
    structure: StrategyStructure = StrategyStructure.PRICE_ACTION
    session_profile: StrategySessionProfile = StrategySessionProfile.UNRESTRICTED

    def __post_init__(self) -> None:
        if not isinstance(self.archetype, StrategyArchetype):
            raise ValueError("strategy archetype is invalid")
        if not isinstance(self.catalyst, StrategyCatalyst):
            raise ValueError("strategy catalyst is invalid")
        if not isinstance(self.structure, StrategyStructure):
            raise ValueError("strategy structure is invalid")
        if not isinstance(self.session_profile, StrategySessionProfile):
            raise ValueError("strategy session profile is invalid")

    @property
    def payload(self) -> tuple[str, str, str, str]:
        return (
            self.archetype.value,
            self.catalyst.value,
            self.structure.value,
            self.session_profile.value,
        )


def _tokens(values: Iterable[object]) -> str:
    return " ".join(str(value).strip().casefold() for value in values if str(value).strip())


def _classification_text(reconstruction: object) -> str:
    candidate = getattr(reconstruction, "candidate_spec")
    rules = getattr(reconstruction, "rules")
    return _tokens((
        getattr(reconstruction, "title", ""),
        *(getattr(rule, "name", "") for rule in rules),
        *(getattr(rule, "value", "") for rule in rules),
        *(getattr(candidate, "session_filters", ())),
    ))


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def validate_quant_identity_support(reconstruction: object, identity: QuantStrategyIdentity) -> None:
    """Reject special display claims that are not grounded in available metadata.

    This deliberately does not second-guess ordinary archetype classification;
    breakout vs reversal vs trend is the bounded model's semantic task. It does
    prevent the title from inventing a named catalyst, Fibonacci structure, or
    geographic/session handoff absent from the source/reconstruction evidence.
    """

    text = _classification_text(reconstruction)
    catalyst_terms = {
        StrategyCatalyst.MACRO_NEWS: ("macro", "cpi", "fomc", "nfp", "inflation", "rate decision", "economic news"),
        StrategyCatalyst.TECH_NEWS: ("tech news", "technology news", "technology catalyst"),
        StrategyCatalyst.SEC_FILING: ("sec filing", "10-k", "10-q", "8-k", "filing"),
        StrategyCatalyst.EARNINGS: ("earnings",),
        StrategyCatalyst.SESSION_OPEN: ("session", "asia", "asian", "london", "new york", "ny open", "market open"),
        StrategyCatalyst.LIQUIDITY_EVENT: ("liquidity",),
    }
    if identity.catalyst is not StrategyCatalyst.NONE and not _has_any(text, catalyst_terms[identity.catalyst]):
        raise ValueError(f"quant strategy catalyst lacks evidence support: {identity.catalyst.value}")

    if identity.structure is StrategyStructure.FIBONACCI and not _has_any(text, ("fibonacci", "fib ", "fib-", "fib_")):
        raise ValueError("Fibonacci quant strategy identity lacks evidence support")

    session_requirements = {
        StrategySessionProfile.ASIA: (("asia", "asian"),),
        StrategySessionProfile.LONDON: (("london",),),
        StrategySessionProfile.NEW_YORK: (("new york", "new_york", "ny open", "ny session"),),
        StrategySessionProfile.ASIA_TO_LONDON: (("asia", "asian"), ("london",)),
        StrategySessionProfile.ASIA_TO_LONDON_NY: (
            ("asia", "asian"),
            ("london",),
            ("new york", "new_york", "ny open", "ny session", "london/ny"),
        ),
        StrategySessionProfile.LONDON_NY_OVERLAP: (
            ("london",),
            ("new york", "new_york", "ny open", "ny session", "london/ny"),
        ),
    }
    if identity.session_profile is not StrategySessionProfile.UNRESTRICTED:
        for terms in session_requirements[identity.session_profile]:
            if not _has_any(text, terms):
                raise ValueError(f"quant strategy session lacks evidence support: {identity.session_profile.value}")

    if identity.archetype is StrategyArchetype.CATALYST_RUNNER and identity.catalyst is StrategyCatalyst.NONE:
        raise ValueError("catalyst-runner identity requires an evidenced catalyst")
    if identity.archetype is StrategyArchetype.SESSION_HANDOFF and identity.session_profile is StrategySessionProfile.UNRESTRICTED:
        raise ValueError("session-handoff identity requires an evidenced session profile")


def _legacy_identity(reconstruction: object) -> QuantStrategyIdentity:
    """Conservative fallback for reconstructions created before taxonomy fields.

    It classifies only explicit words already present in source/reconstruction
    metadata. It does not claim that a source used rules that were not archived.
    """

    candidate = getattr(reconstruction, "candidate_spec")
    rules = getattr(reconstruction, "rules")
    text = _classification_text(reconstruction)

    catalyst = StrategyCatalyst.NONE
    if "sec" in text or "filing" in text:
        catalyst = StrategyCatalyst.SEC_FILING
    elif "earnings" in text:
        catalyst = StrategyCatalyst.EARNINGS
    elif "tech news" in text or "technology news" in text:
        catalyst = StrategyCatalyst.TECH_NEWS
    elif "macro" in text or "economic news" in text:
        catalyst = StrategyCatalyst.MACRO_NEWS

    session = StrategySessionProfile.UNRESTRICTED
    if ("asia" in text or "asian" in text) and "london" in text and ("new york" in text or "new_york" in text or "london/ny" in text):
        session = StrategySessionProfile.ASIA_TO_LONDON_NY
    elif ("asia" in text or "asian" in text) and "london" in text:
        session = StrategySessionProfile.ASIA_TO_LONDON
    elif "london" in text and ("new york" in text or "new_york" in text or "london/ny" in text):
        session = StrategySessionProfile.LONDON_NY_OVERLAP
    elif "asia" in text or "asian" in text:
        session = StrategySessionProfile.ASIA
    elif "london" in text:
        session = StrategySessionProfile.LONDON
    elif "new york" in text or "new_york" in text or "ny session" in text:
        session = StrategySessionProfile.NEW_YORK

    if "fibonacci" in text or "fib " in text:
        structure = StrategyStructure.FIBONACCI
    elif "compression" in text:
        structure = StrategyStructure.COMPRESSION
    elif "gap" in text:
        structure = StrategyStructure.GAP
    elif "range" in text:
        structure = StrategyStructure.RANGE
    elif "volatility" in text or "atr" in text:
        structure = StrategyStructure.VOLATILITY
    elif "momentum" in text or "rsi" in text:
        structure = StrategyStructure.MOMENTUM
    elif "trend" in text or "ema" in text or "sma" in text:
        structure = StrategyStructure.TREND
    else:
        structure = StrategyStructure.PRICE_ACTION

    if catalyst in {StrategyCatalyst.SEC_FILING, StrategyCatalyst.EARNINGS} and "runner" in text:
        archetype = StrategyArchetype.CATALYST_RUNNER
    elif "mean reversion" in text or "mean-reversion" in text:
        archetype = StrategyArchetype.MEAN_REVERSION
    elif "reversal" in text:
        archetype = StrategyArchetype.REVERSAL
    elif "pullback" in text:
        archetype = StrategyArchetype.PULLBACK
    elif "breakout" in text:
        archetype = StrategyArchetype.BREAKOUT
    elif "session" in text and session is not StrategySessionProfile.UNRESTRICTED:
        archetype = StrategyArchetype.SESSION_HANDOFF
    elif "volatility expansion" in text:
        archetype = StrategyArchetype.VOLATILITY_EXPANSION
    elif "momentum" in text:
        archetype = StrategyArchetype.MOMENTUM
    elif "trend" in text:
        archetype = StrategyArchetype.TREND_CONTINUATION
    elif "range" in text:
        archetype = StrategyArchetype.RANGE_ROTATION
    else:
        archetype = StrategyArchetype.BREAKOUT

    if session is StrategySessionProfile.UNRESTRICTED:
        filters = {str(value).strip().casefold() for value in getattr(candidate, "session_filters", ())}
        if "asia" in filters and "london" in filters and ("new_york" in filters or "new york" in filters):
            session = StrategySessionProfile.ASIA_TO_LONDON_NY
        elif "asia" in filters and "london" in filters:
            session = StrategySessionProfile.ASIA_TO_LONDON
        elif "london" in filters and ("new_york" in filters or "new york" in filters):
            session = StrategySessionProfile.LONDON_NY_OVERLAP

    identity = QuantStrategyIdentity(archetype, catalyst, structure, session)
    validate_quant_identity_support(reconstruction, identity)
    return identity


def quant_identity_for_reconstruction(reconstruction: object) -> QuantStrategyIdentity:
    values: dict[str, str] = {}
    for rule in getattr(reconstruction, "rules"):
        name = str(getattr(rule, "name", "")).strip().casefold()
        if name.startswith("identity."):
            values[name] = str(getattr(rule, "value", "")).strip().casefold()

    required = {
        "identity.archetype",
        "identity.catalyst",
        "identity.structure",
        "identity.session_profile",
    }
    if not values:
        return _legacy_identity(reconstruction)
    if set(values) & required != required:
        raise ValueError("partial quant strategy identity is not allowed")

    identity = QuantStrategyIdentity(
        StrategyArchetype(values["identity.archetype"]),
        StrategyCatalyst(values["identity.catalyst"]),
        StrategyStructure(values["identity.structure"]),
        StrategySessionProfile(values["identity.session_profile"]),
    )
    validate_quant_identity_support(reconstruction, identity)
    return identity


def _base_title(identity: QuantStrategyIdentity) -> str:
    if identity.structure is StrategyStructure.FIBONACCI and identity.session_profile is StrategySessionProfile.ASIA_TO_LONDON_NY:
        return "Fibonacci Asia→London/NY Continuation"
    if identity.structure is StrategyStructure.FIBONACCI and identity.session_profile is StrategySessionProfile.ASIA_TO_LONDON:
        return "Fibonacci Asia→London Continuation"
    if identity.archetype is StrategyArchetype.SESSION_HANDOFF and identity.structure is StrategyStructure.COMPRESSION:
        if identity.session_profile is StrategySessionProfile.ASIA_TO_LONDON_NY:
            return "Asian Compression → London/NY Expansion"
        if identity.session_profile is StrategySessionProfile.ASIA_TO_LONDON:
            return "Asian Compression → London Expansion"
    if identity.catalyst is StrategyCatalyst.TECH_NEWS and identity.archetype in {StrategyArchetype.BREAKOUT, StrategyArchetype.VOLATILITY_EXPANSION}:
        return "Tech-News Volatility Breakout"
    if identity.catalyst is StrategyCatalyst.MACRO_NEWS and identity.archetype in {StrategyArchetype.BREAKOUT, StrategyArchetype.VOLATILITY_EXPANSION}:
        return "Macro-News Volatility Breakout"
    if identity.catalyst is StrategyCatalyst.SEC_FILING:
        return "SEC Filing Momentum Runner" if identity.archetype is StrategyArchetype.CATALYST_RUNNER else "SEC Filing Catalyst Breakout"
    if identity.catalyst is StrategyCatalyst.EARNINGS:
        return "Earnings Catalyst Runner" if identity.archetype is StrategyArchetype.CATALYST_RUNNER else "Earnings Volatility Breakout"

    return {
        StrategyArchetype.BREAKOUT: "Price-Structure Breakout",
        StrategyArchetype.REVERSAL: "Exhaustion Reversal",
        StrategyArchetype.MOMENTUM: "Momentum Continuation",
        StrategyArchetype.TREND_CONTINUATION: "Trend Continuation",
        StrategyArchetype.PULLBACK: "Trend Pullback Continuation",
        StrategyArchetype.MEAN_REVERSION: "Statistical Mean Reversion",
        StrategyArchetype.VOLATILITY_EXPANSION: "Volatility Expansion Breakout",
        StrategyArchetype.SESSION_HANDOFF: "Session-Handoff Continuation",
        StrategyArchetype.CATALYST_RUNNER: "Event Catalyst Momentum Runner",
        StrategyArchetype.RANGE_ROTATION: "Range Rotation",
    }[identity.archetype]


def quant_title_for_reconstruction(reconstruction: object) -> str:
    identity = quant_identity_for_reconstruction(reconstruction)
    symbols = tuple(str(value).upper() for value in getattr(reconstruction, "symbols"))
    symbol_text = "/".join(symbols) if len(symbols) <= 3 else "Multi-Asset"
    timeframe = str(getattr(reconstruction, "timeframe")).upper()
    direction = str(getattr(getattr(reconstruction, "candidate_spec"), "direction").value).title()
    return f"{_base_title(identity)} · {symbol_text} · {timeframe} · {direction}"


def catalog_entry_for_reconstruction(reconstruction: object) -> StrategyCatalogEntry:
    spec = getattr(reconstruction, "candidate_spec")
    return StrategyCatalogEntry(
        spec.strategy_id,
        quant_title_for_reconstruction(reconstruction),
        spec.strategy_hash,
        StrategyStage.BACKTEST_CANDIDATE,
        allowed_symbols=tuple(getattr(reconstruction, "symbols")),
        source_url=str(getattr(reconstruction, "source_url")),
        timeframe=str(getattr(reconstruction, "timeframe")),
    )
