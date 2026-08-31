from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping, Sequence

from .backtest import BacktestResultV2, PriceMark, SimulatedTrade, simulate_portfolio, trade_net_pnl
from .broker_research import ResearchManifestRow, render_research_manifest
from .capital import PositionSizingRequest, PositionSizingResult, SizingMode, size_position
from .cognition import CognitionAssessment, CognitionPolicy, EntryCognitionRequest, derive_entry_cognition
from .core import (
    CoherenceResult,
    Decision,
    EvidenceItem,
    EvidenceSnapshot,
    HealthState,
    Person,
    check_coherence,
)
from .features import (
    FeatureBar,
    FeatureConfig,
    FeatureVector,
    completed_feature_bars_from_mt5,
    compute_standard_features,
)
from .forecasting import Forecast
from .markets import InstrumentEconomics
from .mt5worker import MT5BarRequest, ReadOnlyMT5Worker
from .risk import AccountRiskSnapshot, RiskAssessment, RiskConstitution, TradeRiskRequest, assess_trade_risk
from .runtime import CompiledStrategy, PriceRuleKind, RuntimeBar, RuntimeTrade, generate_runtime_trades
from .strategy_ir import assess_observed_entry_frequency, assess_strategy_eligibility


SessionResolver = Callable[[datetime], str]
EventBlockResolver = Callable[[datetime], bool]


_MT5_TIMEFRAME_MINUTES = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M6": 6,
    "M10": 10,
    "M12": 12,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 60,
    "H2": 120,
    "H3": 180,
    "H4": 240,
    "H6": 360,
    "H8": 480,
    "H12": 720,
    "D1": 1440,
}


@dataclass(frozen=True, slots=True)
class LaboratoryConfig:
    feature_config: FeatureConfig = FeatureConfig()
    cognition_policy: CognitionPolicy = CognitionPolicy()
    risk_constitution: RiskConstitution = RiskConstitution()
    strategy_test_equity: float = 100_000.0
    growth_starting_equity: float = 10_000.0
    growth_risk_fraction: float = 0.0025
    spread_price: float = 0.0
    expected_slippage_price: float = 0.0
    commission_per_lot: float | None = None
    max_evidence_items: int = 64

    def __post_init__(self) -> None:
        values = (
            self.strategy_test_equity,
            self.growth_starting_equity,
            self.growth_risk_fraction,
            self.spread_price,
            self.expected_slippage_price,
        )
        if self.commission_per_lot is not None:
            values += (self.commission_per_lot,)
        if any(not math.isfinite(value) for value in values):
            raise ValueError("laboratory financial inputs must be finite")
        if self.strategy_test_equity <= 0 or self.growth_starting_equity <= 0:
            raise ValueError("laboratory equity must be positive")
        if not 0 < self.growth_risk_fraction <= 1:
            raise ValueError("growth risk fraction must be in (0,1]")
        if self.spread_price < 0 or self.expected_slippage_price < 0:
            raise ValueError("laboratory friction cannot be negative")
        if self.commission_per_lot is not None and self.commission_per_lot < 0:
            raise ValueError("commission cannot be negative")
        if self.max_evidence_items < 1:
            raise ValueError("max evidence items must be positive")


@dataclass(frozen=True, slots=True)
class CognitionTrace:
    at: datetime
    coherence: CoherenceResult
    assessment: CognitionAssessment
    decision: Decision


@dataclass(frozen=True, slots=True)
class GrowthSizingTrace:
    trade_id: str
    equity_before: float
    risk: RiskAssessment
    sizing: PositionSizingResult | None
    approved: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LaboratoryRun:
    strategy_hash: str
    bar_count: int
    feature_count: int
    cognition: tuple[CognitionTrace, ...]
    potential_trades: tuple[RuntimeTrade, ...]
    minimum_lot_backtest: BacktestResultV2
    growth_backtest: BacktestResultV2
    growth_sizing: tuple[GrowthSizingTrace, ...]
    minimum_lot_manifest: str
    growth_manifest: str
    mt5_manifest_supported: bool = True
    mt5_manifest_reasons: tuple[str, ...] = ()

    @property
    def cognition_authorized_entries(self) -> int:
        return sum(trace.decision in {Decision.ENTRY_LONG, Decision.ENTRY_SHORT} for trace in self.cognition)


