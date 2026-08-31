from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from .curriculum import resolve_symbol


class AssetClass(StrEnum):
    FX = "fx"
    METAL = "metal"
    ENERGY = "energy"
    CRYPTO = "crypto"
    FUTURE = "future"
    INDEX = "index"
    EQUITY = "equity"


class InstrumentType(StrEnum):
    SPOT = "spot"
    CFD = "cfd"
    FUTURE = "future"
    INDEX_CFD = "index_cfd"
    EQUITY_CFD = "equity_cfd"
    CASH_EQUITY = "cash_equity"


@dataclass(frozen=True, slots=True)
class InstrumentEconomics:
    """Broker-specific economic units required for safe sizing and cost accounting."""

    contract_size: float
    tick_size: float
    tick_value: float
    volume_min: float
    volume_step: float
    volume_max: float
    margin_rate: float = 0.0
    commission_per_lot: float = 0.0
    swap_long: float = 0.0
    swap_short: float = 0.0
    stop_level_points: float = 0.0
    freeze_level_points: float = 0.0

    def __post_init__(self) -> None:
        values = (
            self.contract_size,
            self.tick_size,
            self.tick_value,
            self.volume_min,
            self.volume_step,
            self.volume_max,
            self.margin_rate,
            self.commission_per_lot,
            self.swap_long,
            self.swap_short,
            self.stop_level_points,
            self.freeze_level_points,
        )
        if any(not math.isfinite(value) for value in values):
            raise ValueError("instrument economics must be finite")
        required_positive = {
            "contract_size": self.contract_size,
            "tick_size": self.tick_size,
            "tick_value": self.tick_value,
            "volume_min": self.volume_min,
            "volume_step": self.volume_step,
            "volume_max": self.volume_max,
        }
        invalid = [name for name, value in required_positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"instrument economics require positive {','.join(sorted(invalid))}")
        if self.volume_max < self.volume_min:
            raise ValueError("volume_max cannot be below volume_min")
        if self.margin_rate < 0 or self.commission_per_lot < 0:
            raise ValueError("margin and commission cannot be negative")
        if self.stop_level_points < 0 or self.freeze_level_points < 0:
            raise ValueError("broker distance constraints cannot be negative")

    def normalize_volume_down(self, requested: float) -> float:
        """Round down only; never increase risk to reach a convenient lot size."""
        if not math.isfinite(requested) or requested < 0:
            raise ValueError("requested volume must be finite and nonnegative")
        if requested < self.volume_min:
            return 0.0
        capped = min(requested, self.volume_max)
        steps = int((capped - self.volume_min) / self.volume_step + 1e-12)
        normalized = self.volume_min + steps * self.volume_step
        result = round(min(normalized, self.volume_max), 12)
        if result > requested + 1e-12:
            raise AssertionError("volume normalization rounded upward")
        return result


@dataclass(frozen=True, slots=True)
class MarketIdentity:
    raw_symbol: str
    canonical_symbol: str
    economic_underlier: str
    asset_class: AssetClass
    instrument_type: InstrumentType
    venue: str = ""
    contract: str = ""
    expiry: date | None = None
    base_currency: str = ""
    quote_currency: str = ""
    sessions: tuple[str, ...] = ()
    economics: InstrumentEconomics | None = None

    def __post_init__(self) -> None:
        if not self.raw_symbol.strip() or not self.canonical_symbol.strip() or not self.economic_underlier.strip():
            raise ValueError("market identity requires symbol and economic underlier")
        if self.instrument_type is InstrumentType.FUTURE and not self.contract.strip():
            raise ValueError("futures require an explicit contract identity")
        if len({session.strip().lower() for session in self.sessions if session.strip()}) != len(self.sessions):
            raise ValueError("market sessions must be unique and non-empty")

    @classmethod
    def of(
        cls,
        *,
        raw_symbol: str,
        economic_underlier: str,
        asset_class: AssetClass,
        instrument_type: InstrumentType,
        venue: str = "",
        contract: str = "",
        expiry: date | None = None,
        base_currency: str = "",
        quote_currency: str = "",
        sessions: tuple[str, ...] = (),
        economics: InstrumentEconomics | None = None,
        aliases: dict[str, str] | None = None,
    ) -> "MarketIdentity":
        canonical = resolve_symbol(raw_symbol, aliases).canonical
        normalized_sessions = tuple(session.strip().lower() for session in sessions if session.strip())
        return cls(
            raw_symbol=raw_symbol,
            canonical_symbol=canonical,
            economic_underlier=economic_underlier.strip().upper(),
            asset_class=asset_class,
            instrument_type=instrument_type,
            venue=venue.strip(),
            contract=contract.strip().upper(),
            expiry=expiry,
            base_currency=base_currency.strip().upper(),
            quote_currency=quote_currency.strip().upper(),
            sessions=normalized_sessions,
            economics=economics,
        )


@dataclass(frozen=True, slots=True)
class BrokerSymbolSnapshot:
    """Point-in-time broker symbol specification; read-only evidence, never order authority."""

    broker: str
    account_currency: str
    market: MarketIdentity
    captured_at: datetime
    leverage: float = 0.0

    def __post_init__(self) -> None:
        if not self.broker.strip() or not self.account_currency.strip():
            raise ValueError("broker and account currency are required")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("broker symbol snapshot timestamp must be timezone-aware")
        if not math.isfinite(self.leverage) or self.leverage < 0:
            raise ValueError("leverage must be finite and nonnegative")
        if self.market.economics is None:
            raise ValueError("broker symbol snapshot requires instrument economics")


@dataclass(frozen=True, slots=True)
class SymbolResearchProfile:
    market: MarketIdentity
    currencies: tuple[str, ...] = ()
    related_underliers: tuple[str, ...] = ()
    allowed_asset_context: tuple[AssetClass, ...] = ()

    @classmethod
    def of(
        cls,
        market: MarketIdentity,
        *,
        currencies: tuple[str, ...] = (),
        related_underliers: tuple[str, ...] = (),
        allowed_asset_context: tuple[AssetClass, ...] = (),
    ) -> "SymbolResearchProfile":
        normalized_currencies = tuple(sorted({item.strip().upper() for item in currencies if item.strip()}))
        normalized_underliers = tuple(sorted({item.strip().upper() for item in related_underliers if item.strip()}))
        return cls(
            market=market,
            currencies=normalized_currencies,
            related_underliers=normalized_underliers,
            allowed_asset_context=tuple(sorted(set(allowed_asset_context), key=lambda item: item.value)),
        )


def same_economic_underlier(left: MarketIdentity, right: MarketIdentity) -> bool:
    """Related instruments may inform each other without pretending their price series are identical."""
    return left.economic_underlier == right.economic_underlier
