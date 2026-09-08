from __future__ import annotations

import unittest

from dusty.m165_runtime_execution import build_runtime_execution_envelope
from dusty.risk import RiskConstitution


class M165RuntimeExecutionEnvelopeTests(unittest.TestCase):
    def test_rebases_stop_and_bounds_quote_tolerance_without_authority(self) -> None:
        envelope = build_runtime_execution_envelope(
            symbol="EURUSD",
            volume_lots=0.01,
            tick_size=0.00001,
            planned_reference_price=1.10000,
            planned_stop_price=1.09999,
            current_reference_price=1.10100,
            current_equity=100000.0,
            worst_case_loss_cash=0.04,
            discovery_loss_ceiling_cash=250.0,
            quote_tolerance_ticks=3,
        )
        self.assertAlmostEqual(envelope.stop_distance, 0.00001)
        self.assertAlmostEqual(envelope.stop_price, 1.10099)
        self.assertAlmostEqual(envelope.worst_case_reference_price, 1.10103)
        self.assertAlmostEqual(envelope.maximum_execution_loss_cash, 0.04)
        self.assertLess(envelope.actual_risk_fraction, RiskConstitution().normal_trade_risk)
        self.assertFalse(envelope.broker_write_authority)
        self.assertFalse(envelope.live_write_authority)
        self.assertFalse(envelope.promotion_authority)
        self.assertFalse(envelope.retry_authority)

    def test_runtime_envelope_never_exceeds_current_or_discovery_risk_ceiling(self) -> None:
        with self.assertRaises(ValueError):
            build_runtime_execution_envelope(
                symbol="EURUSD",
                volume_lots=0.01,
                tick_size=0.00001,
                planned_reference_price=1.10000,
                planned_stop_price=1.09999,
                current_reference_price=1.10100,
                current_equity=1000.0,
                worst_case_loss_cash=3.0,
                discovery_loss_ceiling_cash=250.0,
                quote_tolerance_ticks=3,
            )
        with self.assertRaises(ValueError):
            build_runtime_execution_envelope(
                symbol="EURUSD",
                volume_lots=0.01,
                tick_size=0.00001,
                planned_reference_price=1.10000,
                planned_stop_price=1.09999,
                current_reference_price=1.10100,
                current_equity=100000.0,
                worst_case_loss_cash=0.04,
                discovery_loss_ceiling_cash=0.03,
                quote_tolerance_ticks=3,
            )

    def test_invalid_native_geometry_or_tolerance_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            build_runtime_execution_envelope(
                symbol="EURUSD",
                volume_lots=0.01,
                tick_size=0.00001,
                planned_reference_price=1.10000,
                planned_stop_price=1.099995,
                current_reference_price=1.10100,
                current_equity=100000.0,
                worst_case_loss_cash=0.04,
                discovery_loss_ceiling_cash=250.0,
            )
        with self.assertRaises(ValueError):
            build_runtime_execution_envelope(
                symbol="EURUSD",
                volume_lots=0.01,
                tick_size=0.00001,
                planned_reference_price=1.10000,
                planned_stop_price=1.09999,
                current_reference_price=1.10100,
                current_equity=100000.0,
                worst_case_loss_cash=0.04,
                discovery_loss_ceiling_cash=250.0,
                quote_tolerance_ticks=9,
            )


if __name__ == "__main__":
    unittest.main()
