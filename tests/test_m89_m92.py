from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.experience import TradeSide
from dusty.forecast_council import (
    ForecastCouncilRequest,
    ForecastTradeAction,
    ensemble_forecasts,
    reason_about_forecast,
)
from dusty.forecast_dataset import ForecastMarketBar, build_rolling_examples
from dusty.forecast_evaluation import (
    ChallengerEvidence,
    ForecastScorecard,
    assess_challenger,
    purged_walk_forward_splits,
    score_forecasts,
)
from dusty.forecast_hypothesis import ForecastHypothesis, ParameterBound, refine_hypothesis
from dusty.forecasting_v2 import (
    ForecastKey,
    ForecastModelIdentity,
    ForecastRealization,
    ForecastTargetKind,
    ProbabilisticForecast,
    QuantilePoint,
)
from dusty.market_clock import MarketClockAssessment, MarketClockState
from dusty.research import Clause, RuleOp
from dusty.strategy_ir import ExitPlan, RuleGroup, StrategySpecV2


UTC = timezone.utc
NOW = datetime(2026, 9, 1, 12, tzinfo=UTC)


def model(name: str = "candidate") -> ForecastModelIdentity:
    return ForecastModelIdentity("dusty", name, "1", ("a" if name == "candidate" else "d") * 64, "b" * 64)


def forecast(*, name: str = "candidate", issued: datetime = NOW, horizon: int = 4, probability: float = 0.70) -> ProbabilisticForecast:
    return ProbabilisticForecast(
        model(name),
        ForecastKey("EURUSD", "M15", issued, issued, horizon, ForecastTargetKind.RETURN, "trend"),
        1.1,
        (QuantilePoint(0.1, -0.001), QuantilePoint(0.5, 0.003), QuantilePoint(0.9, 0.007)),
        probability,
        issued,
        issued + timedelta(hours=2),
        "c" * 64,
    )


def score(name: str = "candidate", *, count: int = 100, calibration: float = 0.04, crps: float = 0.002) -> ForecastScorecard:
    return ForecastScorecard(model(name).fingerprint, "EURUSD", "M15", 4, "trend", count, 0.001, crps / 2, crps, 0.1, 0.65, calibration)


def strategy(side: TradeSide = TradeSide.LONG) -> StrategySpecV2:
    return StrategySpecV2(
        "forecast-trend",
        side,
        (RuleGroup((Clause("ema_fast", RuleOp.GT, 1.0),)),),
        ExitPlan("atr_stop", "rr_target", max_hold_steps=4),
        15,
        60,
    )


def clock(*, opened: bool = True) -> MarketClockAssessment:
    return MarketClockAssessment(
        MarketClockState.OPEN if opened else MarketClockState.SCHEDULED_CLOSED,
        True,
        opened,
        opened,
        True,
        True,
        None if opened else NOW + timedelta(hours=12),
        ("broker_trade_session_open" if opened else "scheduled_market_closure",),
    )


class M89ProbabilisticEvaluationTests(unittest.TestCase):
    def test_scoring_uses_distribution_calibration_and_direction(self):
        predictions = tuple(forecast(issued=NOW + timedelta(hours=index)) for index in range(3))
        actuals = tuple(
            ForecastRealization("EURUSD", "M15", row.key.issued_at, 4, ForecastTargetKind.RETURN, row.key.issued_at + timedelta(hours=1), 0.004, 1.1, "trend")
            for row in predictions
        )
        result = score_forecasts(predictions, actuals)
        self.assertEqual(result.count, 3)
        self.assertEqual(result.direction_accuracy, 1.0)
        self.assertGreater(result.crps_approximation, 0.0)

    def test_scoring_rejects_realization_horizon_mismatch(self):
        prediction = forecast()
        realization = ForecastRealization("EURUSD", "M15", NOW, 2, ForecastTargetKind.RETURN, NOW + timedelta(hours=1), 0.004, 1.1, "trend")
        with self.assertRaisesRegex(ValueError, "do not match"):
            score_forecasts((prediction,), (realization,))


