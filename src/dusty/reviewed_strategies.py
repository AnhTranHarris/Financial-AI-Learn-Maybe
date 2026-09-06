"""Small, code-reviewed executable research packages.

Built-in RSI seeds remain infrastructure benchmarks. M196.5 additionally allows
an explicitly configured, SHA-256-pinned reconstruction snapshot to supply exact
research packages to both the UI process and its spawned Windows worker. Ordinary
catalog JSON remains metadata-only and cannot supply executable rules.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
from typing import Any

from .cognition import CognitionPolicy
from .experience import TradeSide
from .features import FEATURE_NUMERICS_VERSION, FeatureConfig
from .research import Clause, RuleOp
from .runtime import CompiledStrategy, compile_strategy
from .strategy_catalog import StrategyCatalogEntry, StrategyStage
from .strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from .strategy_taxonomy import catalog_entry_for_reconstruction


@dataclass(frozen=True, slots=True)
class ReviewedResearchPackage:
    spec: StrategySpecV2
    title: str
    features: FeatureConfig = FeatureConfig()
    cognition: CognitionPolicy = CognitionPolicy()

    @property
    def compiled(self) -> CompiledStrategy:
        return compile_strategy(self.spec)

    @property
    def fingerprint(self) -> str:
        payload = {"strategy_hash": self.spec.strategy_hash, "feature_numerics": FEATURE_NUMERICS_VERSION,
                   "features": asdict(self.features), "cognition": asdict(self.cognition)}
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def catalog_entry(self) -> StrategyCatalogEntry:
        return StrategyCatalogEntry(
            self.spec.strategy_id, self.title, self.spec.strategy_hash,
            StrategyStage.BACKTEST_CANDIDATE, universal_symbol_compatibility=True,
            timeframe="M15",
        )


@dataclass(frozen=True, slots=True)
class _ConfiguredReconstructionPackage:
    """Structural adapter; keeps reconstruction provenance in the package hash."""

    inner: Any

    @property
    def spec(self) -> StrategySpecV2:
        return self.inner.reconstruction.candidate_spec

    @property
    def title(self) -> str:
        return self.catalog_entry.title

    @property
    def features(self) -> FeatureConfig:
        return self.inner.features

    @property
    def cognition(self) -> CognitionPolicy:
        return self.inner.cognition

    @property
    def compiled(self) -> CompiledStrategy:
        return self.inner.compiled

    @property
    def fingerprint(self) -> str:
        return self.inner.fingerprint

    @property
    def catalog_entry(self) -> StrategyCatalogEntry:
        return catalog_entry_for_reconstruction(self.inner.reconstruction)



def reviewed_research_packages() -> tuple[ReviewedResearchPackage, ...]:
    """Symmetric RSI momentum seeds: infrastructure benchmarks, not online recommendations.

    Universal catalog visibility means 'may research', never 'suitable for deployment'.
    Broker economics and permissions are separately checked by the research adapter.
    """
    return tuple(
        ReviewedResearchPackage(
            StrategySpecV2(
                strategy_id=f"research-rsi-momentum-{side.value}-v1",
                direction=side,
                entry_groups=(RuleGroup((
                    Clause("rsi", RuleOp.GE, low), Clause("rsi", RuleOp.LE, high),
                    Clause("return_1", RuleOp.GT if side is TradeSide.LONG else RuleOp.LT, 0.0),
                )),),
                exit_plan=ExitPlan("atr:2", "rr:2", max_hold_steps=16),
                decision_timeframe_minutes=15, intended_horizon_minutes=240, cooldown_steps=4,
            ),
            f"RESEARCH ONLY: RSI momentum {side.value} (M15)",
        )
        for side, low, high in ((TradeSide.LONG, 55.0, 70.0), (TradeSide.SHORT, 30.0, 45.0))
    )



def _configured_reconstruction_packages() -> tuple[_ConfiguredReconstructionPackage, ...]:
    # Lazy imports avoid a module-import cycle: trading_skills itself reuses the
    # built-in package type for the UI projection. Resolution happens only after
    # both modules have finished importing.
    from .strategy_library_snapshot import configured_reconstructions
    from .trading_skills import ReconstructedResearchPackage

    return tuple(
        _ConfiguredReconstructionPackage(ReconstructedResearchPackage(row))
        for row in configured_reconstructions()
    )



def resolve_research_package(entry: StrategyCatalogEntry) -> ReviewedResearchPackage | _ConfiguredReconstructionPackage:
    """Resolve exact executable artifacts; catalog metadata alone never suffices.

    Pre-taxonomy snapshots remain executable through their exact legacy catalog
    projection, while the new PC estate uses the deterministic quant-title
    projection. Both views bind the same immutable reconstruction and strategy
    hash; arbitrary caller-edited metadata still fails closed.
    """
    for package in reviewed_research_packages():
        if entry.strategy_id == package.spec.strategy_id:
            if entry != package.catalog_entry:
                raise ValueError("reviewed_package_metadata_mismatch")
            return package
    for package in _configured_reconstruction_packages():
        if entry.strategy_id == package.spec.strategy_id:
            legacy_entry = package.inner.catalog_entry
            if entry not in (package.catalog_entry, legacy_entry):
                raise ValueError("configured_reconstruction_metadata_mismatch")
            return package
    raise ValueError("strategy_has_no_reviewed_executable_package")
