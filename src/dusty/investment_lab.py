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
from .research_eligibility import EntryPolicy, entry_eligibility
from .markets import InstrumentEconomics
from .mt5worker import MT5BarRequest, ReadOnlyMT5Worker
from .risk import AccountRiskSnapshot, RiskAssessment, RiskConstitution, TradeRiskRequest, assess_trade_risk
from .runtime import CompiledStrategy, PriceRuleKind, RuntimeBar, RuntimeTrade, generate_runtime_trades
from .strategy_ir import assess_observed_entry_frequency, assess_strategy_eligibility
from .tester_parity import ExpectedExecutionEnvelope, expected_execution_envelopes


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
    spread_price_used: float = 0.0
    spread_basis: str = "configured_spread_price"
    expected_net_pnl: float | None = None


@dataclass(frozen=True, slots=True)
class LaboratoryRun:
    strategy_hash: str
    bar_count: int
    feature_count: int
    feature_bars: tuple[FeatureBar, ...]
    cognition: tuple[CognitionTrace, ...]
    potential_trades: tuple[RuntimeTrade, ...]
    minimum_lot_backtest: BacktestResultV2
    growth_backtest: BacktestResultV2
    growth_sizing: tuple[GrowthSizingTrace, ...]
    minimum_lot_manifest: str
    growth_manifest: str
    mt5_manifest_supported: bool = True
    mt5_manifest_reasons: tuple[str, ...] = ()
    spread_cost_bases: tuple[str, ...] = ()

    @property
    def cognition_authorized_entries(self) -> int:
        return sum(
            trace.decision in {Decision.ENTRY_LONG, Decision.ENTRY_SHORT}
            for trace in self.cognition
        )

    def growth_execution_envelopes(
        self,
    ) -> tuple[ExpectedExecutionEnvelope, ...]:
        """Build native-tester expectations directly from this exact laboratory run.

        Only growth trades that survived cognition, risk and broker-volume sizing are exported. Their
        identifiers, volumes and ex-ante cash expectations are the same values used by the growth
        manifest and backtest, preventing a caller from manually rebuilding a more favorable parity
        input after observing native MT5 results.
        """
        if not self.mt5_manifest_supported:
            raise ValueError("MT5 execution envelopes require a supported tester manifest")
        if len(self.potential_trades) != len(self.growth_sizing):
            raise ValueError("laboratory growth traces do not align with potential trades")
        approved = tuple(
            (trade, trace)
            for trade, trace in zip(
                self.potential_trades,
                self.growth_sizing,
                strict=True,
            )
            if trace.approved
        )
        if any(
            trace.sizing is None or trace.expected_net_pnl is None
            for _, trace in approved
        ):
            raise ValueError("approved growth trace lacks sizing or ex-ante net PnL")
        volumes: list[float] = []
        net_pnls: list[float] = []
        for _, trace in approved:
            sizing = trace.sizing
            expected_net_pnl = trace.expected_net_pnl
            if sizing is None or expected_net_pnl is None:
                raise AssertionError("validated growth trace became incomplete")
            volumes.append(sizing.approved_volume)
            net_pnls.append(expected_net_pnl)
        return expected_execution_envelopes(
            tuple(trade for trade, _ in approved),
            self.feature_bars,
            strategy_hash=self.strategy_hash,
            trade_ids=tuple(trace.trade_id for _, trace in approved),
            volumes=tuple(volumes),
            expected_net_pnls=tuple(net_pnls),
        )


def _required_feature_keys(strategy: CompiledStrategy) -> tuple[str, ...]:
    keys = {clause.feature for group in strategy.spec.entry_groups for clause in group.clauses}
    if any(
        rule.kind is PriceRuleKind.ATR
        for rule in (strategy.stop, strategy.target, strategy.trailing)
    ):
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
    return check_coherence(
        EvidenceSnapshot.of(f"features:{vector.at.isoformat()}", items),
        at=vector.at,
        required_keys=required,
        max_items=max_items,
    )


def _baseline_risk(
    equity: float,
    risk_fraction: float,
    constitution: RiskConstitution,
) -> RiskAssessment:
    snapshot = AccountRiskSnapshot(equity, equity, equity, equity, equity, 0.0, 0.0, 0.0)
    request = TradeRiskRequest(
        proposed_risk=risk_fraction,
        post_trade_portfolio_heat=risk_fraction,
        post_trade_same_symbol_heat=risk_fraction,
        post_trade_margin_used=0.0,
        has_initial_stop=True,
    )
    return assess_trade_risk(snapshot, request, constitution)


