from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from dusty.forecasting import Forecast, RealizedTarget, forecast_evidence, score_forecasts


T0 = datetime(2026, 8, 30, 13, 0, tzinfo=timezone.utc)


class ForecastTournamentTests(unittest.TestCase):
    def test_m22_forecast_models_compete_through_one_contract(self):
        forecasts = []
        realized = []
        for i in range(4):
            at = T0 + timedelta(minutes=i)
            realized.append(RealizedTarget(at, 1, 100.0, 101.0 if i % 2 == 0 else 99.0))
            forecasts.extend(
                (
                    Forecast("baseline", at, 1, 100.0, 100.2),
                    Forecast(
                        "candidate",
                        at,
                        1,
                        100.0,
                        100.9 if i % 2 == 0 else 99.1,
                        98.5 if i % 2 else 100.5,
                        99.5 if i % 2 else 101.5,
                    ),
                )
            )
        scores = score_forecasts(forecasts, realized)
        self.assertEqual([score.provider for score in scores], ["candidate", "baseline"])
        self.assertEqual(scores[0].directional_accuracy, 1.0)
        self.assertEqual(scores[0].interval_coverage, 1.0)

    def test_forecast_becomes_evidence_not_execution(self):
        forecast = Forecast("kronos", T0, 12, 1.1, 1.111, 1.09, 1.12)
        evidence = forecast_evidence(forecast, "EURUSD")
        self.assertEqual(len(evidence), 4)
        self.assertTrue(all(item.category == "forecast" for item in evidence))
        self.assertTrue(all(item.source == "kronos" for item in evidence))
        self.assertAlmostEqual(forecast.predicted_return, 0.01)

    def test_mismatched_origin_fails_loudly(self):
        forecast = Forecast("candidate", T0, 1, 100.0, 101.0)
        target = RealizedTarget(T0, 1, 99.0, 100.0)
        with self.assertRaises(ValueError):
            score_forecasts((forecast,), (target,))


if __name__ == "__main__":
    unittest.main()
