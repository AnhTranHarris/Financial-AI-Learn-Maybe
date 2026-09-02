"""Explanations of recorded sizing decisions, never funding or execution authority."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, localcontext
import math
from typing import Any


@dataclass(frozen=True, slots=True)
class ResearchCapitalSummary:
    symbol: str
    currency: str
    minimum_lot: float
    starting_balance: float
    configured_risk_fraction: float
    candidates: int
    sized_candidates: int
    approved_candidates: int
    minimum_lot_rejections: int
    minimum_loss_low: float | None
    minimum_loss_high: float | None
    required_balance_low: float | None
    required_balance_high: float | None
    rejection_counts: tuple[tuple[str, int], ...]

    @property
    def preferred_balance(self) -> float | None:
        # Do not invent a balance for setups rejected before sizing took place.
        if not self.candidates or self.sized_candidates != self.candidates:
            return None
        return self.required_balance_high

    def display(self) -> str:
        estimate = self.preferred_balance
        preferred = (f"{_rounded_up(estimate)} {self.currency} (highest sampled sizing threshold)"
                     if estimate is not None else "unavailable — no complete set of sized setups")
        body = (
            f"Preferred balance (risk sizing only): {preferred}\n"
            f"Last research: {self.symbol}; starting balance {self.starting_balance:,.2f} {self.currency}; "
            f"base risk {self.configured_risk_fraction:.2%} "
            f"(initial budget {self.starting_balance * self.configured_risk_fraction:,.2f} {self.currency}). "
            f"Growth approved {self.approved_candidates}/{self.candidates}; "
            f"minimum-lot risk rejections {self.minimum_lot_rejections}."
        )
        if self.minimum_loss_low is not None:
            body += (f"\nPlanned minimum-lot loss range: {self.minimum_loss_low:,.2f}–"
                     f"{self.minimum_loss_high:,.2f} {self.currency}. "
                     f"Sized-setup balance range: {_rounded_up(self.required_balance_low)}–"
                     f"{_rounded_up(self.required_balance_high)} {self.currency}.")
        return body + ("\nExcludes margin and unmodeled costs/gap losses. "
                       "Estimate only; not a deposit recommendation, safety guarantee or trading approval.")


def _rounded_up(value: float) -> str:
    # Presentation only: never round the underlying risk calculation or fingerprint.
    with localcontext() as context:
        context.prec = max(28, len(str(int(value))) + 3)
        return f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_CEILING):,.2f}"


def capital_summary_from_report(report: dict[str, Any], *, currency: str, symbol: str) -> ResearchCapitalSummary:
    """Reuse actual sizing losses (point/tick conversion and friction already applied).

    For each sized setup: minimum lot * loss per lot / effective requested risk.
    The displayed preferred balance is the highest threshold in this *past sample*,
    only if every candidate was sized. It is not a rerun at a larger balance, and
    excludes margin, other positions, future gaps and unmodeled broker costs.
    """
    def positive(value: Any) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ValueError("invalid_research_capital_input")
        return float(value)

    minimum = positive(report["economics"]["volume_min"])
    config = report["config"]
    balance = positive(config["growth_starting_equity"])
    risk = positive(config["growth_risk_fraction"])
    if risk > 1:
        raise ValueError("invalid_research_capital_risk")
    traces = report["laboratory"]["growth_sizing"]
    losses, thresholds = [], []
    rejections: Counter[str] = Counter()
    approved = 0
    for trace in traces:
        if type(trace["approved"]) is not bool:
            raise ValueError("invalid_research_capital_approval")
        approved += int(trace["approved"])
        if not trace["approved"]:
            rejections.update(set(reason.split(":", 1)[0] for reason in trace["reasons"]
                                  if not reason.startswith("minimum_loss:")))
        sizing = trace["sizing"]
        if sizing is None:
            continue
        effective_risk = positive(sizing["allowed_loss"]) / positive(trace["equity_before"])
        if not 0 < effective_risk <= 1:
            raise ValueError("invalid_research_capital_effective_risk")
        minimum_loss = positive(positive(sizing["loss_per_lot"]) * minimum)
        losses.append(minimum_loss)
        thresholds.append(positive(minimum_loss / effective_risk))
    return ResearchCapitalSummary(
        symbol, currency, minimum, balance, risk, len(traces), len(losses), approved,
        rejections["broker_minimum_volume_exceeds_risk_budget"],
        min(losses) if losses else None, max(losses) if losses else None,
        min(thresholds) if thresholds else None, max(thresholds) if thresholds else None,
        tuple(sorted(rejections.items())),
    )
