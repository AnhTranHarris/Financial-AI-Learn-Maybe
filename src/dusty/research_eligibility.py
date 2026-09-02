"""Fixed, entry-only research vetoes. These are hypotheses, not trained forecasts."""
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math

from .experience import TradeSide
from .features import FeatureVector


class EntryPolicy(str, Enum):
    SEED = "seed-v1"
    TREND = "completed-close-ema-sma-alignment-v1"
    NO_TRADE = "no-trade-control-v1"


@dataclass(frozen=True, slots=True)
class EntryEligibility:
    at: datetime
    allowed: bool
    reason: str


def entry_eligibility(vector: FeatureVector, side: TradeSide, policy: EntryPolicy) -> EntryEligibility:
    """Use only this completed-bar vector; no performance, future bars or exit inputs.

    Permission means only 'this veto is clear'. Strategy, cognition and risk still
    decide whether there is an entry. Missing/invalid trend inputs fail closed.
    """
    if not isinstance(policy, EntryPolicy) or not isinstance(side, TradeSide):
        raise ValueError("research_entry_policy_and_side_require_known_enums")
    if policy is EntryPolicy.SEED:
        return EntryEligibility(vector.at, True, "seed_has_no_additional_veto")
    if policy is EntryPolicy.NO_TRADE:
        return EntryEligibility(vector.at, False, "no_trade_control")
    values = vector.feature_map()
    inputs = tuple(values.get(key) for key in ("close", "sma_20", "ema_20"))
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0 for value in inputs):
        return EntryEligibility(vector.at, False, "trend_inputs_missing_or_invalid")
    close, sma, ema = inputs
    aligned = close > sma and ema > sma if side is TradeSide.LONG else close < sma and ema < sma
    return EntryEligibility(vector.at, aligned, "trend_aligned" if aligned else "trend_not_aligned")