def _required_feature_keys(strategy: CompiledStrategy) -> tuple[str, ...]:
    keys = {clause.feature for group in strategy.spec.entry_groups for clause in group.clauses}
    if any(rule.kind is PriceRuleKind.ATR for rule in (strategy.stop, strategy.target, strategy.trailing)):
        keys.add("atr")
    return tuple(sorted(keys))


def _coherence(vector: FeatureVector, required: Sequence[str], *, max_items: int) -> CoherenceResult:
    items = tuple(
        EvidenceItem(
            key=key,
            value=value,
            source="dusty_feature_engine",
            observed_at=vector.at,
            category="market_feature",
            provenance="mt5_completed_bars->dusty_features",
        )
        for key, value in vector.values
    )
    return check_coherence(EvidenceSnapshot.of(f"features:{vector.at.isoformat()}", items), at=vector.at, required_keys=required, max_items=max_items)


def _baseline_risk(equity: float, risk_fraction: float, constitution: RiskConstitution) -> RiskAssessment:
    snapshot = AccountRiskSnapshot(equity, equity, equity, equity, equity, 0.0, 0.0, 0.0)
    request = TradeRiskRequest(
        proposed_risk=risk_fraction,
        post_trade_portfolio_heat=risk_fraction,
        post_trade_same_symbol_heat=risk_fraction,
        post_trade_margin_used=0.0,
        has_initial_stop=True,
    )
    return assess_trade_risk(snapshot, request, constitution)


def _friction_cost_per_lot(config: LaboratoryConfig, economics: InstrumentEconomics) -> float:
    commission = economics.commission_per_lot if config.commission_per_lot is None else config.commission_per_lot
    movement = (config.spread_price + config.expected_slippage_price) / economics.tick_size * economics.tick_value
    return movement + commission


def _simulated_trade(
    trade_id: str,
    runtime: RuntimeTrade,
    *,
    symbol: str,
    volume: float,
    cost_per_lot: float,
) -> SimulatedTrade:
    return SimulatedTrade(
        trade_id,
        symbol,
        runtime.side,
        runtime.entry_at,
        runtime.exit_at,
        runtime.entry_price,
        runtime.exit_price,
        volume,
        entry_cost=cost_per_lot * volume,
    )


def _manifest_row(trade_id: str, runtime: RuntimeTrade, volume: float) -> ResearchManifestRow:
    return ResearchManifestRow(
        trade_id,
        runtime.entry_at,
        runtime.exit_at,
        runtime.side,
        volume,
        runtime.stop_price,
        runtime.target_price or 0.0,
    )


def _manifest_support(strategy: CompiledStrategy) -> tuple[bool, tuple[str, ...]]:
    """State exactly what the current tester manifest can reproduce.

    Dynamic trailing/breakeven changes require an ordered protection-action manifest. Until that exists,
    Python may research those strategies, but MT5 parity is explicitly unavailable rather than falsely
    claimed from only the initial SL/TP and planned close time.
    """
    reasons: list[str] = []
    if strategy.trailing.kind is not PriceRuleKind.OFF:
        reasons.append("dynamic_trailing_manifest_not_supported")
    if strategy.breakeven_rr is not None:
        reasons.append("dynamic_breakeven_manifest_not_supported")
    return not reasons, tuple(reasons)


