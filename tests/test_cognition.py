from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.cognition import EntryCognitionRequest, derive_entry_cognition
from dusty.core import (
    AnalystState,
    CoherenceResult,
    CoherenceState,
    Decision,
    GuardianState,
    Person,
    SkepticState,
)
from dusty.experience import TradeSide
from dusty.forecasting import Forecast
from dusty.research import Clause, RuleOp
from dusty.risk import RiskAssessment, RiskState
from dusty.runtime import compile_strategy
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2


UTC = timezone.utc


class CognitionTests(unittest.TestCase):
    def strategy(self):
        return compile_strategy(
            StrategySpecV2(
                strategy_id="trend-1",
                direction=TradeSide.LONG,
                entry_groups=(
                    RuleGroup(
                        (
                            Clause("close", RuleOp.GT, 1.0),
                            Clause("ema_20", RuleOp.GT, 1.0),
                        )
                    ),
                ),
                exit_plan=ExitPlan("pct:0.01", target_rule="rr:2", max_hold_steps=8),
                decision_timeframe_minutes=15,
                intended_horizon_minutes=120,
            )
        )

    def normal_risk(self):
        return RiskAssessment(True, RiskState.NORMAL, 1.0, ())

    def test_roles_are_derived_and_produce_entry(self) -> None:
        request = EntryCognitionRequest.of(
            strategy=self.strategy(),
            features={"close": 1.2, "ema_20": 1.1},
            coherence=CoherenceResult(CoherenceState.COHERENT),
            risk=self.normal_risk(),
            session="LONDON",
            spread_points=12,
        )
        assessment = derive_entry_cognition(request)
        self.assertEqual(assessment.cognition.analyst, AnalystState.LONG)
        self.assertEqual(assessment.cognition.skeptic, SkepticState.CLEAR)
        self.assertEqual(assessment.cognition.guardian, GuardianState.NORMAL)
        person = Person("p", "EURUSD", "trend-1")
        self.assertEqual(
            person.reason(assessment.cognition, request.coherence),
            Decision.ENTRY_LONG,
        )
        self.assertIn("entry_rules_met", assessment.reasons_for("analyst"))

    def test_forecast_cannot_create_entry_and_can_challenge_existing_setup(self) -> None:
        at = datetime(2026, 8, 31, tzinfo=UTC)
        bearish = Forecast("challenger", at, 4, 1.2, 1.15)
        no_setup = EntryCognitionRequest.of(
            strategy=self.strategy(),
            features={"close": 0.9, "ema_20": 1.1},
            coherence=CoherenceResult(CoherenceState.COHERENT),
            risk=self.normal_risk(),
            forecasts=(bearish,),
            reasoning_at=at,
        )
        self.assertEqual(
            derive_entry_cognition(no_setup).cognition.analyst,
            AnalystState.NEUTRAL,
        )
        setup = EntryCognitionRequest.of(
            strategy=self.strategy(),
            features={"close": 1.2, "ema_20": 1.1},
            coherence=CoherenceResult(CoherenceState.COHERENT),
            risk=self.normal_risk(),
            forecasts=(bearish,),
            reasoning_at=at,
        )
        challenged = derive_entry_cognition(setup)
        self.assertEqual(challenged.cognition.analyst, AnalystState.UNCLEAR)
        self.assertEqual(challenged.cognition.skeptic, SkepticState.CONCERN)

    def test_forecast_assisted_cognition_requires_explicit_reasoning_time(self) -> None:
        at = datetime(2026, 8, 31, tzinfo=UTC)
        forecast = Forecast("challenger", at, 4, 1.2, 1.21)
        with self.assertRaisesRegex(ValueError, "explicit reasoning_at"):
            EntryCognitionRequest.of(
                strategy=self.strategy(),
                features={"close": 1.2, "ema_20": 1.1},
                coherence=CoherenceResult(CoherenceState.COHERENT),
                risk=self.normal_risk(),
                forecasts=(forecast,),
            )

    def test_future_forecast_is_rejected_before_cognition(self) -> None:
        at = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
        future = Forecast("challenger", at + timedelta(minutes=15), 4, 1.2, 1.21)
        with self.assertRaisesRegex(ValueError, "future_forecast_not_available"):
            EntryCognitionRequest.of(
                strategy=self.strategy(),
                features={"close": 1.2, "ema_20": 1.1},
                coherence=CoherenceResult(CoherenceState.COHERENT),
                risk=self.normal_risk(),
                forecasts=(future,),
                reasoning_at=at,
            )

    def test_risk_veto_becomes_guardian_stop(self) -> None:
        blocked = RiskAssessment(
            False,
            RiskState.RESEARCH_ONLY,
            0.0,
            ("account_state:research_only",),
        )
        request = EntryCognitionRequest.of(
            strategy=self.strategy(),
            features={"close": 1.2, "ema_20": 1.1},
            coherence=CoherenceResult(CoherenceState.COHERENT),
            risk=blocked,
        )
        assessment = derive_entry_cognition(request)
        self.assertEqual(assessment.cognition.guardian, GuardianState.STOP)
        self.assertEqual(
            Person("p", "EURUSD", "trend-1").reason(
                assessment.cognition,
                request.coherence,
            ),
            Decision.STAND_DOWN,
        )

    def test_event_exclusion_forces_wait(self) -> None:
        request = EntryCognitionRequest.of(
            strategy=self.strategy(),
            features={"close": 1.2, "ema_20": 1.1},
            coherence=CoherenceResult(CoherenceState.COHERENT),
            risk=self.normal_risk(),
            event_blocked=True,
        )
        assessment = derive_entry_cognition(request)
        self.assertEqual(assessment.cognition.skeptic, SkepticState.CONCERN)
        self.assertEqual(
            Person("p", "EURUSD", "trend-1").reason(
                assessment.cognition,
                request.coherence,
            ),
            Decision.WAIT,
        )

    def test_cognition_is_fingerprint_deterministic(self) -> None:
        request = EntryCognitionRequest.of(
            strategy=self.strategy(),
            features={"close": 1.2, "ema_20": 1.1},
            coherence=CoherenceResult(CoherenceState.COHERENT),
            risk=self.normal_risk(),
        )
        first = derive_entry_cognition(request)
        second = derive_entry_cognition(request)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
