from __future__ import annotations

import unittest

from dusty.tail_risk import TailRiskPolicy, TailRiskStatus, analyze_tail_risk


class M172TailRiskAnalyzerTests(unittest.TestCase):
    def test_sparse_path_is_insufficient(self) -> None:
        report = analyze_tail_risk((0.01, -0.02, 0.03), policy=TailRiskPolicy(minimum_observations=10))
        self.assertEqual(report.status, TailRiskStatus.INSUFFICIENT)
        self.assertIsNone(report.max_drawdown)
        self.assertFalse(report.broker_write_authority)

    def test_measured_tail_risk_keeps_drawdown_cvar_and_loss_streak(self) -> None:
        returns = (
            0.01, 0.02, -0.01, -0.02, -0.03, 0.01, 0.02, -0.04, 0.01, 0.01,
            -0.02, 0.03, 0.01, -0.01, 0.02, 0.01, -0.05, -0.02, 0.04, 0.01,
            0.02, -0.01, 0.01, 0.02, -0.03, 0.01, 0.01, -0.02, 0.02, 0.01,
        )
        report = analyze_tail_risk(returns)
        self.assertEqual(report.status, TailRiskStatus.MEASURED)
        self.assertGreater(report.max_drawdown, 0.0)
        self.assertGreaterEqual(report.conditional_value_at_risk, report.value_at_risk)
        self.assertEqual(report.worst_single_return, -0.05)
        self.assertEqual(report.max_consecutive_losses, 3)

    def test_compounding_is_path_based_not_arithmetic_sum(self) -> None:
        returns = tuple([0.10, -0.10] * 15)
        report = analyze_tail_risk(returns)
        self.assertAlmostEqual(report.terminal_compound_return, (1.10 * 0.90) ** 15 - 1.0)
        self.assertNotAlmostEqual(report.terminal_compound_return, sum(returns))

    def test_catastrophic_minus_one_or_nonfinite_return_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            analyze_tail_risk(tuple([0.01] * 29 + [-1.0]))
        with self.assertRaises(ValueError):
            analyze_tail_risk(tuple([0.01] * 29 + [float("nan")]))

    def test_tail_confidence_changes_var_identity(self) -> None:
        returns = tuple(-0.001 * (index + 1) for index in range(30))
        p90 = analyze_tail_risk(returns, policy=TailRiskPolicy(confidence=0.90))
        p99 = analyze_tail_risk(returns, policy=TailRiskPolicy(confidence=0.99))
        self.assertGreater(p99.value_at_risk, p90.value_at_risk)
        self.assertNotEqual(p90.fingerprint, p99.fingerprint)


if __name__ == "__main__":
    unittest.main()