def _spread_price_for_entry(
    runtime: RuntimeTrade,
    bars_by_at: Mapping[datetime, FeatureBar],
    *,
    config: LaboratoryConfig,
    economics: InstrumentEconomics,
) -> tuple[float, str]:
    """Choose a conservative research spread without pretending bar data is a native quote.

    ``config.spread_price`` is always a floor. When an MT5-derived completed bar carries the following
    bar's spread proxy and broker point size is known, the larger of that proxy-price and the configured
    floor is charged. Exact Ask-Bid/tick execution remains a native tester concern.
    """
    bar = bars_by_at.get(runtime.entry_at)
    if bar is None or bar.decision_spread_proxy_points is None:
        return config.spread_price, "configured_spread_price"
    if economics.point_size <= 0:
        return config.spread_price, "configured_spread_price_point_size_unavailable"
    proxy_price = bar.decision_spread_proxy_points * economics.point_size
    return (
        max(config.spread_price, proxy_price),
        "mt5_availability_bar_spread_proxy_with_configured_floor",
    )


def _friction_cost_per_lot(
    config: LaboratoryConfig,
    economics: InstrumentEconomics,
    *,
    spread_price: float,
) -> float:
    commission = (
        economics.commission_per_lot
        if config.commission_per_lot is None
        else config.commission_per_lot
    )
    movement = (
        (spread_price + config.expected_slippage_price)
        / economics.tick_size
        * economics.tick_value
    )
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


def _market_marks(bars: Sequence[FeatureBar], symbol: str) -> tuple[PriceMark, ...]:
    """Mark equity only with a price observable at the decision timestamp."""
    return tuple(
        PriceMark(bar.at, symbol, bar.market_price_at_availability)
        for bar in bars
    )


