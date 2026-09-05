from __future__ import annotations

"""M156 versioned feature intelligence, dependency, and point-in-time registry."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
from typing import Iterable

from .experiment_manifest import FeatureRef
from .features import FEATURE_NUMERICS_VERSION, FeatureConfig


def _canonical(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(payload: object) -> str:
    return sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _token(value: str, label: str, *, upper: bool = False) -> str:
    rendered = str(value).strip()
    if not rendered:
        raise ValueError(f"{label} required")
    if any(ch.isspace() for ch in rendered):
        raise ValueError(f"{label} cannot contain whitespace")
    return rendered.upper() if upper else rendered.lower()


def _items(values: Iterable[str], label: str, *, upper: bool = False) -> tuple[str, ...]:
    rendered = tuple(_token(value, label, upper=upper) for value in values)
    if len(rendered) != len(set(rendered)):
        raise ValueError(f"{label} values must be unique")
    return tuple(sorted(rendered))


def _feature_key(value: str) -> str:
    rendered = str(value).strip().lower()
    name, separator, version = rendered.partition("@")
    if separator != "@" or not name or not version or "@" in version:
        raise ValueError("feature dependency must use name@version")
    _token(name, "feature dependency name")
    _token(version, "feature dependency version")
    return f"{name}@{version}"


class FeatureFamily(StrEnum):
    PRICE = "price"
    RETURN = "return"
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    VOLUME_ACTIVITY = "volume_activity"
    SESSION = "session"
    EXECUTION = "execution"
    FORECAST = "forecast"
    EVENT = "event"
    STRUCTURE = "structure"
    CUSTOM = "custom"


class FeatureSource(StrEnum):
    MT5_BAR = "mt5_bar"
    MT5_NATIVE_INDICATOR = "mt5_native_indicator"
    MT5_CUSTOM_INDICATOR = "mt5_custom_indicator"
    DUSTY_DERIVED = "dusty_derived"
    EXTERNAL_POINT_IN_TIME = "external_point_in_time"
    FORECAST_PROVIDER = "forecast_provider"
    STATIC = "static"


class AvailabilityPolicy(StrEnum):
    RAW_OBSERVATION = "raw_observation"
    COMPLETED_BAR = "completed_bar"
    SESSION_EVENT = "session_event"
    EXTERNAL_RELEASE = "external_release"
    FORECAST_ISSUED = "forecast_issued"
    UNKNOWN = "unknown"


class LookaheadPolicy(StrEnum):
    NONE = "none"
    FUTURE = "future"
    UNKNOWN = "unknown"


class RepaintPolicy(StrEnum):
    STABLE = "stable"
    MAY_REPAINT = "may_repaint"
    UNKNOWN = "unknown"


class ComputeCost(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    name: str
    version: str
    family: FeatureFamily
    source: FeatureSource
    availability: AvailabilityPolicy
    lookahead: LookaheadPolicy
    repaint: RepaintPolicy
    warmup_observations: int
    dependencies: tuple[str, ...] = ()
    markets: tuple[str, ...] = ("GENERAL",)
    compatible_mutations: tuple[str, ...] = ()
    known_limitations: tuple[str, ...] = ()
    provenance: tuple[str, ...] = ()
    compute_cost: ComputeCost = ComputeCost.LOW
    native_parity_required: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _token(self.name, "feature name"))
        object.__setattr__(self, "version", _token(self.version, "feature version"))
        if self.warmup_observations < 0:
            raise ValueError("feature warmup cannot be negative")
        object.__setattr__(self, "dependencies", tuple(sorted({_feature_key(value) for value in self.dependencies})))
        markets = _items(self.markets, "feature market", upper=True)
        if not markets:
            raise ValueError("feature requires at least one market applicability")
        object.__setattr__(self, "markets", markets)
        object.__setattr__(self, "compatible_mutations", _items(self.compatible_mutations, "feature mutation"))
        limitations = tuple(sorted({str(value).strip() for value in self.known_limitations if str(value).strip()}))
        object.__setattr__(self, "known_limitations", limitations)
        provenance = tuple(sorted({str(value).strip() for value in self.provenance if str(value).strip()}))
        if not provenance:
            raise ValueError("feature provenance required")
        object.__setattr__(self, "provenance", provenance)

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    @property
    def decision_eligible(self) -> bool:
        return (
            self.lookahead is LookaheadPolicy.NONE
            and self.repaint is RepaintPolicy.STABLE
            and self.availability is not AvailabilityPolicy.UNKNOWN
        )

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-feature-definition-v1",
            "key": self.key,
            "family": self.family.value,
            "source": self.source.value,
            "availability": self.availability.value,
            "lookahead": self.lookahead.value,
            "repaint": self.repaint.value,
            "warmup_observations": self.warmup_observations,
            "dependencies": self.dependencies,
            "markets": self.markets,
            "compatible_mutations": self.compatible_mutations,
            "known_limitations": self.known_limitations,
            "provenance": self.provenance,
            "compute_cost": self.compute_cost.value,
            "native_parity_required": self.native_parity_required,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


class FeatureRegistry:
    """Small immutable-after-freeze catalog with dependency-aware identities."""

    def __init__(self, definitions: Iterable[FeatureDefinition] = ()) -> None:
        self._definitions: dict[str, FeatureDefinition] = {}
        self._frozen = False
        for definition in definitions:
            self.add(definition)

    def add(self, definition: FeatureDefinition) -> None:
        if self._frozen:
            raise RuntimeError("feature registry is frozen")
        if definition.key in self._definitions:
            raise ValueError(f"duplicate feature definition: {definition.key}")
        self._definitions[definition.key] = definition

    def get(self, key: str) -> FeatureDefinition:
        normalized = _feature_key(key)
        try:
            return self._definitions[normalized]
        except KeyError as exc:
            raise KeyError(f"unknown feature: {normalized}") from exc

    def keys(self) -> tuple[str, ...]:
        return tuple(sorted(self._definitions))

    def _walk(self, key: str, visiting: set[str], visited: set[str], ordered: list[str]) -> None:
        normalized = _feature_key(key)
        if normalized in visited:
            return
        if normalized in visiting:
            raise ValueError(f"feature dependency cycle at {normalized}")
        definition = self.get(normalized)
        visiting.add(normalized)
        for dependency in definition.dependencies:
            self._walk(dependency, visiting, visited, ordered)
        visiting.remove(normalized)
        visited.add(normalized)
        ordered.append(normalized)

    def closure(self, key: str) -> tuple[FeatureDefinition, ...]:
        ordered: list[str] = []
        self._walk(key, set(), set(), ordered)
        return tuple(self._definitions[item] for item in ordered)

    def validate(self) -> None:
        for definition in self._definitions.values():
            for dependency in definition.dependencies:
                if dependency not in self._definitions:
                    raise ValueError(f"{definition.key} depends on unknown feature {dependency}")
        for key in self.keys():
            self.closure(key)

    def freeze(self) -> "FeatureRegistry":
        self.validate()
        self._frozen = True
        return self

    @property
    def frozen(self) -> bool:
        return self._frozen

    def resolved_fingerprint(self, key: str) -> str:
        memo: dict[str, str] = {}

        def resolve(item: str) -> str:
            normalized = _feature_key(item)
            if normalized in memo:
                return memo[normalized]
            definition = self.get(normalized)
            dependencies = tuple((dep, resolve(dep)) for dep in definition.dependencies)
            memo[normalized] = _digest({"definition": definition.fingerprint, "dependencies": dependencies})
            return memo[normalized]

        return resolve(key)

    def feature_set_fingerprint(self, keys: Iterable[str]) -> str:
        normalized = tuple(sorted({_feature_key(key) for key in keys}))
        if not normalized:
            raise ValueError("feature set cannot be empty")
        return _digest(tuple((key, self.resolved_fingerprint(key)) for key in normalized))

    def warmup_required(self, key: str) -> int:
        return max((row.warmup_observations for row in self.closure(key)), default=0)

    def decision_eligible(self, key: str) -> bool:
        return all(row.decision_eligible for row in self.closure(key))

    def eligibility_reasons(self, key: str) -> tuple[str, ...]:
        reasons: list[str] = []
        for row in self.closure(key):
            if row.lookahead is not LookaheadPolicy.NONE:
                reasons.append(f"{row.key}:lookahead={row.lookahead.value}")
            if row.repaint is not RepaintPolicy.STABLE:
                reasons.append(f"{row.key}:repaint={row.repaint.value}")
            if row.availability is AvailabilityPolicy.UNKNOWN:
                reasons.append(f"{row.key}:availability=unknown")
        return tuple(reasons)

    def requires_native_parity(self, key: str) -> bool:
        return any(row.native_parity_required for row in self.closure(key))

    def supports_market(self, key: str, market: str) -> bool:
        requested = _token(market, "market", upper=True)
        return all("GENERAL" in row.markets or requested in row.markets for row in self.closure(key))

    def to_manifest_ref(self, key: str) -> FeatureRef:
        definition = self.get(key)
        return FeatureRef(
            name=definition.name,
            version=definition.version,
            fingerprint=self.resolved_fingerprint(key),
        )

    @property
    def fingerprint(self) -> str:
        self.validate()
        return _digest(
            tuple(
                (key, self._definitions[key].fingerprint, self.resolved_fingerprint(key))
                for key in self.keys()
            )
        )


def standard_feature_registry(config: FeatureConfig = FeatureConfig()) -> FeatureRegistry:
    """Registry for canonical outputs of ``compute_standard_features``.

    Legacy convenience aliases (``sma``, ``ema``, ``atr``, ``rsi``) are intentionally
    not registry identities. Experiments must bind the period-specific feature name.
    """

    provenance = (f"dusty.features:{FEATURE_NUMERICS_VERSION}",)
    broad_markets = ("FOREX", "CFD", "INDEX", "METAL", "EQUITY", "CRYPTO")
    registry = FeatureRegistry()
    for name in ("open", "high", "low", "close"):
        registry.add(
            FeatureDefinition(
                name=name,
                version="v1",
                family=FeatureFamily.PRICE,
                source=FeatureSource.MT5_BAR,
                availability=AvailabilityPolicy.COMPLETED_BAR,
                lookahead=LookaheadPolicy.NONE,
                repaint=RepaintPolicy.STABLE,
                warmup_observations=1,
                markets=broad_markets,
                provenance=provenance,
            )
        )
    registry.add(
        FeatureDefinition(
            name="spread_points",
            version="v1",
            family=FeatureFamily.EXECUTION,
            source=FeatureSource.MT5_BAR,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=1,
            markets=broad_markets,
            known_limitations=("historical source-bar spread is not an executable quote",),
            provenance=provenance,
        )
    )
    registry.add(
        FeatureDefinition(
            name="tick_volume",
            version="v1",
            family=FeatureFamily.VOLUME_ACTIVITY,
            source=FeatureSource.MT5_BAR,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=1,
            markets=broad_markets,
            known_limitations=("broker tick volume is not centralized exchange volume",),
            provenance=provenance,
        )
    )
    registry.add(
        FeatureDefinition(
            name="return_1",
            version="v1",
            family=FeatureFamily.RETURN,
            source=FeatureSource.DUSTY_DERIVED,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=2,
            dependencies=("close@v1",),
            markets=broad_markets,
            provenance=provenance,
        )
    )
    registry.add(
        FeatureDefinition(
            name=f"sma_{config.ma_period}",
            version="v1",
            family=FeatureFamily.TREND,
            source=FeatureSource.DUSTY_DERIVED,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=config.ma_period,
            dependencies=("close@v1",),
            markets=broad_markets,
            compatible_mutations=("rolling_window",),
            provenance=provenance,
        )
    )
    registry.add(
        FeatureDefinition(
            name=f"ema_{config.ma_period}",
            version="v1",
            family=FeatureFamily.TREND,
            source=FeatureSource.DUSTY_DERIVED,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=config.ma_period,
            dependencies=("close@v1",),
            markets=broad_markets,
            compatible_mutations=("rolling_window",),
            provenance=provenance,
            native_parity_required=True,
        )
    )
    registry.add(
        FeatureDefinition(
            name=f"atr_{config.atr_period}",
            version="v1",
            family=FeatureFamily.VOLATILITY,
            source=FeatureSource.DUSTY_DERIVED,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=config.atr_period,
            dependencies=("high@v1", "low@v1", "close@v1"),
            markets=broad_markets,
            compatible_mutations=("rolling_window", "normalization"),
            provenance=provenance + ("metaquotes:iATR",),
            native_parity_required=True,
        )
    )
    registry.add(
        FeatureDefinition(
            name=f"rsi_{config.rsi_period}",
            version="v1",
            family=FeatureFamily.MOMENTUM,
            source=FeatureSource.DUSTY_DERIVED,
            availability=AvailabilityPolicy.COMPLETED_BAR,
            lookahead=LookaheadPolicy.NONE,
            repaint=RepaintPolicy.STABLE,
            warmup_observations=config.rsi_period + 1,
            dependencies=("close@v1",),
            markets=broad_markets,
            compatible_mutations=("rolling_window", "threshold", "slope"),
            provenance=provenance + ("metaquotes:iRSI",),
            native_parity_required=True,
        )
    )
    return registry.freeze()
