from __future__ import annotations

from dataclasses import dataclass
from datetime import date
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
class MarketIdentity:
    raw_symbol: str
    canonical_symbol: str
    economic_underlier: str
    asset_class: AssetClass
    instrument_type: InstrumentType
    venue: str = ""
    contract: str = ""
    expiry: date | None = None

    def __post_init__(self) -> None:
        if not self.raw_symbol.strip() or not self.canonical_symbol.strip() or not self.economic_underlier.strip():
            raise ValueError("market identity requires symbol and economic underlier")
        if self.instrument_type is InstrumentType.FUTURE and not self.contract.strip():
            raise ValueError("futures require an explicit contract identity")

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
        aliases: dict[str, str] | None = None,
    ) -> "MarketIdentity":
        canonical = resolve_symbol(raw_symbol, aliases).canonical
        return cls(
            raw_symbol=raw_symbol,
            canonical_symbol=canonical,
            economic_underlier=economic_underlier.strip().upper(),
            asset_class=asset_class,
            instrument_type=instrument_type,
            venue=venue.strip(),
            contract=contract.strip().upper(),
            expiry=expiry,
        )


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
