from __future__ import annotations

"""M186 independent demo-desk capital allocation.

This layer composes the existing M94 capital-opportunity allocator with explicit
per-desk accounting and active reservations.  It never pools demo balances,
never transfers P&L between desks, and never treats a new session as a way to
forget open risk.  M186 grants no live-write or promotion authority.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Iterable

from .capital_opportunity import (
    CapitalOpportunityDecision,
    CapitalOpportunityPolicy,
    CertifiedOpportunity,
    SettledCapitalState,
    allocate_certified_opportunities,
)


DEFAULT_DEMO_DESK_CAPITAL = 5_000.0


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _text(value: str, label: str, *, maximum: int = 128) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


@dataclass(frozen=True, slots=True)
class DemoDeskCapitalState:
    desk_id: str
    generation_id: str
    session_fingerprint: str
    capital: SettledCapitalState

    def __post_init__(self) -> None:
        object.__setattr__(self, "desk_id", _text(self.desk_id, "demo desk_id"))
        object.__setattr__(self, "generation_id", _text(self.generation_id, "demo generation_id"))
        object.__setattr__(self, "session_fingerprint", _sha(self.session_fingerprint, "demo session"))

    @classmethod
    def fresh(
        cls,
        desk_id: str,
        generation_id: str,
        session_fingerprint: str,
        *,
        starting_capital: float = DEFAULT_DEMO_DESK_CAPITAL,
    ) -> "DemoDeskCapitalState":
        amount = _finite(starting_capital, "starting demo capital")
        if amount <= 0:
            raise ValueError("starting demo capital must be positive")
        return cls(
            desk_id,
            generation_id,
            session_fingerprint,
            SettledCapitalState(amount, 0.0, 0.0, 0.0, amount, amount),
        )

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m186-demo-desk-capital-v1",
                self.desk_id,
                self.generation_id,
                self.session_fingerprint,
                self.capital.starting_balance,
                self.capital.deposits,
                self.capital.withdrawals,
                self.capital.settled_realized_pnl,
                self.capital.balance,
                self.capital.equity,
                self.capital.protected_reserve,
            )
        )

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def cross_desk_transfer_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class DemoOpportunityEvidence:
    opportunity_id: str
    strategy_fingerprint: str
    evaluation_fingerprint: str
    campaign_fingerprint: str
    shadow_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "opportunity_id", _text(self.opportunity_id, "demo opportunity_id"))
        for field, label in (
            ("strategy_fingerprint", "demo strategy"),
            ("evaluation_fingerprint", "demo evaluation"),
            ("campaign_fingerprint", "demo campaign"),
            ("shadow_fingerprint", "demo shadow"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m186-demo-opportunity-evidence-v1",
                self.opportunity_id,
                self.strategy_fingerprint,
                self.evaluation_fingerprint,
                self.campaign_fingerprint,
                self.shadow_fingerprint,
            )
        )


@dataclass(frozen=True, slots=True)
class DemoCapitalReservation:
    desk_id: str
    generation_id: str
    session_fingerprint: str
    opportunity_id: str
    strategy_fingerprint: str
    evaluation_fingerprint: str
    campaign_fingerprint: str
    shadow_fingerprint: str
    risk_fraction: float
    risk_cash: float
    minimum_viable_capital: float
    reserved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "desk_id", _text(self.desk_id, "reservation desk_id"))
        object.__setattr__(self, "generation_id", _text(self.generation_id, "reservation generation_id"))
        object.__setattr__(self, "session_fingerprint", _sha(self.session_fingerprint, "reservation session"))
        object.__setattr__(self, "opportunity_id", _text(self.opportunity_id, "reservation opportunity_id"))
        for field, label in (
            ("strategy_fingerprint", "reservation strategy"),
            ("evaluation_fingerprint", "reservation evaluation"),
            ("campaign_fingerprint", "reservation campaign"),
            ("shadow_fingerprint", "reservation shadow"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "risk_fraction", _finite(self.risk_fraction, "reservation risk_fraction"))
        object.__setattr__(self, "risk_cash", _finite(self.risk_cash, "reservation risk_cash"))
        object.__setattr__(
            self,
            "minimum_viable_capital",
            _finite(self.minimum_viable_capital, "reservation minimum_viable_capital"),
        )
        object.__setattr__(self, "reserved_at", _aware(self.reserved_at, "reservation reserved_at"))
        if not 0 < self.risk_fraction <= 1 or self.risk_cash <= 0 or self.minimum_viable_capital <= 0:
            raise ValueError("reservation economics are invalid")

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m186-demo-capital-reservation-v1",
                self.desk_id,
                self.generation_id,
                self.session_fingerprint,
                self.opportunity_id,
                self.strategy_fingerprint,
                self.evaluation_fingerprint,
                self.campaign_fingerprint,
                self.shadow_fingerprint,
                self.risk_fraction,
                self.risk_cash,
                self.minimum_viable_capital,
                self.reserved_at.isoformat(),
            )
        )

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class DemoCapitalAllocationDecision:
    desk_fingerprint: str
    reservations: tuple[DemoCapitalReservation, ...]
    conservative_capital: float
    capital_already_reserved: float
    risk_fraction_already_reserved: float
    available_slots_before_new: int
    reasons: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                "dusty-m186-demo-capital-decision-v1",
                self.desk_fingerprint,
                tuple(row.fingerprint for row in self.reservations),
                self.conservative_capital,
                self.capital_already_reserved,
                self.risk_fraction_already_reserved,
                self.available_slots_before_new,
                self.reasons,
            )
        )

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def cross_desk_transfer_authority(self) -> bool:
        return False


def _slot_capacity(capital: SettledCapitalState, policy: CapitalOpportunityPolicy) -> int:
    growth_slots = int(capital.realized_growth_capital // policy.realized_gain_per_extra_slot)
    return min(policy.maximum_concurrent_opportunities, policy.base_opportunity_slots + growth_slots)


def _validate_existing_reservations(
    desk: DemoDeskCapitalState,
    reservations: tuple[DemoCapitalReservation, ...],
    *,
    policy: CapitalOpportunityPolicy,
) -> tuple[tuple[DemoCapitalReservation, ...], set[str]]:
    fingerprints = tuple(row.fingerprint for row in reservations)
    if len(fingerprints) != len(set(fingerprints)):
        raise ValueError("duplicate active demo reservation evidence")

    shadow_owners: dict[str, tuple[str, str]] = {}
    current: list[DemoCapitalReservation] = []
    seen_current_opportunities: set[str] = set()
    for row in reservations:
        owner = (row.desk_id, row.session_fingerprint)
        prior = shadow_owners.get(row.shadow_fingerprint)
        if prior is not None and prior != owner:
            raise ValueError("same shadow evidence is claimed by multiple demo desk/session owners")
        shadow_owners[row.shadow_fingerprint] = owner

        if row.desk_id != desk.desk_id:
            continue
        if row.generation_id != desk.generation_id:
            raise ValueError("active reservation generation drift for demo desk")
        if row.session_fingerprint != desk.session_fingerprint:
            raise ValueError("active reservation session drift for demo desk")
        if row.opportunity_id in seen_current_opportunities:
            raise ValueError("duplicate active opportunity reservation for demo desk")
        seen_current_opportunities.add(row.opportunity_id)
        current.append(row)

    used_risk = sum(row.risk_fraction for row in current)
    if used_risk > policy.maximum_total_risk_fraction + 1e-12:
        raise ValueError("active demo reservations exceed portfolio risk constitution")
    if len(current) > _slot_capacity(desk.capital, policy):
        raise ValueError("active demo reservations exceed settled-growth slot capacity")
    return tuple(current), set(shadow_owners)


def allocate_demo_capital(
    desk: DemoDeskCapitalState,
    opportunities: Iterable[CertifiedOpportunity],
    evidence: Iterable[DemoOpportunityEvidence],
    *,
    active_firm_reservations: Iterable[DemoCapitalReservation] = (),
    at: datetime,
    policy: CapitalOpportunityPolicy = CapitalOpportunityPolicy(),
) -> DemoCapitalAllocationDecision:
    """Allocate new opportunities within one isolated demo desk.

    Active reservations from all desks are supplied only to enforce provenance
    uniqueness.  Capital and risk arithmetic uses reservations belonging to the
    current desk only; no other desk can contribute or absorb capital/P&L.
    """

    now = _aware(at, "demo capital allocation time")
    rows = tuple(opportunities)
    row_ids = tuple(row.opportunity_id for row in rows)
    if len(row_ids) != len(set(row_ids)):
        raise ValueError("duplicate opportunity identity in demo allocation batch")

    bindings = tuple(evidence)
    evidence_ids = tuple(row.opportunity_id for row in bindings)
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ValueError("duplicate demo opportunity evidence binding")
    binding_map = {row.opportunity_id: row for row in bindings}
    row_map = {row.opportunity_id: row for row in rows}
    if set(binding_map) != set(row_map):
        raise ValueError("demo opportunity evidence must exactly match allocation batch")
    for opportunity_id, row in row_map.items():
        binding = binding_map[opportunity_id]
        if _sha(row.strategy_hash, "capital opportunity strategy") != binding.strategy_fingerprint:
            raise ValueError("capital opportunity/evidence strategy identity drift")

    all_reservations = tuple(active_firm_reservations)
    current, claimed_shadows = _validate_existing_reservations(desk, all_reservations, policy=policy)
    existing_ids = {row.opportunity_id for row in current}
    reserved_capital = sum(row.minimum_viable_capital for row in current)
    used_risk = sum(row.risk_fraction for row in current)
    total_slots = _slot_capacity(desk.capital, policy)
    remaining_slots = total_slots - len(current)
    conservative = desk.capital.conservative_deployable_capital

    reasons: list[str] = []
    candidates: list[CertifiedOpportunity] = []
    for row in rows:
        binding = binding_map[row.opportunity_id]
        if row.opportunity_id in existing_ids:
            reasons.append(f"{row.opportunity_id}:already_reserved_on_desk")
        elif binding.shadow_fingerprint in claimed_shadows:
            reasons.append(f"{row.opportunity_id}:shadow_evidence_already_claimed")
        else:
            candidates.append(row)

    if remaining_slots <= 0:
        reasons.append("demo_desk_slots_exhausted")
        return DemoCapitalAllocationDecision(
            desk.fingerprint,
            (),
            conservative,
            reserved_capital,
            used_risk,
            0,
            tuple(reasons),
        )

    remaining_risk = policy.maximum_total_risk_fraction - used_risk
    if remaining_risk <= 1e-12:
        reasons.append("demo_desk_risk_budget_exhausted")
        return DemoCapitalAllocationDecision(
            desk.fingerprint,
            (),
            conservative,
            reserved_capital,
            used_risk,
            remaining_slots,
            tuple(reasons),
        )

    adjusted_capital = SettledCapitalState(
        desk.capital.starting_balance,
        desk.capital.deposits,
        desk.capital.withdrawals,
        desk.capital.settled_realized_pnl,
        desk.capital.balance,
        desk.capital.equity,
        desk.capital.protected_reserve + reserved_capital,
    )
    adjusted_policy = CapitalOpportunityPolicy(
        maximum_risk_fraction_per_trade=min(policy.maximum_risk_fraction_per_trade, remaining_risk),
        maximum_total_risk_fraction=remaining_risk,
        maximum_concurrent_opportunities=remaining_slots,
        base_opportunity_slots=remaining_slots,
        realized_gain_per_extra_slot=policy.realized_gain_per_extra_slot,
    )
    base: CapitalOpportunityDecision = allocate_certified_opportunities(
        adjusted_capital,
        candidates,
        at=now,
        policy=adjusted_policy,
    )
    reasons.extend(base.reasons)

    reservations: list[DemoCapitalReservation] = []
    for allocation in base.allocations:
        row = row_map[allocation.opportunity_id]
        binding = binding_map[allocation.opportunity_id]
        reservations.append(
            DemoCapitalReservation(
                desk.desk_id,
                desk.generation_id,
                desk.session_fingerprint,
                row.opportunity_id,
                binding.strategy_fingerprint,
                binding.evaluation_fingerprint,
                binding.campaign_fingerprint,
                binding.shadow_fingerprint,
                allocation.risk_fraction,
                allocation.risk_cash,
                row.minimum_viable_capital,
                now,
            )
        )

    return DemoCapitalAllocationDecision(
        desk.fingerprint,
        tuple(reservations),
        conservative,
        reserved_capital,
        used_risk,
        remaining_slots,
        tuple(reasons),
    )
