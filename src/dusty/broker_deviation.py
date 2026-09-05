from __future__ import annotations

"""M187 provenance-bound broker execution deviation classification.

M187 consumes the frozen M185 shadow, an existing DemoExecutionResult, and
broker-history deal evidence. It classifies execution quality only. It never
sends/retries/cancels orders, mutates positions, promotes strategies, changes
risk, or overrides Guardian.
"""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
import json
import math

from .demo_execution import DemoExecutionResult
from .execution_lifecycle import ExecutionState
from .shadow_trade import ShadowFillComparison, ShadowTradeRecord


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _finite(value: float, label: str) -> float:
    rendered = float(value)
    if not math.isfinite(rendered):
        raise ValueError(f"{label} must be finite")
    return rendered


def _unit(value: float, label: str) -> float:
    rendered = _finite(value, label)
    if not 0.0 <= rendered <= 1.0:
        raise ValueError(f"{label} must be in [0,1]")
    return rendered


class BrokerDeviationStatus(StrEnum):
    INCOMPLETE = "incomplete"
    INCONSISTENT = "inconsistent"
    BROKER_FAILURE = "broker_failure"
    DEVIATED = "deviated"
    WITHIN_POLICY = "within_policy"


@dataclass(frozen=True, slots=True)
class BrokerDeviationPolicy:
    minimum_fill_fraction: float
    maximum_adverse_slippage_fraction: float
    maximum_first_fill_latency_ms: float
    maximum_last_fill_latency_ms: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "minimum_fill_fraction", _unit(self.minimum_fill_fraction, "minimum_fill_fraction"))
        slippage = _finite(self.maximum_adverse_slippage_fraction, "maximum_adverse_slippage_fraction")
        first = _finite(self.maximum_first_fill_latency_ms, "maximum_first_fill_latency_ms")
        last = _finite(self.maximum_last_fill_latency_ms, "maximum_last_fill_latency_ms")
        if slippage < 0 or first < 0 or last < 0:
            raise ValueError("broker deviation thresholds must be nonnegative")
        if last < first:
            raise ValueError("last-fill latency ceiling cannot be below first-fill ceiling")
        object.__setattr__(self, "maximum_adverse_slippage_fraction", slippage)
        object.__setattr__(self, "maximum_first_fill_latency_ms", first)
        object.__setattr__(self, "maximum_last_fill_latency_ms", last)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m187-broker-deviation-policy-v1",
            self.minimum_fill_fraction,
            self.maximum_adverse_slippage_fraction,
            self.maximum_first_fill_latency_ms,
            self.maximum_last_fill_latency_ms,
        ))


@dataclass(frozen=True, slots=True)
class BrokerDeviationAssessment:
    status: BrokerDeviationStatus
    intent_hash: str
    shadow_fingerprint: str
    comparison_fingerprint: str
    execution_state: ExecutionState
    retcode: int
    order_ticket: int
    deal_ticket: int
    fill_fraction: float
    adverse_slippage_fraction: float | None
    first_fill_latency_ms: float | None
    last_fill_latency_ms: float | None
    reasons: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    policy_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_hash", _sha(self.intent_hash, "assessment intent"))
        object.__setattr__(self, "shadow_fingerprint", _sha(self.shadow_fingerprint, "assessment shadow"))
        object.__setattr__(self, "comparison_fingerprint", _sha(self.comparison_fingerprint, "assessment comparison"))
        object.__setattr__(self, "fill_fraction", _unit(self.fill_fraction, "assessment fill_fraction"))
        if self.adverse_slippage_fraction is not None:
            object.__setattr__(self, "adverse_slippage_fraction", _finite(self.adverse_slippage_fraction, "assessment slippage"))
        for name in ("first_fill_latency_ms", "last_fill_latency_ms"):
            value = getattr(self, name)
            if value is not None:
                rendered = _finite(value, name)
                if rendered < 0:
                    raise ValueError("assessment latency must be nonnegative")
                object.__setattr__(self, name, rendered)
        object.__setattr__(self, "evidence_fingerprints", tuple(sorted({_sha(value, "assessment evidence") for value in self.evidence_fingerprints})))
        object.__setattr__(self, "policy_fingerprint", _sha(self.policy_fingerprint, "assessment policy"))

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m187-broker-deviation-assessment-v1",
            self.status.value,
            self.intent_hash,
            self.shadow_fingerprint,
            self.comparison_fingerprint,
            self.execution_state.value,
            self.retcode,
            self.order_ticket,
            self.deal_ticket,
            self.fill_fraction,
            self.adverse_slippage_fraction,
            self.first_fill_latency_ms,
            self.last_fill_latency_ms,
            self.reasons,
            self.evidence_fingerprints,
            self.policy_fingerprint,
        ))

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def retry_authority(self) -> bool:
        return False

    @property
    def position_mutation_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


