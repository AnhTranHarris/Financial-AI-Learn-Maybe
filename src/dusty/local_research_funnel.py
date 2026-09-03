"""Local read-only adapter for the M107 unified research funnel.

This module deliberately sits beside the certified legacy local research runtime. It reuses
that runtime's selection validation and history reader, but routes the bounded acquisition
through the checkpointed M107 funnel. Nothing here changes desktop routing, sends an order,
or launches the native Strategy Tester; the fidelity queue remains proposal-only evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from .local_app import RuntimeSelection
from .local_research import ResearchSettings, SelectedTerminalHistoryReader, validate_research_selection
from .markets import InstrumentEconomics
from .mt5worker import MT5Bar
from .research_challengers import ChallengerPlan
from .research_cycle import ResearchCycleResult
from .research_funnel import (
    FunnelLaboratoryPolicy,
    FunnelPolicy,
    FunnelScreenPolicy,
    UnifiedResearchFunnel,
    compact_funnel_report,
    decode_acquisition,
    first_generation_challenger_plan,
)
from .reviewed_strategies import resolve_research_package


LOCAL_FUNNEL_ADAPTER_PROTOCOL = "dusty-local-unified-funnel-adapter-v1"


@dataclass(frozen=True, slots=True)
class LocalResearchFunnelRun:
    cycle: ResearchCycleResult
    bars: tuple[MT5Bar, ...]
    economics: InstrumentEconomics
    report: dict[str, Any]


def run_local_research_funnel(
    selection: RuntimeSelection,
    settings: ResearchSettings,
    cache_root: Path,
    start: datetime,
    end: datetime,
    *,
    reader: SelectedTerminalHistoryReader | None = None,
    challenger_plan: ChallengerPlan | None = None,
    screen_policy: FunnelScreenPolicy = FunnelScreenPolicy(),
    additional_stress_points: float = 10.0,
) -> LocalResearchFunnelRun:
    """Run the checkpointed funnel against one already-bound local MT5 research selection.

    The adapter is intentionally opt-in and separate from ``execute_research`` in M107. That keeps
    the certified desktop path unchanged while proving that the new engine can reuse the exact same
    read-only MT5 acquisition contract. A future routing milestone can switch the desktop coordinator
    only after this adapter has its own certification evidence.
    """
    validate_research_selection(selection)
    plan = settings.evaluation_plan(start, end)
    if plan is None:
        raise ValueError("unified_funnel_requires_fixed_holdout_plan")
    if settings.comparison:
        raise ValueError("unified_funnel_is_alternative_to_legacy_comparison")
    if not settings.cost_source.strip():
        raise ValueError("unified_funnel_requires_cost_source_note")

    package = resolve_research_package(selection.strategy)
    plan_candidates = challenger_plan or first_generation_challenger_plan(package)
    policy = FunnelPolicy(
        laboratory=FunnelLaboratoryPolicy(
            growth_starting_equity=selection.terminal.account.balance,
            strategy_test_equity=100_000.0,
            growth_risk_fraction=0.0025,
            commission_per_lot=settings.commission_per_lot,
            spread_floor_points=settings.spread_floor_points,
            slippage_points=settings.slippage_points,
        ),
        screen=screen_policy,
        additional_round_trip_slippage_points=additional_stress_points,
    )
    history_reader = reader or SelectedTerminalHistoryReader()
    request = {
        "schema": 1,
        "adapter_protocol": LOCAL_FUNNEL_ADAPTER_PROTOCOL,
        "code_commit": selection.binding.code_commit,
        "binding_fingerprint": selection.binding.fingerprint,
        "package_fingerprint": package.fingerprint,
        "symbol": selection.symbol.symbol,
        "timeframe": "M15",
        "start": start,
        "end": end,
        "account_mode": selection.terminal.account.mode,
        "account_currency": selection.terminal.account.currency,
        "cost_source_sha256": sha256(settings.cost_source.strip().encode("utf-8")).hexdigest(),
    }

    funnel = UnifiedResearchFunnel(cache_root)
    cycle = funnel.run(
        request,
        parent=package,
        challenger_plan=plan_candidates,
        evaluation_plan=plan,
        policy=policy,
        acquire=lambda: history_reader.read(selection, start, end),
    )
    bars, economics = decode_acquisition(cycle)
    report = compact_funnel_report(cycle)
    report["local_adapter"] = {
        "protocol": LOCAL_FUNNEL_ADAPTER_PROTOCOL,
        "binding_fingerprint": selection.binding.fingerprint,
        "package_fingerprint": package.fingerprint,
        "cost_source_sha256": request["cost_source_sha256"],
        "broker_cost_observation": getattr(history_reader, "cost_observation", None),
        "legacy_desktop_routing_changed": False,
        "broker_write_authorized": False,
        "native_tester_launched": False,
    }
    return LocalResearchFunnelRun(cycle, bars, economics, report)
