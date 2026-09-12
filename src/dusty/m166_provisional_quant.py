from __future__ import annotations

"""Deterministic provisional M166-M173 research execution.

This module accelerates the quant robustness lane while production M165 remains
incomplete. It runs only from frozen local evidence and owns no broker, live,
promotion, retry, custody, or risk authority. Production admission/certification
remain separate fail-closed gates.
"""

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
from typing import Iterable, Mapping

from .features import completed_feature_bars_from_mt5, compute_standard_features
from .forward_decay import PerformanceEvidence, measure_historical_forward_decay
from .m166_research_identity import parameter_fingerprint
from .mt5worker import MT5Bar
from .parameter_stability import ParameterPointResult, assess_parameter_neighborhood
from .purged_validation import TemporalSample, build_purged_temporal_split
from .regime_torture import RegimeDefinition, RegimeSliceResult, assess_regime_torture
from .research import Clause
from .research_sessions import SESSION_EVIDENCE_PROTOCOL, matching_research_session
from .runtime import RuntimeBar, RuntimeTrade, compile_strategy, generate_runtime_trades
from .strategy_ir import ExitPlan, RuleGroup, StrategySpecV2
from .tail_risk import analyze_tail_risk
from .walk_forward_lab import (
    WalkForwardFoldResult,
    WalkForwardMode,
    build_walk_forward_plan,
    summarize_walk_forward,
)

UTC = timezone.utc


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ProvisionalQuantPolicy:
    """Explicit provisional-only research policy; never a production threshold."""

    train_days: int = 365
    test_days: int = 30
    minimum_trades_per_fold: int = 5
    minimum_total_trades: int = 30
    maximum_drawdown: float = 0.25
    neighbor_fraction: float = 0.05

    def __post_init__(self) -> None:
        if not 30 <= int(self.train_days) <= 3650:
            raise ValueError("train_days out of range")
        if not 5 <= int(self.test_days) <= 365:
            raise ValueError("test_days out of range")
        if not 1 <= int(self.minimum_trades_per_fold) <= 100000:
            raise ValueError("minimum_trades_per_fold out of range")
        if not 1 <= int(self.minimum_total_trades) <= 1000000:
            raise ValueError("minimum_total_trades out of range")
        if not math.isfinite(self.maximum_drawdown) or not 0 < self.maximum_drawdown < 1:
            raise ValueError("maximum_drawdown must be in (0,1)")
        if not math.isfinite(self.neighbor_fraction) or not 0 < self.neighbor_fraction <= 0.25:
            raise ValueError("neighbor_fraction must be in (0,.25]")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m166-provisional-quant-policy-v1",
            "train_days": self.train_days,
            "test_days": self.test_days,
            "minimum_trades_per_fold": self.minimum_trades_per_fold,
            "minimum_total_trades": self.minimum_total_trades,
            "maximum_drawdown": self.maximum_drawdown,
            "neighbor_fraction": self.neighbor_fraction,
            "session_evidence_protocol": SESSION_EVIDENCE_PROTOCOL,
            "production_semantics": False,
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


@dataclass(frozen=True, slots=True)
class EvaluatedTrade:
    entry_at: datetime
    exit_at: datetime
    return_fraction: float


@dataclass(frozen=True, slots=True)
class WindowEvaluation:
    net_return: float
    max_drawdown: float
    trades: tuple[EvaluatedTrade, ...]

    @property
    def trade_count(self) -> int:
        return len(self.trades)


def _trade_return(trade: RuntimeTrade) -> float:
    direction = 1.0 if trade.side.value == "long" else -1.0
    value = direction * (trade.exit_price - trade.entry_price) / trade.entry_price
    if not math.isfinite(value) or value <= -1.0:
        raise ValueError("runtime trade produced invalid research return")
    return value