_SUCCESS_STATES = {ExecutionState.ACCEPTED, ExecutionState.PARTIAL, ExecutionState.FILLED}
_FAILURE_STATES = {ExecutionState.REJECTED, ExecutionState.FAULT}
_MT5_PLACED = 10008
_MT5_DONE = 10009
_MT5_DONE_PARTIAL = 10010
_MT5_SUCCESS_RETCODES = {_MT5_PLACED, _MT5_DONE, _MT5_DONE_PARTIAL}


def _execution_fingerprint(result: DemoExecutionResult) -> str:
    return _digest((
        "dusty-m187-demo-execution-result-v1",
        result.intent_hash,
        result.state.value,
        int(result.retcode),
        int(result.order_ticket),
        int(result.deal_ticket),
        str(result.comment),
    ))


def _retcode_state_issue(execution: DemoExecutionResult) -> str | None:
    retcode = int(execution.retcode)
    if execution.state is ExecutionState.ACCEPTED and retcode not in {_MT5_PLACED, _MT5_DONE}:
        return "accepted_state_conflicts_with_mt5_retcode"
    if execution.state is ExecutionState.PARTIAL and retcode != _MT5_DONE_PARTIAL:
        return "partial_state_conflicts_with_mt5_retcode"
    if execution.state is ExecutionState.FILLED and retcode != _MT5_DONE:
        return "filled_state_conflicts_with_mt5_retcode"
    if execution.state in _FAILURE_STATES and retcode in _MT5_SUCCESS_RETCODES:
        return "failure_state_conflicts_with_mt5_success_retcode"
    return None


