from __future__ import annotations

"""M202 deterministic production observability.

M202 converts already-existing runtime evidence into one compact health snapshot.
It does not poll MT5, restart processes, place orders, mutate strategies, or
silently repair faults. Observability reports truth; earlier runtime/governance
components remain responsible for action.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import IntEnum, StrEnum
from hashlib import sha256
import json

from .market_clock import MarketClockState
from .provider_degradation import ProviderOperationalStatus
from .security_boundary import SecurityBoundaryCertification, SecurityBoundaryStatus


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


class AlertSeverity(IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2


class FirmHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    HALTED = "halted"


@dataclass(frozen=True, slots=True)
class OperationalObservation:
    observed_at: datetime
    last_heartbeat_at: datetime
    terminal_connected: bool
    terminal_identity_ok: bool
    account_identity_ok: bool
    market_state: MarketClockState
    provider_statuses: tuple[ProviderOperationalStatus, ...]
    open_positions: int
    active_orders: int
    unresolved_reconciliation_count: int
    recovery_halt_active: bool
    champion_suspended: bool
    portfolio_risk_halt: bool
    state_integrity_ok: bool
    artifact_integrity_ok: bool
    security: SecurityBoundaryCertification

    def __post_init__(self) -> None:
        observed = _utc(self.observed_at, "observed_at")
        heartbeat = _utc(self.last_heartbeat_at, "last_heartbeat_at")
        if heartbeat > observed:
            raise ValueError("heartbeat cannot be in the future")
        for name in ("open_positions", "active_orders", "unresolved_reconciliation_count"):
            value = getattr(self, name)
            if isinstance(value, bool) or int(value) != value or int(value) < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if not self.provider_statuses:
            raise ValueError("provider health evidence is required; deterministic-only mode must be explicit")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m202-operational-observation-v1",
            _utc(self.observed_at, "observed_at").isoformat(),
            _utc(self.last_heartbeat_at, "last_heartbeat_at").isoformat(),
            self.terminal_connected,
            self.terminal_identity_ok,
            self.account_identity_ok,
            self.market_state.value,
            tuple(row.value for row in self.provider_statuses),
            self.open_positions,
            self.active_orders,
            self.unresolved_reconciliation_count,
            self.recovery_halt_active,
            self.champion_suspended,
            self.portfolio_risk_halt,
            self.state_integrity_ok,
            self.artifact_integrity_ok,
            self.security.fingerprint,
        ))


@dataclass(frozen=True, slots=True)
class ProductionAlert:
    severity: AlertSeverity
    code: str
    detail: str

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m202-alert-v1", int(self.severity), self.code, self.detail))


@dataclass(frozen=True, slots=True)
class ProductionHealthSnapshot:
    observed_at: datetime
    health: FirmHealth
    alerts: tuple[ProductionAlert, ...]
    observation_fingerprint: str

    broker_write_authority = False
    live_write_authority = False
    restart_authority = False
    credential_access_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    @property
    def highest_severity(self) -> AlertSeverity:
        return max((row.severity for row in self.alerts), default=AlertSeverity.INFO)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m202-health-snapshot-v1",
            _utc(self.observed_at, "observed_at").isoformat(),
            self.health.value,
            tuple(row.fingerprint for row in self.alerts),
            self.observation_fingerprint,
        ))


def assess_production_health(
    observation: OperationalObservation,
    *,
    heartbeat_warning_after: timedelta = timedelta(minutes=5),
    heartbeat_critical_after: timedelta = timedelta(minutes=15),
) -> ProductionHealthSnapshot:
    if heartbeat_warning_after <= timedelta(0) or heartbeat_critical_after <= heartbeat_warning_after:
        raise ValueError("heartbeat thresholds are invalid")

    alerts: list[ProductionAlert] = []

    def add(severity: AlertSeverity, code: str, detail: str) -> None:
        alerts.append(ProductionAlert(severity, code, detail))

    age = _utc(observation.observed_at, "observed_at") - _utc(observation.last_heartbeat_at, "last_heartbeat_at")
    if age >= heartbeat_critical_after:
        add(AlertSeverity.CRITICAL, "heartbeat_stale", "runtime heartbeat exceeded critical age")
    elif age >= heartbeat_warning_after:
        add(AlertSeverity.WARNING, "heartbeat_delayed", "runtime heartbeat exceeded warning age")

    if not observation.terminal_connected:
        add(AlertSeverity.CRITICAL, "terminal_disconnected", "bound MT5 terminal is disconnected")
    if not observation.terminal_identity_ok:
        add(AlertSeverity.CRITICAL, "terminal_identity_drift", "terminal identity does not match certified runtime")
    if not observation.account_identity_ok:
        add(AlertSeverity.CRITICAL, "account_identity_drift", "account identity does not match certified runtime")

    if observation.market_state in {
        MarketClockState.HALTED,
        MarketClockState.UNEXPECTED_STALE_MARKET,
    }:
        add(AlertSeverity.CRITICAL, "market_abnormal", observation.market_state.value)
    elif observation.market_state in {
        MarketClockState.BROKER_MAINTENANCE,
        MarketClockState.TRADE_RESTRICTED,
        MarketClockState.UNKNOWN,
    }:
        add(AlertSeverity.WARNING, "market_restricted", observation.market_state.value)

    unavailable = sum(
        row in {ProviderOperationalStatus.UNAVAILABLE, ProviderOperationalStatus.QUARANTINED}
        for row in observation.provider_statuses
    )
    degraded = sum(row is ProviderOperationalStatus.DEGRADED for row in observation.provider_statuses)
    if unavailable == len(observation.provider_statuses):
        add(AlertSeverity.WARNING, "optional_providers_unavailable", "deterministic core remains available")
    elif unavailable or degraded:
        add(AlertSeverity.WARNING, "provider_fleet_degraded", "one or more optional providers are degraded")

    if observation.unresolved_reconciliation_count:
        add(AlertSeverity.CRITICAL, "unresolved_reconciliation", str(observation.unresolved_reconciliation_count))
    if observation.recovery_halt_active:
        add(AlertSeverity.CRITICAL, "recovery_halt", "restart/recovery safety halt is active")
    if observation.portfolio_risk_halt:
        add(AlertSeverity.CRITICAL, "portfolio_risk_halt", "portfolio risk governor is halted")
    if observation.champion_suspended:
        add(AlertSeverity.WARNING, "champion_suspended", "active Champion is suspended")
    if not observation.state_integrity_ok:
        add(AlertSeverity.CRITICAL, "state_integrity_failed", "durable state integrity check failed")
    if not observation.artifact_integrity_ok:
        add(AlertSeverity.CRITICAL, "artifact_integrity_failed", "artifact integrity check failed")

    if observation.security.status is SecurityBoundaryStatus.REJECTED:
        add(AlertSeverity.CRITICAL, "security_boundary_rejected", "M201 security boundary rejected")
    elif observation.security.status is SecurityBoundaryStatus.PENDING:
        add(AlertSeverity.WARNING, "security_boundary_pending", "M201 security evidence incomplete")

    # Stable deterministic ordering avoids alert order depending on caller/source ordering.
    alerts = sorted(alerts, key=lambda row: (-int(row.severity), row.code, row.detail))
    highest = max((row.severity for row in alerts), default=AlertSeverity.INFO)
    if highest is AlertSeverity.CRITICAL:
        health = FirmHealth.HALTED
    elif highest is AlertSeverity.WARNING:
        health = FirmHealth.DEGRADED
    else:
        health = FirmHealth.HEALTHY

    return ProductionHealthSnapshot(
        observed_at=_utc(observation.observed_at, "observed_at"),
        health=health,
        alerts=tuple(alerts),
        observation_fingerprint=observation.fingerprint,
    )