def _path_metrics(values: Iterable[float]) -> tuple[float, float]:
    rows = tuple(float(value) for value in values)
    equity = peak = 1.0
    maximum_drawdown = 0.0
    for value in rows:
        if not math.isfinite(value) or value <= -1.0:
            raise ValueError("return path must be finite and greater than -1")
        equity *= 1.0 + value
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)
    return equity - 1.0, maximum_drawdown


def build_runtime_bars(bars: Iterable[MT5Bar]) -> tuple[RuntimeBar, ...]:
    raw = tuple(bars)
    completed = completed_feature_bars_from_mt5(raw)
    vectors = compute_standard_features(completed)
    if len(completed) != len(vectors):
        raise RuntimeError("feature/vector cardinality drift")
    runtime: list[RuntimeBar] = []
    for bar, vector in zip(completed, vectors):
        if bar.at != vector.at:
            raise RuntimeError("feature availability identity drift")
        runtime.append(
            RuntimeBar.of(
                bar.at,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                features=vector.feature_map(),
                execution_price=bar.execution_price,
            )
        )
    return tuple(runtime)


def _bind_session_evidence(
    rows: tuple[RuntimeBar, ...],
    session_filters: tuple[str, ...],
) -> tuple[RuntimeBar, ...]:
    if not session_filters:
        return rows
    return tuple(
        replace(row, session=matching_research_session(row.at, session_filters))
        for row in rows
    )


def evaluate_window(
    spec: StrategySpecV2,
    runtime_bars: Iterable[RuntimeBar],
    *,
    start: datetime,
    end: datetime,
) -> WindowEvaluation:
    if spec.event_exclusion_minutes:
        raise ValueError("provisional runner requires explicit event evidence for event-filtered strategy")
    compiled = compile_strategy(spec)
    rows = tuple(row for row in runtime_bars if start <= row.at < end)
    rows = _bind_session_evidence(rows, spec.session_filters)
    if not rows:
        return WindowEvaluation(0.0, 0.0, ())
    reserve = max(1, spec.exit_plan.max_hold_steps)
    allowed_entries = {row.at for row in rows[:-reserve]} if len(rows) > reserve else set()
    trades = generate_runtime_trades(
        compiled,
        rows,
        entry_authorizer=lambda bar, _compiled: bar.at in allowed_entries,
    )
    evaluated = tuple(EvaluatedTrade(row.entry_at, row.exit_at, _trade_return(row)) for row in trades)
    net_return, drawdown = _path_metrics(row.return_fraction for row in evaluated)
    return WindowEvaluation(net_return, drawdown, evaluated)


def run_m166(
    spec: StrategySpecV2,
    runtime_bars: tuple[RuntimeBar, ...],
    *,
    strategy_fingerprint: str,
    dataset_fingerprint: str,
    parameter_fp: str,
    policy: ProvisionalQuantPolicy,
) -> tuple[object, tuple[WalkForwardFoldResult, ...], object, tuple[EvaluatedTrade, ...]]:
    if not runtime_bars:
        raise ValueError("M166 requires runtime bars")
    plan = build_walk_forward_plan(
        strategy_execution_fingerprint=strategy_fingerprint,
        parameter_fingerprint=parameter_fp,
        dataset_fingerprint=dataset_fingerprint,
        start=runtime_bars[0].at,
        end=runtime_bars[-1].at,
        train_days=policy.train_days,
        test_days=policy.test_days,
        mode=WalkForwardMode.ANCHORED,
    )
    fold_results: list[WalkForwardFoldResult] = []
    all_trades: list[EvaluatedTrade] = []
    for window in plan.windows:
        result = evaluate_window(spec, runtime_bars, start=window.test_start, end=window.test_end)
        passed = (
            result.trade_count >= policy.minimum_trades_per_fold
            and result.net_return > 0.0
            and result.max_drawdown <= policy.maximum_drawdown
        )
        fold_results.append(
            WalkForwardFoldResult(
                plan.fingerprint,
                window.fingerprint,
                window.fold,
                result.net_return,
                result.max_drawdown,
                result.trade_count,
                passed,
            )
        )
        all_trades.extend(result.trades)
    rows = tuple(fold_results)
    return plan, rows, summarize_walk_forward(plan, rows), tuple(all_trades)