def _growth_stage(
    trades: Sequence[RuntimeTrade],
    bars: Sequence[FeatureBar],
    *,
    symbol: str,
    economics: InstrumentEconomics,
    config: LaboratoryConfig,
) -> tuple[BacktestResultV2, tuple[GrowthSizingTrace, ...], str]:
    equity = config.growth_starting_equity
    high_water = equity
    day_key = None
    week_key = None
    day_start = equity
    week_start = equity
    cost_per_lot = _friction_cost_per_lot(config, economics)
    approved_trades: list[SimulatedTrade] = []
    manifest: list[ResearchManifestRow] = []
    traces: list[GrowthSizingTrace] = []

    for index, runtime in enumerate(trades):
        trade_id = f"growth-{index:06d}"
        current_day = runtime.entry_at.date()
        current_week = runtime.entry_at.isocalendar()[:2]
        if day_key != current_day:
            day_key = current_day
            day_start = equity
        if week_key != current_week:
            week_key = current_week
            week_start = equity
        if equity <= 0:
            traces.append(GrowthSizingTrace(trade_id, equity, _baseline_risk(max(config.growth_starting_equity, 1.0), config.growth_risk_fraction, config.risk_constitution), None, False, ("equity_depleted",)))
            continue

        snapshot = AccountRiskSnapshot(equity, equity, high_water, day_start, week_start, 0.0, 0.0, 0.0)
        preliminary = assess_trade_risk(
            snapshot,
            TradeRiskRequest(
                proposed_risk=config.growth_risk_fraction,
                post_trade_portfolio_heat=config.growth_risk_fraction,
                post_trade_same_symbol_heat=config.growth_risk_fraction,
                post_trade_margin_used=0.0,
                has_initial_stop=True,
            ),
            config.risk_constitution,
        )
        effective_request_risk = config.growth_risk_fraction * preliminary.risk_multiplier
        if not preliminary.allowed or effective_request_risk <= 0:
            traces.append(GrowthSizingTrace(trade_id, equity, preliminary, None, False, preliminary.reasons or ("risk_multiplier_zero",)))
            continue

        sizing_request = PositionSizingRequest(
            equity=equity,
            risk_fraction=effective_request_risk,
            entry_price=runtime.entry_price,
            stop_price=runtime.stop_price,
            economics=economics,
            spread_price=config.spread_price,
            expected_slippage_price=config.expected_slippage_price,
            commission_per_lot=config.commission_per_lot,
        )
        sizing = size_position(sizing_request, mode=SizingMode.GROWTH_RISK)
        if not sizing.feasible or sizing.approved_volume <= 0:
            traces.append(GrowthSizingTrace(trade_id, equity, preliminary, sizing, False, sizing.reasons))
            continue

        margin = runtime.entry_price * economics.contract_size * sizing.approved_volume * economics.margin_rate
        final_risk = assess_trade_risk(
            snapshot,
            TradeRiskRequest(
                proposed_risk=sizing.effective_risk_fraction,
                post_trade_portfolio_heat=sizing.effective_risk_fraction,
                post_trade_same_symbol_heat=sizing.effective_risk_fraction,
                post_trade_margin_used=margin,
                has_initial_stop=True,
            ),
            config.risk_constitution,
        )
        if not final_risk.allowed:
            traces.append(GrowthSizingTrace(trade_id, equity, final_risk, sizing, False, final_risk.reasons))
            continue

        simulated = _simulated_trade(trade_id, runtime, symbol=symbol, volume=sizing.approved_volume, cost_per_lot=cost_per_lot)
        approved_trades.append(simulated)
        manifest.append(_manifest_row(trade_id, runtime, sizing.approved_volume))
        traces.append(GrowthSizingTrace(trade_id, equity, final_risk, sizing, True, ()))
        equity += trade_net_pnl(simulated, economics)
        high_water = max(high_water, equity)

    marks = tuple(PriceMark(bar.at, symbol, bar.close) for bar in bars)
    result = simulate_portfolio(approved_trades, marks, {symbol: economics}, starting_equity=config.growth_starting_equity)
    return result, tuple(traces), render_research_manifest(manifest)


