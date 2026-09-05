from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
import unittest

from dusty.broker_deviation import BrokerDeviationAssessment, BrokerDeviationStatus
from dusty.demo_capital_allocator import DemoDeskCapitalState
from dusty.execution_lifecycle import ExecutionState
from dusty.forecast_demo import ForecastDeskEvidence
from dusty.independent_session_promotion import (
    IndependentDemoSessionEvidence,
    IndependentPromotionPolicy,
    IndependentPromotionStatus,
    assess_independent_session_promotion,
)


def fp(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


class M188IndependentSessionPromotionTests(unittest.TestCase):
    champion = fp("frozen-champion")

    def assessment(self, desk: int, index: int = 0, status: BrokerDeviationStatus = BrokerDeviationStatus.WITHIN_POLICY) -> BrokerDeviationAssessment:
        return BrokerDeviationAssessment(
            status,
            fp(f"intent-{desk}-{index}"),
            fp(f"shadow-{desk}-{index}"),
            fp(f"comparison-{desk}-{index}"),
            ExecutionState.FILLED if status is not BrokerDeviationStatus.BROKER_FAILURE else ExecutionState.REJECTED,
            10009 if status is not BrokerDeviationStatus.BROKER_FAILURE else 10006,
            1000 + desk,
            2000 + desk * 10 + index,
            1.0 if status is not BrokerDeviationStatus.INCOMPLETE else 0.0,
            0.0001 if status is not BrokerDeviationStatus.INCOMPLETE else None,
            100.0 if status is not BrokerDeviationStatus.INCOMPLETE else None,
            120.0 if status is not BrokerDeviationStatus.INCOMPLETE else None,
            ("observed_execution_within_explicit_policy",) if status is BrokerDeviationStatus.WITHIN_POLICY else (status.value,),
            (fp(f"evidence-{desk}-{index}"),),
            fp("broker-policy"),
        )

    def session(
        self,
        desk: int,
        *,
        champion: str | None = None,
        completed: int = 40,
        calibration: float = 0.05,
        pnl: float = 100.0,
        drawdown: float = 0.04,
        clock_faults: int = 0,
        rule_violations: int = 0,
        assessment_status: BrokerDeviationStatus = BrokerDeviationStatus.WITHIN_POLICY,
        assessment_count: int = 1,
        session_fingerprint: str | None = None,
    ) -> IndependentDemoSessionEvidence:
        session_fp = session_fingerprint or fp(f"session-{desk}")
        forecast = ForecastDeskEvidence(
            f"desk-{desk}",
            champion or self.champion,
            session_fp,
            completed,
            calibration,
            pnl,
            drawdown,
            clock_faults,
            5,
        )
        capital = DemoDeskCapitalState.fresh(f"desk-{desk}", "generation-1", session_fp)
        assessments = tuple(self.assessment(desk, index, assessment_status) for index in range(assessment_count))
        shadows = tuple(row.shadow_fingerprint for row in assessments)
        return IndependentDemoSessionEvidence(forecast, capital, shadows, assessments, rule_violations)

    def six(self):
        return tuple(self.session(index) for index in range(6))

    def test_six_clean_independent_sessions_are_research_eligible_only(self) -> None:
        first = assess_independent_session_promotion(self.six())
        second = assess_independent_session_promotion(self.six())
        self.assertEqual(first.status, IndependentPromotionStatus.RESEARCH_PROMOTION_ELIGIBLE)
        self.assertEqual(first.passing_sessions, 6)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertFalse(first.live_write_authority)
        self.assertFalse(first.champion_mutation_authority)
        self.assertFalse(first.risk_override_authority)
        self.assertFalse(first.guardian_override_authority)

    def test_five_clean_sessions_are_insufficient_not_rejected(self) -> None:
        result = assess_independent_session_promotion(self.six()[:5])
        self.assertEqual(result.status, IndependentPromotionStatus.INSUFFICIENT)
        self.assertIn("independent_session_count_below_policy", result.blockers)
        self.assertIn("passing_independent_sessions_below_policy", result.blockers)

    def test_duplicate_desk_or_session_cannot_be_counted_twice(self) -> None:
        rows = list(self.six())
        rows[5] = self.session(0, session_fingerprint=fp("different-session-same-desk"))
        with self.assertRaisesRegex(ValueError, "same desk twice"):
            assess_independent_session_promotion(rows)
        rows = list(self.six())
        rows[5] = self.session(5, session_fingerprint=rows[0].forecast.session_fingerprint)
        with self.assertRaisesRegex(ValueError, "same session twice"):
            assess_independent_session_promotion(rows)

    def test_champion_drift_fails_closed_before_profit_is_considered(self) -> None:
        rows = list(self.six())
        rows[5] = self.session(5, champion=fp("different-champion"), pnl=10_000)
        with self.assertRaisesRegex(ValueError, "one frozen champion"):
            assess_independent_session_promotion(rows)

    def test_cross_session_shadow_reuse_is_not_independent_evidence(self) -> None:
        rows = list(self.six())
        duplicate = replace(
            rows[5].broker_assessments[0],
            shadow_fingerprint=rows[0].shadow_fingerprints[0],
        )
        rows[5] = IndependentDemoSessionEvidence(
            rows[5].forecast,
            rows[5].capital,
            (duplicate.shadow_fingerprint,),
            (duplicate,),
        )
        with self.assertRaisesRegex(ValueError, "cannot be reused"):
            assess_independent_session_promotion(rows)

    def test_forecast_and_capital_desk_or_session_drift_fails_closed(self) -> None:
        base = self.session(0)
        with self.assertRaisesRegex(ValueError, "desk identity drift"):
            IndependentDemoSessionEvidence(
                base.forecast,
                DemoDeskCapitalState.fresh("other-desk", "generation-1", base.capital.session_fingerprint),
                base.shadow_fingerprints,
                base.broker_assessments,
            )
        with self.assertRaisesRegex(ValueError, "session identity drift"):
            IndependentDemoSessionEvidence(
                base.forecast,
                DemoDeskCapitalState.fresh(base.capital.desk_id, "generation-1", fp("other-session")),
                base.shadow_fingerprints,
                base.broker_assessments,
            )

    def test_m187_assessments_must_exactly_cover_session_shadows(self) -> None:
        base = self.session(0)
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            IndependentDemoSessionEvidence(
                base.forecast,
                base.capital,
                (fp("unmatched-shadow"),),
                base.broker_assessments,
            )

    def test_profitable_session_with_broker_deviation_is_rejected(self) -> None:
        rows = list(self.six())
        rows[2] = self.session(2, pnl=5_000, assessment_status=BrokerDeviationStatus.DEVIATED)
        result = assess_independent_session_promotion(rows)
        self.assertEqual(result.status, IndependentPromotionStatus.REJECTED)
        self.assertIn("desk:desk-2:broker_execution_deviated", result.blockers)

    def test_incomplete_broker_history_is_insufficient_not_silently_passed(self) -> None:
        rows = list(self.six())
        rows[2] = self.session(2, assessment_status=BrokerDeviationStatus.INCOMPLETE)
        result = assess_independent_session_promotion(rows)
        self.assertEqual(result.status, IndependentPromotionStatus.INSUFFICIENT)
        self.assertIn("desk:desk-2:broker_execution_evidence_incomplete", result.blockers)

    def test_rule_violation_or_unexpected_clock_fault_rejects_winning_session(self) -> None:
        rows = list(self.six())
        rows[1] = self.session(1, pnl=9_000, rule_violations=1)
        result = assess_independent_session_promotion(rows)
        self.assertEqual(result.status, IndependentPromotionStatus.REJECTED)
        self.assertIn("desk:desk-1:rule_violation", result.blockers)
        rows = list(self.six())
        rows[1] = self.session(1, pnl=9_000, clock_faults=1)
        result = assess_independent_session_promotion(rows)
        self.assertEqual(result.status, IndependentPromotionStatus.REJECTED)
        self.assertIn("desk:desk-1:unexpected_clock_fault", result.blockers)

    def test_profit_cannot_hide_calibration_drawdown_or_negative_after_costs(self) -> None:
        rows = list(self.six())
        rows[0] = self.session(0, calibration=0.2, pnl=1_000)
        rows[1] = self.session(1, drawdown=0.2, pnl=1_000)
        rows[2] = self.session(2, pnl=-1.0)
        result = assess_independent_session_promotion(rows)
        self.assertEqual(result.status, IndependentPromotionStatus.REJECTED)
        self.assertIn("desk:desk-0:miscalibrated", result.blockers)
        self.assertIn("desk:desk-1:drawdown_exceeded", result.blockers)
        self.assertIn("desk:desk-2:not_profitable_after_costs", result.blockers)

    def test_sparse_forecast_or_execution_evidence_is_insufficient(self) -> None:
        rows = list(self.six())
        rows[3] = self.session(3, completed=10)
        result = assess_independent_session_promotion(rows)
        self.assertEqual(result.status, IndependentPromotionStatus.INSUFFICIENT)
        self.assertIn("desk:desk-3:insufficient_forecasts", result.blockers)
        policy = IndependentPromotionPolicy(minimum_execution_observations_per_session=2)
        result = assess_independent_session_promotion(self.six(), policy=policy)
        self.assertEqual(result.status, IndependentPromotionStatus.INSUFFICIENT)
        self.assertIn("desk:desk-0:insufficient_broker_execution_observations", result.blockers)

    def test_explicit_policy_can_require_more_than_six_sessions(self) -> None:
        result = assess_independent_session_promotion(
            self.six(),
            policy=IndependentPromotionPolicy(required_sessions=7),
        )
        self.assertEqual(result.status, IndependentPromotionStatus.INSUFFICIENT)
        self.assertIn("independent_session_count_below_policy", result.blockers)

    def test_session_evidence_and_assessment_never_gain_live_authority(self) -> None:
        session = self.session(0)
        self.assertFalse(session.live_write_authority)
        result = assess_independent_session_promotion(self.six())
        self.assertFalse(result.live_write_authority)


if __name__ == "__main__":
    unittest.main()