def run_m167(
    runtime_bars: tuple[RuntimeBar, ...],
    windows: Iterable[object],
    *,
    dataset_fingerprint: str,
    label_horizon_minutes: int,
) -> tuple[dict[str, object], ...]:
    horizon = timedelta(minutes=int(label_horizon_minutes))
    samples = tuple(
        TemporalSample(
            _digest({"dataset": dataset_fingerprint, "at": row.at.isoformat()}),
            row.at,
            row.at,
            row.at + horizon,
        )
        for row in runtime_bars
    )
    evidence: list[dict[str, object]] = []
    for window in windows:
        split = build_purged_temporal_split(
            samples,
            test_start=window.test_start,
            test_end=window.test_end,
            embargo_seconds=int(label_horizon_minutes) * 60,
        )
        evidence.append(
            {
                "fold": window.fold,
                "window_fingerprint": window.fingerprint,
                "split_fingerprint": split.fingerprint,
                "test_start": split.test_start.isoformat(),
                "test_end": split.test_end.isoformat(),
                "training_count": len(split.training),
                "test_count": len(split.test),
                "purged_count": len(split.purged),
                "embargoed_count": len(split.embargoed),
            }
        )
    return tuple(evidence)


def _numeric_clause_neighbors(spec: StrategySpecV2, fraction: float) -> tuple[StrategySpecV2, ...]:
    result: list[StrategySpecV2] = []
    for group_index, group in enumerate(spec.entry_groups):
        for clause_index, clause in enumerate(group.clauses):
            if isinstance(clause.value, bool) or not isinstance(clause.value, (int, float)):
                continue
            center = float(clause.value)
            scale = abs(center) if center else 1.0
            for sign in (-1.0, 1.0):
                changed = center + sign * scale * fraction
                clauses = list(group.clauses)
                clauses[clause_index] = replace(clause, value=changed)
                groups = list(spec.entry_groups)
                groups[group_index] = RuleGroup(tuple(clauses), group.mode)
                result.append(replace(spec, entry_groups=tuple(groups)))
    if spec.exit_plan.max_hold_steps > 1:
        result.append(replace(spec, exit_plan=replace(spec.exit_plan, max_hold_steps=spec.exit_plan.max_hold_steps - 1)))
    result.append(replace(spec, exit_plan=replace(spec.exit_plan, max_hold_steps=spec.exit_plan.max_hold_steps + 1)))
    unique: dict[str, StrategySpecV2] = {}
    for row in result:
        unique.setdefault(parameter_fingerprint(row), row)
    return tuple(unique[key] for key in sorted(unique))


def run_m168(
    spec: StrategySpecV2,
    runtime_bars: tuple[RuntimeBar, ...],
    windows: tuple[object, ...],
    *,
    center_parameter_fingerprint: str,
    center_trades: tuple[EvaluatedTrade, ...],
    policy: ProvisionalQuantPolicy,
) -> tuple[object, tuple[dict[str, object], ...]]:
    center_return, center_dd = _path_metrics(row.return_fraction for row in center_trades)
    center_pass = len(center_trades) >= policy.minimum_total_trades and center_return > 0 and center_dd <= policy.maximum_drawdown
    center = ParameterPointResult(center_parameter_fingerprint, 0.0, center_return, center_dd, center_pass)
    neighbors: list[ParameterPointResult] = []
    details: list[dict[str, object]] = []
    for neighbor in _numeric_clause_neighbors(spec, policy.neighbor_fraction):
        trades: list[EvaluatedTrade] = []
        for window in windows:
            trades.extend(evaluate_window(neighbor, runtime_bars, start=window.test_start, end=window.test_end).trades)
        score, drawdown = _path_metrics(row.return_fraction for row in trades)
        passed = len(trades) >= policy.minimum_total_trades and score > 0 and drawdown <= policy.maximum_drawdown
        fp = parameter_fingerprint(neighbor)
        neighbors.append(ParameterPointResult(fp, 1.0, score, drawdown, passed))
        details.append({"parameter_fingerprint": fp, "trade_count": len(trades), "score": score, "max_drawdown": drawdown, "passed": passed})
    return assess_parameter_neighborhood(center, tuple(neighbors)), tuple(details)


