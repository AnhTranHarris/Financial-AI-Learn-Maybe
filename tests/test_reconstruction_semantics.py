from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from dusty.experience import TradeSide
from dusty.reconstruction_semantics import (
    assess_reconstruction_semantics,
    dead_hypothesis_clause_coordinates,
)
from dusty.research import Clause, RuleOp
from dusty.runtime import RuntimeBar
from dusty.strategy_ir import ExitPlan, GroupMode, RuleGroup, StrategySpecV2


UTC = timezone.utc
START = datetime(2026, 1, 1, tzinfo=UTC)


def spec(*clauses: Clause, sessions: tuple[str, ...] = ()) -> StrategySpecV2:
    return StrategySpecV2(
        strategy_id="semantic-test",
        direction=TradeSide.LONG,
        entry_groups=(RuleGroup(tuple(clauses), GroupMode.ALL),),
        exit_plan=ExitPlan("pct:0.01", "pct:0.02", "off", "off", 2),
        decision_timeframe_minutes=15,
        intended_horizon_minutes=60,
        session_filters=sessions,
    )


def bar(index: int, *, session: str = "ASIA", **features: float) -> RuntimeBar:
    price = 1.1 + index * 0.0001
    values = {"close": price, **features}
    return RuntimeBar.of(
        START + timedelta(minutes=15 * index),
        open=price,
        high=price + 0.0002,
        low=price - 0.0002,
        close=price,
        features=values,
        session=session,
        execution_price=price,
    )


class ReconstructionSemanticTests(unittest.TestCase):
    def test_detects_price_indicator_period_confusion_from_observed_domain(self) -> None:
        candidate = spec(
            Clause("return_1", RuleOp.GT, 0.01),
            Clause("rsi", RuleOp.LT, 30.0),
            Clause("sma", RuleOp.GT, 200.0),
        )
        rows = tuple(
            bar(i, return_1=0.02 if i == 3 else 0.001, rsi=25.0 if i >= 2 else 40.0, sma=1.10 + i * 0.0001)
            for i in range(8)
        )
        result = assess_reconstruction_semantics(candidate, rows)
        self.assertEqual(result.status, "dead_reconstruction")
        self.assertEqual(result.entry_match_count, 0)
        sma = next(row for row in result.clauses if row.feature == "sma")
        self.assertTrue(sma.dead)
        self.assertLess(sma.observed_max or 999.0, 2.0)
        self.assertIn((0, 2), dead_hypothesis_clause_coordinates(result))

    def test_activatable_candidate_is_not_rejected(self) -> None:
        candidate = spec(Clause("rsi", RuleOp.LT, 30.0))
        rows = (bar(0, rsi=40.0), bar(1, rsi=25.0), bar(2, rsi=20.0))
        result = assess_reconstruction_semantics(candidate, rows)
        self.assertTrue(result.valid)
        self.assertEqual(result.entry_match_count, 2)
        self.assertEqual(dead_hypothesis_clause_coordinates(result), ())

    def test_session_filter_is_respected(self) -> None:
        candidate = spec(Clause("rsi", RuleOp.LT, 30.0), sessions=("ASIA",))
        rows = (bar(0, session="LONDON", rsi=20.0), bar(1, session="ASIA", rsi=40.0))
        result = assess_reconstruction_semantics(candidate, rows)
        self.assertEqual(result.session_eligible_rows, 1)
        self.assertEqual(result.status, "dead_reconstruction")

    def test_event_filtered_candidate_fails_closed_without_event_binding(self) -> None:
        base = spec(Clause("rsi", RuleOp.LT, 30.0))
        candidate = StrategySpecV2(
            strategy_id=base.strategy_id,
            direction=base.direction,
            entry_groups=base.entry_groups,
            exit_plan=base.exit_plan,
            decision_timeframe_minutes=base.decision_timeframe_minutes,
            intended_horizon_minutes=base.intended_horizon_minutes,
            event_exclusion_minutes=15,
        )
        with self.assertRaisesRegex(ValueError, "eventless"):
            assess_reconstruction_semantics(candidate, (bar(0, rsi=20.0),))

    def test_missing_feature_is_not_mislabeled_as_dead_clause(self) -> None:
        candidate = spec(Clause("rsi", RuleOp.LT, 30.0), Clause("sma", RuleOp.GT, 1.0))
        rows = (bar(0, rsi=20.0),)
        result = assess_reconstruction_semantics(candidate, rows)
        sma = next(row for row in result.clauses if row.feature == "sma")
        self.assertEqual(sma.available_count, 0)
        self.assertFalse(sma.dead)
        self.assertEqual(result.status, "dead_reconstruction")


if __name__ == "__main__":
    unittest.main()
