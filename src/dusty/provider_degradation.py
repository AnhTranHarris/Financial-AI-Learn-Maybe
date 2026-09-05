from __future__ import annotations

"""M191 deterministic provider degradation policy.

The manager consumes explicit provider/reviewer health observations. It does not
start, stop, restart, select, weight, or disable providers itself. It can only
classify whether a contractor is healthy enough to contribute *new* evidence.
Dusty's deterministic core remains operational even when every optional model is
unavailable.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: str, label: str, *, maximum: int = 128) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be non-empty, one line, and <= {maximum} characters")
    return rendered


class ProviderObservationOutcome(StrEnum):
    SUCCESS = "success"
    TRANSIENT_FAILURE = "transient_failure"
    RESOURCE_BLOCKED = "resource_blocked"
    INVALID_RESPONSE = "invalid_response"
    IDENTITY_DRIFT = "identity_drift"


class ProviderOperationalStatus(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class ProviderHealthObservation:
    provider_id: str
    model_identity_fingerprint: str
    observed_at: datetime
    outcome: ProviderObservationOutcome
    evidence_fingerprint: str
    detail: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _text(self.provider_id, "provider id", maximum=64))
        object.__setattr__(self, "model_identity_fingerprint", _sha(self.model_identity_fingerprint, "model identity"))
        object.__setattr__(self, "observed_at", _aware(self.observed_at, "provider observation timestamp"))
        object.__setattr__(self, "evidence_fingerprint", _sha(self.evidence_fingerprint, "provider evidence"))
        object.__setattr__(self, "detail", _text(self.detail, "provider observation detail", maximum=256))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m191-provider-health-observation-v1",
            self.provider_id,
            self.model_identity_fingerprint,
            self.observed_at.isoformat(),
            self.outcome.value,
            self.evidence_fingerprint,
            self.detail,
        ))


@dataclass(frozen=True, slots=True)
class ProviderDegradationPolicy:
    degraded_after_consecutive_failures: int = 2
    unavailable_after_consecutive_failures: int = 4
    recovery_successes_required: int = 2
    observation_window: int = 20

    def __post_init__(self) -> None:
        values = (
            self.degraded_after_consecutive_failures,
            self.unavailable_after_consecutive_failures,
            self.recovery_successes_required,
            self.observation_window,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("M191 policy values must be positive integers")
        if self.unavailable_after_consecutive_failures <= self.degraded_after_consecutive_failures:
            raise ValueError("unavailable threshold must exceed degraded threshold")
        if self.observation_window < self.unavailable_after_consecutive_failures:
            raise ValueError("observation window too short for unavailable threshold")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m191-provider-degradation-policy-v1",
            self.degraded_after_consecutive_failures,
            self.unavailable_after_consecutive_failures,
            self.recovery_successes_required,
            self.observation_window,
        ))


@dataclass(frozen=True, slots=True)
class ProviderDegradationAssessment:
    provider_id: str
    model_identity_fingerprint: str
    status: ProviderOperationalStatus
    consecutive_failures: int
    consecutive_successes: int
    observations_considered: int
    reason: str
    observation_fingerprints: tuple[str, ...]
    policy_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider_id", _text(self.provider_id, "assessment provider id", maximum=64))
        object.__setattr__(self, "model_identity_fingerprint", _sha(self.model_identity_fingerprint, "assessment model identity"))
        object.__setattr__(self, "policy_fingerprint", _sha(self.policy_fingerprint, "M191 policy"))
        object.__setattr__(self, "reason", _text(self.reason, "assessment reason", maximum=256))
        rows = tuple(_sha(row, "M191 observation") for row in self.observation_fingerprints)
        if len(rows) != len(set(rows)):
            raise ValueError("assessment observation fingerprints must be unique")
        object.__setattr__(self, "observation_fingerprints", rows)
        for field in ("consecutive_failures", "consecutive_successes", "observations_considered"):
            value = getattr(self, field)
            if type(value) is not int or value < 0:
                raise ValueError(f"{field} must be a nonnegative integer")

    @property
    def new_evidence_allowed(self) -> bool:
        return self.status is ProviderOperationalStatus.HEALTHY

    @property
    def deterministic_core_operational(self) -> bool:
        return True

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def provider_restart_authority(self) -> bool:
        return False

    @property
    def provider_selection_authority(self) -> bool:
        return False

    @property
    def evidence_weight_override_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m191-provider-degradation-assessment-v1",
            self.provider_id,
            self.model_identity_fingerprint,
            self.status.value,
            self.consecutive_failures,
            self.consecutive_successes,
            self.observations_considered,
            self.reason,
            self.observation_fingerprints,
            self.policy_fingerprint,
        ))


def _trailing_counts(rows: tuple[ProviderHealthObservation, ...]) -> tuple[int, int]:
    failures = 0
    for row in reversed(rows):
        if row.outcome is ProviderObservationOutcome.SUCCESS:
            break
        failures += 1
    successes = 0
    for row in reversed(rows):
        if row.outcome is not ProviderObservationOutcome.SUCCESS:
            break
        successes += 1
    return failures, successes


def assess_provider_degradation(
    observations: tuple[ProviderHealthObservation, ...],
    *,
    provider_id: str,
    model_identity_fingerprint: str,
    policy: ProviderDegradationPolicy = ProviderDegradationPolicy(),
) -> ProviderDegradationAssessment:
    provider = _text(provider_id, "provider id", maximum=64)
    identity = _sha(model_identity_fingerprint, "expected model identity")
    all_rows = tuple(sorted(observations, key=lambda row: (row.observed_at, row.fingerprint)))
    if len({row.fingerprint for row in all_rows}) != len(all_rows):
        raise ValueError("M191 cannot assess duplicate health observations")
    if any(row.provider_id != provider for row in all_rows):
        raise ValueError("M191 cannot mix providers")
    if any(row.model_identity_fingerprint != identity for row in all_rows):
        # A caller must record identity drift as an explicit outcome for the
        # expected identity, rather than silently relabel observations.
        raise ValueError("M191 observation model identity does not match expected identity")
    if any(left.observed_at == right.observed_at for left, right in zip(all_rows, all_rows[1:])):
        raise ValueError("M191 observations require unique timestamps")
    if not all_rows:
        return ProviderDegradationAssessment(
            provider, identity, ProviderOperationalStatus.UNAVAILABLE,
            0, 0, 0, "no_provider_health_evidence", (), policy.fingerprint,
        )

    # Identity drift is sticky for this model-identity lineage. It cannot age
    # out of a rolling health window. Recovery requires an externally verified
    # new identity lineage, not merely enough later successes.
    if any(row.outcome is ProviderObservationOutcome.IDENTITY_DRIFT for row in all_rows):
        return ProviderDegradationAssessment(
            provider, identity, ProviderOperationalStatus.QUARANTINED,
            0, 0, len(all_rows), "provider_identity_drift_requires_external_revalidation",
            tuple(row.fingerprint for row in all_rows), policy.fingerprint,
        )

    rows = all_rows[-policy.observation_window:]
    failures, successes = _trailing_counts(rows)
    if failures >= policy.unavailable_after_consecutive_failures:
        status = ProviderOperationalStatus.UNAVAILABLE
        reason = "consecutive_provider_failures_exceeded_unavailable_threshold"
    elif failures >= policy.degraded_after_consecutive_failures:
        status = ProviderOperationalStatus.DEGRADED
        reason = "consecutive_provider_failures_exceeded_degraded_threshold"
    elif failures:
        status = ProviderOperationalStatus.DEGRADED
        reason = "recent_provider_failure_requires_fresh_success_evidence"
    elif successes < policy.recovery_successes_required:
        status = ProviderOperationalStatus.DEGRADED
        reason = "provider_recovery_success_evidence_incomplete"
    else:
        status = ProviderOperationalStatus.HEALTHY
        reason = "provider_health_confirmed_by_consecutive_successes"

    return ProviderDegradationAssessment(
        provider,
        identity,
        status,
        failures,
        successes,
        len(rows),
        reason,
        tuple(row.fingerprint for row in rows),
        policy.fingerprint,
    )


@dataclass(frozen=True, slots=True)
class ProviderFleetAssessment:
    providers: tuple[ProviderDegradationAssessment, ...]

    def __post_init__(self) -> None:
        rows = tuple(sorted(self.providers, key=lambda row: row.provider_id))
        if len({row.provider_id for row in rows}) != len(rows):
            raise ValueError("provider fleet cannot contain duplicate provider ids")
        object.__setattr__(self, "providers", rows)

    @property
    def usable_provider_ids(self) -> tuple[str, ...]:
        return tuple(row.provider_id for row in self.providers if row.new_evidence_allowed)

    @property
    def deterministic_core_operational(self) -> bool:
        return True

    @property
    def all_optional_providers_unavailable(self) -> bool:
        return not self.usable_provider_ids

    @property
    def fingerprint(self) -> str:
        return _digest(("dusty-m191-provider-fleet-v1", tuple(row.fingerprint for row in self.providers)))