def classify_broker_deviation(
    shadow: ShadowTradeRecord,
    comparison: ShadowFillComparison,
    execution: DemoExecutionResult,
    *,
    policy: BrokerDeviationPolicy,
) -> BrokerDeviationAssessment:
    """Classify one frozen shadow-versus-broker execution observation."""

    intent_hash = _sha(shadow.intent_hash, "shadow intent")
    if _sha(comparison.intent_hash, "comparison intent") != intent_hash:
        raise ValueError("shadow/comparison intent identity drift")
    if comparison.shadow_fingerprint != shadow.fingerprint:
        raise ValueError("comparison does not belong to frozen shadow evidence")
    if _sha(execution.intent_hash, "execution intent") != intent_hash:
        raise ValueError("shadow/execution intent identity drift")
    if comparison.client_tag != shadow.client_tag:
        raise ValueError("shadow/comparison client tag identity drift")

    order_ticket = int(execution.order_ticket)
    deal_ticket = int(execution.deal_ticket)
    if order_ticket < 0 or deal_ticket < 0:
        raise ValueError("execution tickets cannot be negative")
    fill_order_tickets = {row.order_ticket for row in comparison.fills}
    fill_deal_tickets = {row.deal_ticket for row in comparison.fills}

    inconsistencies: list[str] = []
    retcode_issue = _retcode_state_issue(execution)
    if retcode_issue is not None:
        inconsistencies.append(retcode_issue)
    if order_ticket and fill_order_tickets and fill_order_tickets != {order_ticket}:
        inconsistencies.append("broker_history_order_ticket_mismatch")
    if deal_ticket and comparison.fills and deal_ticket not in fill_deal_tickets:
        inconsistencies.append("returned_deal_ticket_missing_from_history")
    if execution.state in _FAILURE_STATES and comparison.fills:
        inconsistencies.append("failure_state_conflicts_with_observed_fill")
    if execution.state is ExecutionState.PARTIAL and math.isclose(comparison.fill_fraction, 1.0, rel_tol=0.0, abs_tol=1e-12):
        inconsistencies.append("partial_state_conflicts_with_full_fill_history")
    if execution.state is ExecutionState.FILLED and comparison.fills and comparison.fill_fraction < 1.0 - 1e-12:
        inconsistencies.append("filled_state_conflicts_with_partial_history")

    evidence = {
        shadow.fingerprint,
        comparison.fingerprint,
        _execution_fingerprint(execution),
        *comparison.fill_fingerprints,
        *comparison.fill_source_fingerprints,
    }
    if inconsistencies:
        return BrokerDeviationAssessment(
            BrokerDeviationStatus.INCONSISTENT,
            intent_hash,
            shadow.fingerprint,
            comparison.fingerprint,
            execution.state,
            int(execution.retcode),
            order_ticket,
            deal_ticket,
            comparison.fill_fraction,
            comparison.adverse_slippage_fraction,
            comparison.first_fill_latency_ms,
            comparison.last_fill_latency_ms,
            tuple(sorted(inconsistencies)),
            tuple(evidence),
            policy.fingerprint,
        )

    if not comparison.fills:
        if execution.state in _FAILURE_STATES:
            status = BrokerDeviationStatus.BROKER_FAILURE
            reasons = (f"execution_{execution.state.value}", f"retcode:{int(execution.retcode)}")
        else:
            status = BrokerDeviationStatus.INCOMPLETE
            reasons = ("broker_history_fill_evidence_missing", f"execution_state:{execution.state.value}")
        return BrokerDeviationAssessment(
            status,
            intent_hash,
            shadow.fingerprint,
            comparison.fingerprint,
            execution.state,
            int(execution.retcode),
            order_ticket,
            deal_ticket,
            comparison.fill_fraction,
            None,
            None,
            None,
            reasons,
            tuple(evidence),
            policy.fingerprint,
        )

    if execution.state not in _SUCCESS_STATES:
        return BrokerDeviationAssessment(
            BrokerDeviationStatus.INCONSISTENT,
            intent_hash,
            shadow.fingerprint,
            comparison.fingerprint,
            execution.state,
            int(execution.retcode),
            order_ticket,
            deal_ticket,
            comparison.fill_fraction,
            comparison.adverse_slippage_fraction,
            comparison.first_fill_latency_ms,
            comparison.last_fill_latency_ms,
            (f"observed_fill_with_unexpected_execution_state:{execution.state.value}",),
            tuple(evidence),
            policy.fingerprint,
        )

    if comparison.adverse_slippage_fraction is None or comparison.first_fill_latency_ms is None or comparison.last_fill_latency_ms is None:
        raise ValueError("filled comparison is missing execution-quality metrics")

    deviations: list[str] = []
    if comparison.fill_fraction + 1e-12 < policy.minimum_fill_fraction:
        deviations.append("fill_fraction_below_policy")
    if comparison.adverse_slippage_fraction > policy.maximum_adverse_slippage_fraction + 1e-15:
        deviations.append("adverse_slippage_above_policy")
    if comparison.first_fill_latency_ms > policy.maximum_first_fill_latency_ms + 1e-9:
        deviations.append("first_fill_latency_above_policy")
    if comparison.last_fill_latency_ms > policy.maximum_last_fill_latency_ms + 1e-9:
        deviations.append("last_fill_latency_above_policy")

    status = BrokerDeviationStatus.DEVIATED if deviations else BrokerDeviationStatus.WITHIN_POLICY
    return BrokerDeviationAssessment(
        status,
        intent_hash,
        shadow.fingerprint,
        comparison.fingerprint,
        execution.state,
        int(execution.retcode),
        order_ticket,
        deal_ticket,
        comparison.fill_fraction,
        comparison.adverse_slippage_fraction,
        comparison.first_fill_latency_ms,
        comparison.last_fill_latency_ms,
        tuple(deviations) if deviations else ("observed_execution_within_explicit_policy",),
        tuple(evidence),
        policy.fingerprint,
    )
