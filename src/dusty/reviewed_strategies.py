"""Small, code-reviewed research hypotheses, not profitable or certified strategies.

No downloaded Python/MQL is evaluated. Catalog metadata cannot supply executable rules.
Changing a rule, indicator period or policy creates a different package fingerprint.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json

from .cognition import CognitionPolicy
from .experience import TradeSide
from .features import FEATURE_NUMERICS_VERSION, FeatureConfig
from .research import Clause, RuleOp
from .runtime import CompiledStrategy, compile_strategy
from .strategy_catalog import StrategyCatalogEntry, StrategyStage
from .strategy_ir import ExitPlan, RuleGroup, StrategySpecV2


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


def resolve_research_package(entry: StrategyCatalogEntry) -> ReviewedResearchPackage:
    for package in reviewed_research_packages():
        if entry.strategy_id == package.spec.strategy_id:
            if entry != package.catalog_entry:
                raise ValueError("reviewed_package_metadata_mismatch")
            return package
    raise ValueError("strategy_has_no_reviewed_executable_package")
