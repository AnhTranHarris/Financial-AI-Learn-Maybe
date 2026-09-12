from __future__ import annotations

"""Provisional M166-M174 research dependency planning.

This module accelerates research while M165 production broker calibration is still
incomplete.  It never weakens production admission.  Instead it marks which stages
may run provisionally, which stages are calibration-sensitive, and which artifacts
must be selectively recomputed when the final M165 calibration fingerprint changes.

The module owns no broker, live, promotion, retry, custody, or risk authority.
"""

from dataclasses import dataclass
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


STAGES: tuple[str, ...] = (
    "m166_walk_forward",
    "m167_purged_validation",
    "m168_parameter_stability",
    "m169_regime_torture",
    "m170_cost_torture",
    "m171_forward_decay",
    "m172_tail_risk",
    "m173_strategy_dependency",
    "m174_robustness",
)

# Direct dependency graph. M170 is the only research stage with direct M165
# calibration dependence. M174 consumes the complete admitted artifact set.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "m166_walk_forward": ("strategy", "dataset", "parameters"),
    "m167_purged_validation": ("m166_walk_forward", "dataset"),
    "m168_parameter_stability": ("m166_walk_forward", "parameters"),
    "m169_regime_torture": ("m166_walk_forward", "strategy", "dataset"),
    "m170_cost_torture": ("m165_calibration", "m166_walk_forward"),
    "m171_forward_decay": ("m166_walk_forward", "strategy", "dataset"),
    "m172_tail_risk": ("m166_walk_forward", "strategy", "dataset"),
    "m173_strategy_dependency": ("strategy", "dataset"),
    "m174_robustness": (
        "m165_calibration",
        "m166_walk_forward",
        "m167_purged_validation",
        "m168_parameter_stability",
        "m169_regime_torture",
        "m170_cost_torture",
        "m171_forward_decay",
        "m172_tail_risk",
        "m173_strategy_dependency",
    ),
}

# Production M166 admission and M174 certification remain blocked until genuine
# M165 calibration. Research computations may be performed ahead of that gate.
PROVISIONAL_RUNNABLE: tuple[str, ...] = STAGES[:-1]
PRODUCTION_BLOCKED_UNTIL_M165: tuple[str, ...] = ("m166_production_admission", "m174_robustness")


@dataclass(frozen=True, slots=True)
class ProvisionalResearchPlan:
    lane_id: str
    strategy_fingerprint: str
    dataset_fingerprint: str
    parameter_fingerprint: str
    current_calibration_fingerprint: str
    current_observation_count: int
    current_distinct_days: int

    broker_write_authority = False
    live_write_authority = False
    custody_write_authority = False
    promotion_authority = False
    retry_authority = False
    risk_override_authority = False

    def __post_init__(self) -> None:
        lane = str(self.lane_id).strip().lower()
        if not lane:
            raise ValueError("lane_id required")
        object.__setattr__(self, "lane_id", lane)
        for name in (
            "strategy_fingerprint",
            "dataset_fingerprint",
            "parameter_fingerprint",
            "current_calibration_fingerprint",
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        if isinstance(self.current_observation_count, bool) or int(self.current_observation_count) < 0:
            raise ValueError("current_observation_count must be nonnegative")
        if isinstance(self.current_distinct_days, bool) or int(self.current_distinct_days) < 0:
            raise ValueError("current_distinct_days must be nonnegative")
        object.__setattr__(self, "current_observation_count", int(self.current_observation_count))
        object.__setattr__(self, "current_distinct_days", int(self.current_distinct_days))

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m166-m174-provisional-research-v1",
            "lane_id": self.lane_id,
            "strategy_fingerprint": self.strategy_fingerprint,
            "dataset_fingerprint": self.dataset_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "current_calibration_fingerprint": self.current_calibration_fingerprint,
            "current_observation_count": self.current_observation_count,
            "current_distinct_days": self.current_distinct_days,
            "provisional_runnable": list(PROVISIONAL_RUNNABLE),
            "production_blocked_until_m165": list(PRODUCTION_BLOCKED_UNTIL_M165),
            "dependencies": {key: list(value) for key, value in DEPENDENCIES.items()},
            "authority": {
                "broker_write": False,
                "live_write": False,
                "custody_write": False,
                "promotion": False,
                "retry": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def descendants_of(changed_inputs: tuple[str, ...]) -> tuple[str, ...]:
    """Return deterministic transitive stage invalidation for changed identities."""
    changed = set(changed_inputs)
    invalidated: set[str] = set()
    while True:
        before = len(invalidated)
        for stage in STAGES:
            deps = set(DEPENDENCIES[stage])
            if deps & (changed | invalidated):
                invalidated.add(stage)
        if len(invalidated) == before:
            break
    return tuple(stage for stage in STAGES if stage in invalidated)


def revalidation_after_final_calibration(
    *,
    provisional_calibration_fingerprint: str,
    final_calibration_fingerprint: str,
) -> tuple[str, ...]:
    provisional = _sha(provisional_calibration_fingerprint, "provisional calibration")
    final = _sha(final_calibration_fingerprint, "final calibration")
    if provisional == final:
        return ()
    return descendants_of(("m165_calibration",))