def _regime_definitions(available_at: datetime) -> tuple[RegimeDefinition, ...]:
    names = ("trend_above_sma", "trend_below_sma", "momentum_rsi_ge_50", "momentum_rsi_lt_50")
    return tuple(RegimeDefinition(name, _digest({"protocol": "dusty-provisional-fixed-regime-v1", "name": name}), available_at) for name in names)


def run_m169(
    trades: tuple[EvaluatedTrade, ...],
    runtime_bars: tuple[RuntimeBar, ...],
    *,
    policy: ProvisionalQuantPolicy,
) -> tuple[object, tuple[dict[str, object], ...]]:
    if not runtime_bars:
        raise ValueError("M169 requires runtime bars")
    features: Mapping[datetime, dict[str, object]] = {row.at: row.feature_map() for row in runtime_bars}
    definitions = _regime_definitions(runtime_bars[0].at)
    buckets: dict[str, list[float]] = {row.name: [] for row in definitions}
    for trade in trades:
        row = features.get(trade.entry_at, {})
        close = row.get("close")
        sma = row.get("sma")
        rsi = row.get("rsi")
        if isinstance(close, (int, float)) and isinstance(sma, (int, float)):
            buckets["trend_above_sma" if float(close) >= float(sma) else "trend_below_sma"].append(trade.return_fraction)
        if isinstance(rsi, (int, float)):
            buckets["momentum_rsi_ge_50" if float(rsi) >= 50.0 else "momentum_rsi_lt_50"].append(trade.return_fraction)
    results: list[RegimeSliceResult] = []
    details: list[dict[str, object]] = []
    by_name = {row.name: row for row in definitions}
    for name in sorted(buckets):
        values = buckets[name]
        net, drawdown = _path_metrics(values)
        passed = len(values) >= 20 and net > 0 and drawdown <= policy.maximum_drawdown
        definition = by_name[name]
        results.append(RegimeSliceResult(definition.definition_fingerprint, len(values), net, drawdown, passed))
        details.append({"regime": name, "sample_count": len(values), "net_return": net, "max_drawdown": drawdown, "passed": passed})
    return assess_regime_torture(definitions, tuple(results), evaluation_cutoff=runtime_bars[-1].at), tuple(details)


def run_m171(
    trades: tuple[EvaluatedTrade, ...],
    *,
    strategy_fingerprint: str,
    period_start: datetime,
    period_end: datetime,
) -> object | None:
    net_return, _ = _path_metrics(row.return_fraction for row in trades)
    if net_return <= 0:
        return None
    historical = PerformanceEvidence(
        _digest({"strategy": strategy_fingerprint, "trades": [(row.entry_at.isoformat(), row.exit_at.isoformat(), row.return_fraction) for row in trades]}),
        strategy_fingerprint,
        period_start,
        period_end,
        "compound_return",
        net_return,
        len(trades),
        False,
    )
    return measure_historical_forward_decay(historical, None)


def run_m172(trades: tuple[EvaluatedTrade, ...]) -> object:
    return analyze_tail_risk(tuple(row.return_fraction for row in trades))


def dataclass_payload(value: object) -> dict[str, object]:
    return asdict(value)  # type: ignore[arg-type]


broker_write_authority = False
live_write_authority = False
custody_write_authority = False
promotion_authority = False
retry_authority = False
risk_override_authority = False