class M90ForecastCouncilTests(unittest.TestCase):
    def request(self, **changes: object) -> ForecastCouncilRequest:
        values = dict(
            strategy=strategy(),
            strategy_setup_present=True,
            forecasts=(forecast(),),
            scorecards=(score(),),
            market_clock=clock(),
            reasoning_at=NOW,
            estimated_round_trip_cost_fraction=0.0005,
            risk_allowed=True,
        )
        values.update(changes)
        return ForecastCouncilRequest(**values)

    def test_calibrated_forecast_may_confirm_existing_setup(self):
        decision = reason_about_forecast(self.request())
        self.assertEqual(decision.action, ForecastTradeAction.ENTER_LONG)
        self.assertGreater(decision.net_edge_fraction, 0)

    def test_forecast_cannot_manufacture_strategy_setup(self):
        decision = reason_about_forecast(self.request(strategy_setup_present=False))
        self.assertEqual(decision.action, ForecastTradeAction.WAIT)
        self.assertIn("frozen_strategy_setup_absent", decision.analyst_reasons)

    def test_market_closure_causes_normal_wait(self):
        decision = reason_about_forecast(self.request(market_clock=clock(opened=False)))
        self.assertEqual(decision.action, ForecastTradeAction.WAIT)
        self.assertIn("market_clock:scheduled_closed", decision.patience_reasons)

    def test_miscalibrated_model_has_no_entry_authority(self):
        decision = reason_about_forecast(self.request(scorecards=(score(calibration=0.30),)))
        self.assertEqual(decision.action, ForecastTradeAction.WAIT)
        self.assertTrue(any("miscalibrated" in reason for reason in decision.skeptic_reasons))

    def test_ensemble_refuses_mixed_horizons(self):
        with self.assertRaisesRegex(ValueError, "cannot mix"):
            ensemble_forecasts((forecast(), forecast(name="other", horizon=8)), {model().fingerprint: 1, model("other").fingerprint: 1})

    def test_council_refuses_to_average_different_horizons(self):
        other_forecast = forecast(name="other", horizon=8)
        other_score = ForecastScorecard(model("other").fingerprint, "EURUSD", "M15", 8, "trend", 100, 0.001, 0.001, 0.002, 0.1, 0.65, 0.04)
        decision = reason_about_forecast(self.request(forecasts=(forecast(), other_forecast), scorecards=(score(), other_score)))
        self.assertEqual(decision.action, ForecastTradeAction.WAIT)
        self.assertIn("forecast_consensus_identity_mismatch", decision.skeptic_reasons)


class M91HypothesisTests(unittest.TestCase):
    def hypothesis(self) -> ForecastHypothesis:
        return ForecastHypothesis(
            "h1",
            None,
            "a" * 64,
            "b" * 64,
            (("lookback", 64.0),),
            (ParameterBound("lookback", 32, 128),),
            "test whether a longer context improves calibrated returns",
            ("github:kronos",),
        )

    def test_refinement_is_new_bounded_challenger(self):
        parent = self.hypothesis()
        child = refine_hypothesis(parent, challenger_id="h2", changes={"lookback": 96}, rationale="neighbor test")
        self.assertEqual(child.parent_fingerprint, parent.fingerprint)
        self.assertNotEqual(child.fingerprint, parent.fingerprint)
        self.assertEqual(dict(parent.parameters)["lookback"], 64)

    def test_out_of_range_refinement_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside declared bound"):
            refine_hypothesis(self.hypothesis(), challenger_id="h2", changes={"lookback": 256}, rationale="unsafe")


class M92WalkForwardTournamentTests(unittest.TestCase):
    def examples(self):
        bars = tuple(
            ForecastMarketBar(
                "EURUSD", "M15", NOW + timedelta(minutes=15 * index), NOW + timedelta(minutes=15 * (index + 1)),
                1 + index / 1000, 1.002 + index / 1000, 0.999 + index / 1000, 1.001 + index / 1000,
            )
            for index in range(30)
        )
        return build_rolling_examples(bars, lookback=3, horizon_steps=2)

    def test_walk_forward_training_targets_are_known_before_test(self):
        splits = purged_walk_forward_splits(self.examples(), minimum_train_size=5, test_size=4, embargo=timedelta(minutes=15))
        self.assertTrue(splits)
        for split in splits:
            self.assertTrue(all(row.target_known_at <= split.train_cutoff for row in split.train))

    def test_candidate_cannot_promote_without_native_parity(self):
        candidate = score(crps=0.001)
        baseline = score("baseline", crps=0.002)
        decision = assess_challenger(ChallengerEvidence(candidate, baseline, True, True, False))
        self.assertFalse(decision.promoted)
        self.assertIn("native_mt5_parity_missing", decision.reasons)


if __name__ == "__main__":
    unittest.main()