def _growth_stage(
    trades: Sequence[RuntimeTrade],
    bars: Sequence[FeatureBar],
    *,
    symbol: str,
    economics: InstrumentEconomics,
    config: LaboratoryConfig,
) -> tuple[
    BacktestResultV2,
    tuple[GrowthSizingTrace, ...],
    str,
    tuple[str, ...],
]:
    equity = config.growth_starting_equity
    high_water = equity
    day_key = None
    week_key = None
    day_start = equity
    week_start = equity
    approved_trades: list[SimulatedTrade] = []
    manifest: list[ResearchManifestRow] = []
    traces: list[GrowthSizingTrace] = []
    spread_bases: set[str] = set()
    bars_by_at = {bar.at: bar for bar in bars}

    for index, runtime in enumerate(trades):
        trade_id = f"growth-{index:06d}"
        spread_price, spread_basis = _spread_price_for_entry(
            runtime,
            bars_by_at,
            config=config,
            economics=economics,
        )
        spread_bases.add(spread_basis)
        current_day = runtime.entry_at.date()
        current_week = runtime.entry_at.isocalendar()[:2]
        if day_key != current_day:
            day_key = current_day
            day_start = equity
        if week_key != current_week:
            week_key = current_week
            week_start = equity
        if equity <= 0:
            traces.append(
                GrowthSizingTrace(
                    trade_id,
                    equity,
                    _baseline_risk(
                        max(config.growth_starting_equity, 1.0),
                        config.growth_risk_fraction,
                        config.risk_constitution,
                    ),
                    None,
                    False,
                    ("equity_depleted",),
                    spread_price,
                    spread_basis,
                )
            )
            continue

        snapshot = AccountRiskSnapshot(
            equity,
            equity,
            high_water,
            day_start,
            week_start,
            0.0,
            0.0,
            0.0,
        )
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
            traces.append(
                GrowthSizingTrace(
                    trade_id,
                    equity,
                    preliminary,
                    None,
                    False,
                    preliminary.reasons or ("risk_multiplier_zero",),
                    spread_price,
                    spread_basis,
                )
            )
            continue

        sizing_request = PositionSizingRequest(
            equity=equity,
            risk_fraction=effective_request_risk,
            entry_price=runtime.entry_price,
            stop_price=runtime.stop_price,
            economics=economics,
            spread_price=spread_price,
            expected_slippage_price=config.expected_slippage_price,
            commission_per_lot=config.commission_per_lot,
        )
        sizing = size_position(sizing_request, mode=SizingMode.GROWTH_RISK)
        if not sizing.feasible or sizing.approved_volume <= 0:
            traces.append(
                GrowthSizingTrace(
                    trade_id,
                    equity,
                    preliminary,
                    sizing,
                    False,
                    sizing.reasons,
                    spread_price,
                    spread_basis,
                )
            )
            continue

        margin = (
            runtime.entry_price
            * economics.contract_size
            * sizing.approved_volume
            * economics.margin_rate
        )
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
            traces.append(
                GrowthSizingTrace(
                    trade_id,
                    equity,
                    final_risk,
                    sizing,
                    False,
                    final_risk.reasons,
                    spread_price,
                    spread_basis,
                )
            )
            continue

        cost_per_lot = _friction_cost_per_lot(
            config,
            economics,
            spread_price=spread_price,
        )
        simulated = _simulated_trade(
            trade_id,
            runtime,
            symbol=symbol,
            volume=sizing.approved_volume,
            cost_per_lot=cost_per_lot,
        )
        approved_trades.append(simulated)
        manifest.append(_manifest_row(trade_id, runtime, sizing.approved_volume))
        expected_net_pnl = trade_net_pnl(simulated, economics)
        traces.append(
            GrowthSizingTrace(
                trade_id,
                equity,
                final_risk,
                sizing,
                True,
                (),
                spread_price,
                spread_basis,
                expected_net_pnl,
            )
        )
        equity += expected_net_pnl
        high_water = max(high_water, equity)

    result = simulate_portfolio(
        approved_trades,
        _market_marks(bars, symbol),
        {symbol: economics},
        starting_equity=config.growth_starting_equity,
    )
    return (
        result,
        tuple(traces),
        render_research_manifest(manifest),
        tuple(sorted(spread_bases)),
    )


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
    feature_warmup_bars: Iterable[FeatureBar] = (),
    entry_cutoff: datetime | None = None,
    entry_policy: EntryPolicy = EntryPolicy.SEED,
    require_forecasts: bool = False,
) -> LaboratoryRun:
    """Reference chain from completed bars through cognition, two-stage sizing, and MT5 manifests.

    This is deliberately a single-symbol, single-position laboratory. It proves semantic wiring;
    portfolio concurrency remains owned by the separate portfolio/backtest layers. Bar-level spread is
    always labeled as historical/proxy evidence; native tick/tester execution is the final authority.
    Optional past-only warm-up initializes indicators without creating cognition, positions, cash or
    ledger rows outside the scored bars. An entry cutoff can only veto new entries, never modify exits.
    """
    if not isinstance(entry_policy, EntryPolicy):
        raise ValueError("unknown_research_entry_policy")
    eligibility = assess_strategy_eligibility(strategy.spec)
    if not eligibility.promotable:
        raise ValueError(f"strategy_not_execution_eligible:{','.join(eligibility.reasons)}")
    feature_bars = tuple(bars)
    if not symbol.strip() or not feature_bars:
        raise ValueError("laboratory requires symbol and completed bars")
    warmup = tuple(feature_warmup_bars)
    if warmup and warmup[-1].at >= feature_bars[0].at:
        raise ValueError("feature_warmup_must_precede_scored_bars")
    if entry_cutoff is not None and (entry_cutoff.tzinfo is None or entry_cutoff.utcoffset() is None
                                    or not feature_bars[0].at < entry_cutoff <= feature_bars[-1].at):
        raise ValueError("entry_cutoff_must_be_aware_and_inside_scored_bars")
    features = compute_standard_features(warmup + feature_bars, config.feature_config)[len(warmup):]
    entry_permissions = {vector.at: entry_eligibility(vector, strategy.spec.direction, entry_policy).allowed
                         for vector in features}
    required = _required_feature_keys(strategy)
    baseline_risk = _baseline_risk(
        config.growth_starting_equity,
        config.growth_risk_fraction,
        config.risk_constitution,
    )
    forecast_map = forecasts_by_time or {}
    if type(require_forecasts) is not bool:
        raise ValueError("require_forecasts_must_be_boolean")
    for bar in feature_bars:
        for forecast in forecast_map.get(bar.at, ()):
            if forecast.at != bar.at or not math.isclose(forecast.origin, bar.close, rel_tol=1e-12):
                raise ValueError("forecast_timestamp_or_origin_mismatch")
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
                spread_points=bar.spread_points_for_guardian,
                forecasts=tuple(forecast_map.get(bar.at, ())),
                reasoning_at=vector.at,
            ),
            config.cognition_policy,
        )
        decision = Person("lab", symbol.upper(), strategy.spec.strategy_id).reason(
            assessment.cognition,
            coherence,
        )
        decisions[bar.at] = decision
        traces.append(CognitionTrace(bar.at, coherence, assessment, decision))
        runtime_bars.append(
            RuntimeBar.of(
                bar.at,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                features=vector.feature_map(),
                session=session,
                event_blocked=event_blocked,
                execution_price=bar.market_price_at_availability,
            )
        )

    expected_entry = (
        Decision.ENTRY_LONG
        if strategy.spec.direction.value == "long"
        else Decision.ENTRY_SHORT
    )
    potential = generate_runtime_trades(
        strategy,
        runtime_bars,
        entry_authorizer=lambda row, _: (decisions.get(row.at) is expected_entry
                                        and entry_permissions[row.at]
                                        and (not require_forecasts or bool(forecast_map.get(row.at)))
                                        and (entry_cutoff is None or row.at < entry_cutoff)),
    )
    frequency = assess_observed_entry_frequency(trade.entry_at for trade in potential)
    if not frequency.promotable:
        raise ValueError(f"observed_entry_frequency_not_eligible:{','.join(frequency.reasons)}")

    bars_by_at = {bar.at: bar for bar in feature_bars}
    minimum_simulated: list[SimulatedTrade] = []
    spread_bases: set[str] = set()
    for index, trade in enumerate(potential):
        spread_price, spread_basis = _spread_price_for_entry(
            trade,
            bars_by_at,
            config=config,
            economics=economics,
        )
        spread_bases.add(spread_basis)
        minimum_simulated.append(
            _simulated_trade(
                f"minimum-{index:06d}",
                trade,
                symbol=symbol,
                volume=economics.volume_min,
                cost_per_lot=_friction_cost_per_lot(
                    config,
                    economics,
                    spread_price=spread_price,
                ),
            )
        )
    minimum_result = simulate_portfolio(
        minimum_simulated,
        _market_marks(feature_bars, symbol),
        {symbol: economics},
        starting_equity=config.strategy_test_equity,
    )
    minimum_manifest = render_research_manifest(
        _manifest_row(f"minimum-{index:06d}", trade, economics.volume_min)
        for index, trade in enumerate(potential)
    )
    (
        growth_result,
        growth_sizing,
        growth_manifest,
        growth_spread_bases,
    ) = _growth_stage(
        potential,
        feature_bars,
        symbol=symbol,
        economics=economics,
        config=config,
    )
    spread_bases.update(growth_spread_bases)
    manifest_supported, manifest_reasons = _manifest_support(strategy)
    if entry_policy is not EntryPolicy.SEED:
        # The legacy manifest identity is just the base strategy hash, not the veto.
        manifest_supported = False
        manifest_reasons += ("research_entry_policy_not_bound_by_native_manifest",)
    if forecasts_by_time is not None or require_forecasts:
        manifest_supported = False
        manifest_reasons += ("research_forecast_not_bound_by_native_manifest",)
    if not manifest_supported:
        minimum_manifest = ""
        growth_manifest = ""
    return LaboratoryRun(
        strategy.strategy_hash,
        len(feature_bars),
        len(features),
        feature_bars,
        tuple(traces),
        potential,
        minimum_result,
        growth_result,
        growth_sizing,
        minimum_manifest,
        growth_manifest,
        manifest_supported,
        manifest_reasons,
        tuple(sorted(spread_bases)),
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
        raise ValueError(
            "investment laboratory requires an explicitly mapped intraday/daily MT5 timeframe"
        )
    if requested_minutes != strategy.spec.decision_timeframe_minutes:
        raise ValueError("strategy decision timeframe does not match MT5 history timeframe")
    raw_bars = tuple(worker.stream_bars(request))
    bars = completed_feature_bars_from_mt5(raw_bars)
    if not bars:
        raise ValueError(
            "MT5 history did not contain two chronological bars needed to prove completion"
        )
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