def run_laboratory_from_bars(
    strategy: CompiledStrategy,
    bars: Iterable[FeatureBar],
    *,
    symbol: str,
    economics: InstrumentEconomics,
    config: LaboratoryConfig = LaboratoryConfig(),
    forecasts_by_time: Mapping[datetime, Sequence[Forecast]] | None = None,
    session_resolver: SessionResolver | None = None,
    event_block_resolver: EventBlockResolver | None = None,
    health: HealthState = HealthState.HEALTHY,
) -> LaboratoryRun:
    """Reference chain from completed bars through cognition, two-stage sizing, and MT5 manifests.

    This is deliberately a single-symbol, single-position laboratory. It proves semantic wiring;
    portfolio concurrency remains owned by the separate portfolio/backtest layers.
    """
    eligibility = assess_strategy_eligibility(strategy.spec)
    if not eligibility.promotable:
        raise ValueError(f"strategy_not_execution_eligible:{','.join(eligibility.reasons)}")
    feature_bars = tuple(bars)
    if not symbol.strip() or not feature_bars:
        raise ValueError("laboratory requires symbol and completed bars")
    features = compute_standard_features(feature_bars, config.feature_config)
    required = _required_feature_keys(strategy)
    baseline_risk = _baseline_risk(config.growth_starting_equity, config.growth_risk_fraction, config.risk_constitution)
    forecast_map = forecasts_by_time or {}
    decisions: dict[datetime, Decision] = {}
    traces: list[CognitionTrace] = []
    runtime_bars: list[RuntimeBar] = []

    for bar, vector in zip(feature_bars, features, strict=True):
        session = session_resolver(bar.at) if session_resolver else ""
        event_blocked = event_block_resolver(bar.at) if event_block_resolver else False
        coherence = _coherence(vector, required, max_items=config.max_evidence_items)
        assessment = derive_entry_cognition(
            EntryCognitionRequest.of(
                strategy=strategy,
                features=vector.feature_map(),
                coherence=coherence,
                risk=baseline_risk,
                health=health,
                session=session,
                event_blocked=event_blocked,
                spread_points=bar.spread_points,
                forecasts=tuple(forecast_map.get(bar.at, ())),
            ),
            config.cognition_policy,
        )
        decision = Person("lab", symbol.upper(), strategy.spec.strategy_id).reason(assessment.cognition, coherence)
        decisions[bar.at] = decision
        traces.append(CognitionTrace(bar.at, coherence, assessment, decision))
        runtime_bars.append(RuntimeBar.of(bar.at, open=bar.open, high=bar.high, low=bar.low, close=bar.close, features=vector.feature_map(), session=session, event_blocked=event_blocked))

    expected_entry = Decision.ENTRY_LONG if strategy.spec.direction.value == "long" else Decision.ENTRY_SHORT
    potential = generate_runtime_trades(strategy, runtime_bars, entry_authorizer=lambda row, _: decisions.get(row.at) is expected_entry)
    frequency = assess_observed_entry_frequency(trade.entry_at for trade in potential)
    if not frequency.promotable:
        raise ValueError(f"observed_entry_frequency_not_eligible:{','.join(frequency.reasons)}")

    cost_per_lot = _friction_cost_per_lot(config, economics)
    minimum_simulated = tuple(
        _simulated_trade(f"minimum-{index:06d}", trade, symbol=symbol, volume=economics.volume_min, cost_per_lot=cost_per_lot)
        for index, trade in enumerate(potential)
    )
    marks = tuple(PriceMark(bar.at, symbol, bar.close) for bar in feature_bars)
    minimum_result = simulate_portfolio(minimum_simulated, marks, {symbol: economics}, starting_equity=config.strategy_test_equity)
    minimum_manifest = render_research_manifest(
        _manifest_row(f"minimum-{index:06d}", trade, economics.volume_min) for index, trade in enumerate(potential)
    )
    growth_result, growth_sizing, growth_manifest = _growth_stage(potential, feature_bars, symbol=symbol, economics=economics, config=config)
    manifest_supported, manifest_reasons = _manifest_support(strategy)
    if not manifest_supported:
        minimum_manifest = ""
        growth_manifest = ""
    return LaboratoryRun(
        strategy.strategy_hash,
        len(feature_bars),
        len(features),
        tuple(traces),
        potential,
        minimum_result,
        growth_result,
        growth_sizing,
        minimum_manifest,
        growth_manifest,
        manifest_supported,
        manifest_reasons,
    )


def run_laboratory_from_mt5(
    worker: ReadOnlyMT5Worker,
    request: MT5BarRequest,
    strategy: CompiledStrategy,
    *,
    economics: InstrumentEconomics,
    config: LaboratoryConfig = LaboratoryConfig(),
    forecasts_by_time: Mapping[datetime, Sequence[Forecast]] | None = None,
    session_resolver: SessionResolver | None = None,
    event_block_resolver: EventBlockResolver | None = None,
    health: HealthState = HealthState.HEALTHY,
) -> LaboratoryRun:
    requested_minutes = _MT5_TIMEFRAME_MINUTES.get(request.timeframe.upper())
    if requested_minutes is None:
        raise ValueError("investment laboratory requires an explicitly mapped intraday/daily MT5 timeframe")
    if requested_minutes != strategy.spec.decision_timeframe_minutes:
        raise ValueError("strategy decision timeframe does not match MT5 history timeframe")
    raw_bars = tuple(worker.stream_bars(request))
    bars = completed_feature_bars_from_mt5(raw_bars)
    if not bars:
        raise ValueError("MT5 history did not contain two chronological bars needed to prove completion")
    return run_laboratory_from_bars(
        strategy,
        bars,
        symbol=request.symbol,
        economics=economics,
        config=config,
        forecasts_by_time=forecasts_by_time,
        session_resolver=session_resolver,
        event_block_resolver=event_block_resolver,
        health=health,
    )
